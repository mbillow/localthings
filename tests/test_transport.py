"""Tests for DtlsTransport -- the CoAP-DTLS side of the transport seam.

The seam exists so a second transport can be added without a parallel code
path through the coordinator (issue #168), which means two things have to
hold here and are asserted directly rather than incidentally: `read` hands
back a decoded body, and `write` takes one. Everything else is forwarding.
"""

from __future__ import annotations

from typing import cast

import cbor2
import pytest

from custom_components.localthings.transport import DecodeError, DtlsTransport


class _FakeCoapSession:
    """Stand-in for smartthings_local's DtlsCoapSession: CBOR on the wire,
    path segments as lists, a `(code, payload)` pair back."""

    def __init__(self, host, port, cert_pem=None, key_pem=None, **kwargs):
        self.host, self.port, self.kwargs = host, port, kwargs
        self.cert_pem, self.key_pem = cert_pem, key_pem
        self.reader_started = False
        self.closed = False
        self.paced = 0
        self.posts: list[tuple[list[str], bytes]] = []
        self.subscribed: list[list[str]] = []
        self.refreshed: list = []
        self.payload = cbor2.dumps({"x.com.samsung.da.power": "On"})
        self.code = 0x45

    def connect(self):
        pass

    def start_reader(self):
        self.reader_started = True

    def get(self, path_segs, timeout=None):
        return self.code, self.payload

    def post(self, path_segs, payload, timeout=None):
        self.posts.append((path_segs, payload))
        return 0x44, b""

    def pace(self):
        self.paced += 1

    def subscribe(self, path_segs):
        self.subscribed.append(path_segs)
        return b"\x01"

    def refresh_observes(self, paths):
        self.refreshed.append(paths)

    def close(self):
        self.closed = True


def fake_of(transport: DtlsTransport) -> _FakeCoapSession:
    """The session a connected transport is holding. A helper rather than a
    cast at every use site: `_session` is typed as the real session, which
    is the point of the seam."""
    return cast(_FakeCoapSession, transport._session)


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr("custom_components.localthings.transport.DtlsCoapSession", _FakeCoapSession)
    t = DtlsTransport("10.0.0.9", 49154, cert_pem="CERT", key_pem="KEY", local_port=49700)
    t.connect()
    return t


class TestLifetime:
    def test_connect_starts_the_reader_thread(self, transport):
        """A session whose reader never started receives no OBSERVE
        notification, which used to be the coordinator's job to remember."""
        assert fake_of(transport).reader_started is True

    def test_optional_arguments_are_only_passed_when_set(self, monkeypatch):
        monkeypatch.setattr(
            "custom_components.localthings.transport.DtlsCoapSession", _FakeCoapSession
        )
        bare = DtlsTransport("10.0.0.9", 49154, cert_pem="CERT", key_pem="KEY")
        bare.connect()
        session = fake_of(bare)

        assert "local_port" not in session.kwargs
        assert "on_notification" not in session.kwargs

    def test_close_drops_the_session(self, transport):
        session = fake_of(transport)
        transport.close()

        assert session.closed is True
        assert transport._session is None

    def test_closing_twice_is_harmless(self, transport):
        transport.close()
        transport.close()

    def test_use_before_connect_is_an_error_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(
            "custom_components.localthings.transport.DtlsCoapSession", _FakeCoapSession
        )
        never = DtlsTransport("10.0.0.9", 49154, cert_pem="CERT", key_pem="KEY")

        with pytest.raises(RuntimeError, match="no session"):
            never.read(["oic", "p"], timeout=1.0)


class TestRead:
    def test_returns_the_decoded_body(self, transport):
        code, body = transport.read(["power", "vs", "0"], timeout=1.0)

        assert code == 0x45
        assert body == {"x.com.samsung.da.power": "On"}

    def test_an_empty_payload_is_no_body_rather_than_an_error(self, transport):
        """A 4.04 with nothing in it is the expected answer on every board
        that doesn't have the resource -- callers check the code."""
        fake_of(transport).code = 0x84
        fake_of(transport).payload = b""

        assert transport.read(["nope", "vs", "0"], timeout=1.0) == (0x84, None)

    def test_an_undecodable_payload_raises_its_own_error(self, transport):
        """Distinct from a transport failure: the device answered. The
        summary poll reports it separately for that reason."""
        cast(
            _FakeCoapSession, transport._session
        ).payload = b"\x18"  # a uint8 head with no byte after it

        with pytest.raises(DecodeError):
            transport.read(["power", "vs", "0"], timeout=1.0)


class TestWrite:
    def test_takes_a_body_and_returns_the_code(self, transport):
        code = transport.write(["power", "vs", "0"], {"x.com.samsung.da.power": "Off"}, timeout=1.0)

        assert code == 0x44
        path_segs, payload = fake_of(transport).posts[0]
        assert path_segs == ["power", "vs", "0"]
        assert cbor2.loads(payload) == {"x.com.samsung.da.power": "Off"}


class TestObserve:
    def test_this_transport_supports_push(self, transport):
        assert DtlsTransport.supports_observe is True

    def test_subscribe_and_refresh_forward_to_the_session(self, transport):
        """observe.py hands a transport straight to ObserveRefreshTask,
        which only ever calls refresh_observes."""
        transport.subscribe(["power", "vs", "0"])
        transport.refresh_observes([("power", "vs", "0")])

        assert fake_of(transport).subscribed == [["power", "vs", "0"]]
        assert fake_of(transport).refreshed == [[("power", "vs", "0")]]

    def test_pace_forwards(self, transport):
        transport.pace()

        assert fake_of(transport).paced == 1
