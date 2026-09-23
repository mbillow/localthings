"""Device-token bootstrap for the 8888 bridge (issue #168).

This family authorizes with a device token the appliance issues, not with
the certificate. The exchange is a callback: we ask on 8888, the appliance
POSTs the token to an HTTPS listener here on 8889. Two things decide whether
it arrives:

* The appliance takes the callback address from the request's `Host`
  header, not its source address -- the default would make it call itself.
* A pending request blocks the next for about a minute (`403 "... until
  completing the process of a previous request"`), so retries are paced.

Blocking throughout; the config flow runs it in an executor.
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
_CONTENT_LENGTH_RE = re.compile(rb"^content-length:\s*(\d+)\s*$", re.IGNORECASE | re.MULTILINE)

# A callback is a small JSON body; anything past this is not one.
_MAX_CALLBACK_BYTES = 64 * 1024

# One request, then wait: the appliance refuses a second while the first is
# still pending, and it answers within a few seconds when it answers at all.
_RETRY_INTERVAL_S = 15.0
_ACCEPT_POLL_S = 1.0


class CallbackPortUnavailable(OSError):
    """Port 8889 could not be bound -- usually an earlier exchange still
    holding it for the rest of its wait."""


class TokenListener:
    """The HTTPS listener the appliance POSTs the token to. Start it before
    asking for a token, stop it when you have one.

    Accepts a callback only from `peer`, the appliance's own address: the
    listener is open on every interface for the length of the exchange, and
    a token posted by anything else would be stored as this entry's.
    """

    def __init__(self, cert_pem: str, key_pem: str, peer: str) -> None:
        self._context = server_context(cert_pem, key_pem)
        self._peer = peer
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._token: str | None = None

    @property
    def token(self) -> str | None:
        return self._token

    def start(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", CALLBACK_PORT))
            server.listen(5)
        except OSError as err:
            server.close()
            raise CallbackPortUnavailable(str(err)) from err
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
            if address[0] != self._peer:
                _LOGGER.debug("token callback from %s ignored; expected %s", address[0], self._peer)
                raw.close()
                continue
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
        data = _read_request(connection)
        match = _TOKEN_RE.search(data)
        if match:
            self._token = match.group(1).decode()
            _LOGGER.debug("device token received from %s", peer)
        connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        connection.close()


def _read_request(connection) -> bytes:
    """Headers and the whole body -- the token is in the JSON body, which
    need not arrive in the same segment as the headers."""
    data = b""
    while b"\r\n\r\n" not in data and len(data) < _MAX_CALLBACK_BYTES:
        chunk = connection.recv(4096)
        if not chunk:
            return data
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    match = _CONTENT_LENGTH_RE.search(head)
    length = min(int(match.group(1)), _MAX_CALLBACK_BYTES) if match else 0
    while len(body) < length:
        chunk = connection.recv(4096)
        if not chunk:
            break
        body += chunk
    return head + b"\r\n\r\n" + body


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
    listener = TokenListener(cert_pem, key_pem, peer=socket.gethostbyname(host))
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
