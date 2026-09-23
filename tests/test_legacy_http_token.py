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
