"""The device-token callback listener for the 8888 bridge (issue #168)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from custom_components.localthings import legacy_http_token
from custom_components.localthings.legacy_http_token import (
    CallbackPortUnavailable,
    TokenListener,
    _read_request,
)


class _Chunks:
    """A connection that delivers its bytes in the given pieces."""

    def __init__(self, *chunks: bytes):
        self._chunks = list(chunks)

    def recv(self, _size):
        return self._chunks.pop(0) if self._chunks else b""


def test_a_body_that_arrives_after_the_headers_is_read_whole():
    head = b"POST / HTTP/1.1\r\nContent-Length: 27\r\n\r\n"
    body = b'{"DeviceToken": "tok12345"}'

    data = _read_request(_Chunks(head, body[:10], body[10:]))

    assert data.endswith(body)


def test_a_request_without_a_length_stops_at_the_headers():
    data = _read_request(_Chunks(b"GET / HTTP/1.1\r\n\r\n", b"never read"))

    assert data == b"GET / HTTP/1.1\r\n\r\n"


def test_a_port_that_cannot_be_bound_is_its_own_error(monkeypatch):
    monkeypatch.setattr(legacy_http_token, "server_context", lambda cert, key: object())

    class _Busy:
        def __init__(self, *args):
            self.closed = False

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            raise OSError(98, "Address already in use")

        def close(self):
            self.closed = True

    monkeypatch.setattr(legacy_http_token.socket, "socket", _Busy)
    listener = TokenListener("C", "K", peer="10.0.0.7")

    with pytest.raises(CallbackPortUnavailable):
        listener.start()


def test_a_callback_from_anything_but_the_appliance_is_ignored(monkeypatch):
    monkeypatch.setattr(legacy_http_token, "server_context", lambda cert, key: object())
    handled: list[str] = []
    listener = TokenListener("C", "K", peer="10.0.0.7")
    monkeypatch.setattr(listener, "_handle", lambda raw, peer: handled.append(peer))

    class _Conn:
        def close(self):
            pass

    class _Server:
        def __init__(self):
            self._peers = ["10.0.0.66", "10.0.0.7"]

        def accept(self):
            if not self._peers:
                listener._stop.set()
                raise TimeoutError
            return _Conn(), (self._peers.pop(0), 5555)

    listener._socket = cast(Any, _Server())
    listener._serve()

    assert handled == ["10.0.0.7"]


def test_a_refused_handshake_from_the_appliance_is_recorded(monkeypatch):
    """What tells the flow to offer an AC14K_M-signed leaf (#524)."""
    import ssl

    class _Context:
        def wrap_socket(self, raw, server_side):
            raise ssl.SSLError("tlsv1 alert unknown ca")

    monkeypatch.setattr(legacy_http_token, "server_context", lambda cert, key: _Context())
    listener = TokenListener("C", "K", peer="10.0.0.7")

    class _Raw:
        def settimeout(self, _timeout):
            pass

    listener._handle(cast(Any, _Raw()), "10.0.0.7")

    assert listener.handshake_failed
    assert listener.token is None


def test_a_refused_handshake_ends_the_exchange_early(monkeypatch):
    """The same certificate will be refused again, so there is no point in
    waiting out the remaining ninety seconds."""
    from custom_components.localthings.legacy_http_token import CallbackCertRejected

    class _Listener:
        token = None
        handshake_failed = False

        def __init__(self, cert, key, peer):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    requests: list[str] = []

    def _request(host, port, address, context):
        requests.append(host)
        _Listener.handshake_failed = True
        return 200

    monkeypatch.setattr(legacy_http_token, "TokenListener", _Listener)
    monkeypatch.setattr(legacy_http_token, "client_context", lambda cert, key: object())
    monkeypatch.setattr(legacy_http_token, "request_token", _request)
    monkeypatch.setattr(legacy_http_token.socket, "gethostbyname", lambda host: host)
    monkeypatch.setattr(legacy_http_token.time, "sleep", lambda _s: None)

    with pytest.raises(CallbackCertRejected):
        legacy_http_token.obtain_device_token("10.0.0.7", 8888, "C", "K", listen_address="10.0.0.2")

    assert requests == ["10.0.0.7"]
