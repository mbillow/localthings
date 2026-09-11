"""Device-token bootstrap for the 8888 bridge (issue #168).

The CA step is unchanged from the DTLS path: the user supplies the CA, a
leaf is minted from it. This family wants one thing more, and it is the
only part of setup that has no counterpart on a CoAP device: a *device
token*, which cannot be minted locally because the appliance issues it.

The exchange is a callback. We ask for a token on port 8888; the appliance
then connects back and POSTs it to an HTTPS listener on port 8889. Two
things about that decide whether it ever arrives:

* **The appliance takes the callback address from the `Host` header**, not
  from the source address of the request. Python's default `Host` is the
  appliance's own address, so the default makes it call itself and nothing
  is ever delivered.
* **A request in flight blocks the next one.** While one is pending the
  appliance answers `403 "This request is not able to be processed until
  completing the process of a previous request"` for roughly a minute, so
  retries are paced rather than immediate.

Blocking throughout, like everything else behind the transport seam; the
config flow runs it in an executor.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import ssl
import threading
import time
from http.client import HTTPSConnection

from .legacy_http_tls import client_context, server_context

_LOGGER = logging.getLogger(__name__)

# Where the appliance calls back. Not configurable: the appliance derives
# the port from the Host header we send, but HA has to be reachable on it,
# and a second port to explain buys nothing.
CALLBACK_PORT = 8889

# What the appliance wants in the DeviceToken header of the request itself.
# Any placeholder is accepted; this is the one the vendor's own clients send.
_PLACEHOLDER_TOKEN = "xxxxxxxxxxx"

_TOKEN_RE = re.compile(rb'"DeviceToken"\s*:\s*"([^"]+)"')

# One request, then wait: the appliance refuses a second while the first is
# still pending, and it answers within a few seconds when it answers at all.
_RETRY_INTERVAL_S = 15.0
_ACCEPT_POLL_S = 1.0


class TokenListener:
    """The HTTPS listener the appliance POSTs the token to. Start it before
    asking for a token, stop it when you have one."""

    def __init__(self, cert_pem: str, key_pem: str) -> None:
        self._context = server_context(cert_pem, key_pem)
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._token: str | None = None

    @property
    def token(self) -> str | None:
        return self._token

    def start(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", CALLBACK_PORT))
        server.listen(5)
        server.settimeout(_ACCEPT_POLL_S)
        self._socket = server
        self._thread = threading.Thread(
            target=self._serve, daemon=True, name="localthings-token-callback"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def _serve(self) -> None:
        server = self._socket
        if server is None:
            return
        while not self._stop.is_set() and self._token is None:
            try:
                raw, address = server.accept()
            except TimeoutError:
                continue
            except OSError as err:
                _LOGGER.debug("token listener: %s", err)
                return
            try:
                self._handle(raw, address[0])
            except Exception as err:  # one bad callback must not kill the listener
                _LOGGER.debug("token callback from %s failed: %s", address[0], err)
            finally:
                raw.close()

    def _handle(self, raw: socket.socket, peer: str) -> None:
        raw.settimeout(10.0)
        try:
            connection = self._context.wrap_socket(raw, server_side=True)
        except ssl.SSLError as err:
            _LOGGER.debug("token callback TLS handshake from %s failed: %s", peer, err)
            return
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = connection.recv(4096)
            if not chunk:
                break
            data += chunk
        match = _TOKEN_RE.search(data)
        if match:
            self._token = match.group(1).decode()
            _LOGGER.debug("device token received from %s", peer)
        connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        connection.close()


def local_address_for(host: str) -> str:
    """This machine's address on the appliance's network.

    Asked of the routing table rather than of the hostname: a HA host with
    several interfaces (or a container with its own) resolves its own name
    to an address the appliance cannot reach, and the callback would be
    sent somewhere nothing is listening.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((host, 9))  # discard port; UDP connect sends nothing
        return probe.getsockname()[0]
    finally:
        probe.close()


def request_token(host: str, port: int, listen_address: str, context: ssl.SSLContext) -> int:
    """Ask the appliance to issue a token. Returns the HTTP status."""
    connection = HTTPSConnection(host, port, context=context, timeout=15)
    try:
        body = json.dumps({}).encode()
        connection.putrequest(
            "POST", "/devicetoken/request", skip_host=True, skip_accept_encoding=True
        )
        # The whole trick: this is where the appliance will call back.
        connection.putheader("Host", f"{listen_address}:{CALLBACK_PORT}")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("DeviceToken", _PLACEHOLDER_TOKEN)
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def obtain_device_token(
    host: str,
    port: int,
    cert_pem: str,
    key_pem: str,
    *,
    timeout: float = 90.0,
    listen_address: str | None = None,
) -> str | None:
    """Run the exchange; returns the token, or None if none arrived.

    None is a legitimate outcome rather than an error: the appliance has to
    be awake and reachable, and inbound 8889 has to reach this host. The
    config flow offers a pasted token as the fallback, so a user whose
    network makes the callback impossible is not locked out.
    """
    listener = TokenListener(cert_pem, key_pem)
    listener.start()
    try:
        address = listen_address or local_address_for(host)
        context = client_context(cert_pem, key_pem)
        deadline = time.monotonic() + timeout
        while listener.token is None and time.monotonic() < deadline:
            try:
                status = request_token(host, port, address, context)
                _LOGGER.debug("token request answered %s", status)
            except OSError as err:
                _LOGGER.debug("token request failed: %s", err)
            waited = 0.0
            while listener.token is None and waited < _RETRY_INTERVAL_S:
                if time.monotonic() >= deadline:
                    break
                time.sleep(_ACCEPT_POLL_S)
                waited += _ACCEPT_POLL_S
        return listener.token
    finally:
        listener.stop()
