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

_SUBDEVICE_ID = "11111111-2222-3333-aaaa-555555555555"
_COLLECTION_PATHS = [
    ["device", "0"],
    ["device", "1"],
    [_SUBDEVICE_ID, "device", "0"],
    [_SUBDEVICE_ID.upper(), "device", "0"],
]


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
        self.subscribe_queries: list[tuple] = []
        self.unsubscribed: list[list[str]] = []
        self.refreshed: list = []
        self.payload = cbor2.dumps({"x.com.samsung.da.power": "On"})
        self.code = 0x45
        self.post_code = 0x44
        self.post_payload = b""

    def connect(self):
        pass

    def start_reader(self):
        self.reader_started = True

    def get(self, path_segs, query=(), timeout=None):
        return self.code, self.payload

    def post(self, path_segs, payload, timeout=None):
        self.posts.append((path_segs, payload))
        return self.post_code, self.post_payload

    def pace(self):
        self.paced += 1

    def subscribe(self, path_segs, *, query=()):
        self.subscribed.append(path_segs)
        self.subscribe_queries.append(query)
        return b"\x01"

    def unsubscribe(self, path_segs):
        self.unsubscribed.append(path_segs)
        return 1

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

        with pytest.raises(DecodeError) as caught:
            transport.read(["power", "vs", "0"], timeout=1.0)
        assert (caught.value.code, caught.value.payload) == (0x45, b"\x18")

    def test_an_error_response_is_not_decoded(self, transport):
        """A 4.xx body is often a plain diagnostic string, not CBOR; it is
        handed back raw rather than turned into a decode failure."""
        fake_of(transport).code = 0x80
        fake_of(transport).payload = b"bad option"

        assert transport.read(["power", "vs", "0"], timeout=1.0) == (0x80, b"bad option")

    @pytest.mark.parametrize("path", _COLLECTION_PATHS)
    def test_device_directory_requests_batch_representations(self, transport, monkeypatch, path):
        """The R18 wall unit's prefixed collection also defaults to links."""
        from custom_components.localthings.registry.batch import parse_device0_batch

        href = "/temperature/current/0"
        rep = {"rt": ["oic.r.temperature"], "temperature": 21}
        directory = {"links": [{"href": href, "rt": rep["rt"]}]}
        batch = [{"href": href, "rep": rep}]
        calls = []

        def get(path_segs, *, timeout, query=()):
            calls.append((path_segs, query, timeout))
            return 0x45, cbor2.dumps(batch if query else directory)

        monkeypatch.setattr(fake_of(transport), "get", get)
        code, body = transport.read(path, timeout=10.0)

        assert code == 0x45
        assert parse_device0_batch(body) == {href: rep}
        assert [(p, q) for p, q, _ in calls] == [(path, ()), (path, ["if=oic.if.b"])]
        assert 0 < calls[1][2] <= 10.0
        assert fake_of(transport).paced == 1

    @pytest.mark.parametrize("path", _COLLECTION_PATHS)
    def test_a_path_that_needed_the_batch_asks_for_it_first_next_time(
        self, transport, monkeypatch, path
    ):
        """One GET per poll instead of two on a board that always answers
        links first (#597)."""
        batch = [{"href": "/power/vs/1", "rep": {"x.com.samsung.da.power": "Off"}}]
        queries = []

        def get(path_segs, *, timeout, query=()):
            queries.append(tuple(query))
            return 0x45, cbor2.dumps(batch if query else {"links": []})

        monkeypatch.setattr(fake_of(transport), "get", get)
        transport.read(path, timeout=10.0)
        queries.clear()

        assert transport.read(path, timeout=10.0) == (0x45, batch)
        assert queries == [("if=oic.if.b",)]

    @pytest.mark.parametrize("path", _COLLECTION_PATHS)
    def test_a_failed_batch_is_not_remembered(self, transport, monkeypatch, path):
        queries = []

        def get(path_segs, *, timeout, query=()):
            queries.append(tuple(query))
            return (0x84, b"") if query else (0x45, cbor2.dumps({"links": []}))

        monkeypatch.setattr(fake_of(transport), "get", get)
        transport.read(path, timeout=10.0)
        queries.clear()
        transport.read(path, timeout=10.0)

        assert queries == [(), ("if=oic.if.b",)]

    def test_batch_preference_is_kept_per_full_subdevice_path(self, transport, monkeypatch):
        other = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        calls = []

        def get(path_segs, *, timeout, query=()):
            calls.append((path_segs, tuple(query)))
            return 0x45, cbor2.dumps([] if query else {"links": []})

        monkeypatch.setattr(fake_of(transport), "get", get)
        first, second = [_SUBDEVICE_ID, "device", "0"], [other, "device", "0"]
        transport.read(first, timeout=10.0)
        calls.clear()
        transport.read(second, timeout=10.0)
        transport.read(first, timeout=10.0, query=["if=oic.if.ll"])

        assert calls == [
            (second, ()),
            (second, ("if=oic.if.b",)),
            (first, ("if=oic.if.ll",)),
        ]

    @pytest.mark.parametrize(
        ("path", "code", "body"),
        [
            (["device", "0"], 0x45, [{"href": "/power/0", "rep": {"value": True}}]),
            (["device", "0"], 0x45, {"value": True}),
            (["device", "0"], 0x45, {"links": "invalid"}),
            (["device", "0"], 0x44, {"links": []}),
            (["oic", "res"], 0x45, {"links": []}),
            (["device", "x"], 0x45, {"links": []}),
            (["device", "\u00b2"], 0x45, {"links": []}),
            (["uuid", "device", "0"], 0x45, {"links": []}),
            ([_SUBDEVICE_ID.replace("-", ""), "device", "0"], 0x45, {"links": []}),
            ([_SUBDEVICE_ID, "device", "x"], 0x45, {"links": []}),
            ([_SUBDEVICE_ID, "device", "\u00b2"], 0x45, {"links": []}),
            ([_SUBDEVICE_ID, "power", "0"], 0x45, {"links": []}),
            (["extra", _SUBDEVICE_ID, "device", "0"], 0x45, {"links": []}),
            (["power", "0"], 0x45, {"value": True}),
        ],
    )
    def test_other_reads_do_not_request_batch(self, transport, monkeypatch, path, code, body):
        calls = []

        def get(path_segs, *, timeout, query=()):
            calls.append(path_segs)
            return code, cbor2.dumps(body)

        monkeypatch.setattr(fake_of(transport), "get", get)
        assert transport.read(path, timeout=1.0) == (code, body)
        assert calls == [path]
        assert fake_of(transport).paced == 0

    @pytest.mark.parametrize("path", _COLLECTION_PATHS)
    def test_a_callers_own_query_is_sent_and_not_second_guessed(self, transport, monkeypatch, path):
        """/device/0 with an explicit query gets one GET under that query,
        not the links-then-batch fallback."""
        calls = []

        def get(path_segs, *, timeout, query=()):
            calls.append((path_segs, query))
            return 0x45, cbor2.dumps({"links": []})

        monkeypatch.setattr(fake_of(transport), "get", get)
        assert transport.read(path, timeout=1.0, query=["if=oic.if.ll"]) == (
            0x45,
            {"links": []},
        )
        assert calls == [(path, ("if=oic.if.ll",))]

    @pytest.mark.parametrize("path", _COLLECTION_PATHS)
    @pytest.mark.parametrize(
        ("code", "payload", "expected"),
        [
            (0x80, b"bad option", b"bad option"),
            (0x84, b"", None),
            (0x45, cbor2.dumps({"links": []}), {"links": []}),
        ],
    )
    def test_batch_fallback_preserves_response_contract(
        self, transport, monkeypatch, code, payload, expected, path
    ):
        queries = []

        def get(path_segs, *, timeout, query=()):
            queries.append(query)
            return (code, payload) if query else (0x45, cbor2.dumps({"links": []}))

        monkeypatch.setattr(fake_of(transport), "get", get)
        assert transport.read(path, timeout=1.0) == (code, expected)
        assert queries == [(), ["if=oic.if.b"]]

    @pytest.mark.parametrize("path", _COLLECTION_PATHS)
    def test_batch_decode_error_retains_payload(self, transport, monkeypatch, path):
        def get(path_segs, *, timeout, query=()):
            return 0x45, b"\x18" if query else cbor2.dumps({"links": []})

        monkeypatch.setattr(fake_of(transport), "get", get)
        with pytest.raises(DecodeError) as caught:
            transport.read(path, timeout=1.0)
        assert (caught.value.code, caught.value.payload) == (0x45, b"\x18")

    @pytest.mark.parametrize("path", _COLLECTION_PATHS)
    @pytest.mark.parametrize("elapsed", [3.0, 10.0, 11.0])
    def test_batch_read_shares_timeout_including_pacing(
        self, transport, monkeypatch, elapsed, path
    ):
        now = [100.0]
        timeouts = []

        def get(path_segs, *, timeout, query=()):
            timeouts.append(timeout)
            now[0] += elapsed / 2
            return 0x45, cbor2.dumps([] if query else {"links": []})

        def pace():
            now[0] += elapsed / 2

        monkeypatch.setattr(
            "custom_components.localthings.transport.time.monotonic", lambda: now[0]
        )
        monkeypatch.setattr(fake_of(transport), "get", get)
        monkeypatch.setattr(fake_of(transport), "pace", pace)
        if elapsed >= 10:
            with pytest.raises(TimeoutError, match="batch read"):
                transport.read(path, timeout=10.0)
            assert timeouts == [10.0]
        else:
            assert transport.read(path, timeout=10.0) == (0x45, [])
            assert timeouts == [10.0, 7.0]


class TestWrite:
    def test_takes_a_body_and_returns_the_code(self, transport):
        code, response = transport.write(
            ["power", "vs", "0"], {"x.com.samsung.da.power": "Off"}, timeout=1.0
        )

        assert code == 0x44
        assert response is None
        path_segs, payload = fake_of(transport).posts[0]
        assert path_segs == ["power", "vs", "0"]
        assert cbor2.loads(payload) == {"x.com.samsung.da.power": "Off"}

    def test_an_undecodable_response_is_returned_raw_not_raised(self, transport):
        """Raising here would send an already-accepted write a second time
        through the caller's reconnect-and-retry."""
        fake_of(transport).post_payload = b"\x18"

        assert transport.write(["power", "vs", "0"], {"x": 1}, timeout=1.0) == (0x44, b"\x18")

    def test_a_batch_list_goes_on_the_wire_in_order(self, transport):
        body = [{"href": "/devices/0"}, {"href": "/mode/vs/0", "rep": {"x": 1}}]

        transport.write(["device", "0"], body, timeout=1.0)

        assert cbor2.loads(fake_of(transport).posts[0][1]) == body


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

    def test_a_subscription_query_and_unsubscribe_forward(self, transport):
        """observe_resource subscribes under a query and retires its own
        relation afterwards (issue #607)."""
        transport.subscribe(["power", "vs", "0"], query=["if=oic.if.baseline"])
        transport.unsubscribe(["power", "vs", "0"])

        assert fake_of(transport).subscribe_queries == [("if=oic.if.baseline",)]
        assert fake_of(transport).unsubscribed == [["power", "vs", "0"]]

    def test_pace_forwards(self, transport):
        transport.pace()

        assert fake_of(transport).paced == 1


def test_a_links_only_lower_zone_is_discovered_through_its_batch(transport, monkeypatch):
    """#597's Dual Cook answers a plain GET of /device/1 with links only;
    through the transport, enumeration still finds the lower zone."""
    from custom_components.localthings.registry.subdevices import enumerate_subdevices

    lower = [
        {"href": "/device/1", "rep": {"rt": ["x.com.samsung.devcol", "oic.wk.col"]}},
        {"href": "/power/vs/1", "rep": {"x.com.samsung.da.power": "Off"}},
        {"href": "/oven/vs/1", "rep": {"x.com.samsung.da.state": "Ready"}},
    ]

    def get(path_segs, *, timeout, query=()):
        if list(path_segs) != ["device", "1"]:
            return 0x84, b""
        if query:
            return 0x45, cbor2.dumps(lower)
        return 0x45, cbor2.dumps({"links": [{"href": "/power/vs/1"}, {"href": "/oven/vs/1"}]})

    monkeypatch.setattr(fake_of(transport), "get", get)
    oic_res = [{"href": "/device/1"}, {"href": "/power/vs/1"}]

    subdevices, fetched = enumerate_subdevices(transport, {}, oic_res)

    assert [s.key for s in subdevices] == ["1"]
    assert fetched["/oven/vs/1"] == {"x.com.samsung.da.state": "Ready"}


@pytest.mark.parametrize("prefixed_hrefs", [False, True])
async def test_links_only_prefixed_collection_discovers_wall_unit_swing(
    hass, transport, monkeypatch, prefixed_hrefs
):
    """The wall unit has swing even when the floor unit has no such href."""
    from custom_components.localthings.climate import LocalThingsClimate
    from custom_components.localthings.registry.entities import ClimateDesc
    from custom_components.localthings.registry.identity import DeviceIdentity
    from tests.test_subdevice_discovery import _coordinator

    main = {
        "/information/vs/0": {"x.com.samsung.da.modelNum": "R18_BIXBY_FAC_TIME_23K"},
        "/subdevices/vs/0": {"x.com.samsung.da.subdeviceIdList": [_SUBDEVICE_ID]},
        "/power/vs/0": {"x.com.samsung.da.power": "Off"},
        "/mode/vs/0": {
            "x.com.samsung.da.modes": ["Cool"],
            "x.com.samsung.da.supportedModes": ["Cool", "Dry", "Wind"],
        },
        "/wind/strength/vs/0": {
            "x.com.samsung.da.modes": "0",
            "x.com.samsung.da.supportedModes": ["0"],
        },
    }
    direction = {
        "rt": ["x.com.samsung.da.wind.direction"],
        "x.com.samsung.da.modes": "Fix",
        "x.com.samsung.da.supportedModes": ["Fix", "Up_And_Low", "Left_And_Right", "All"],
    }
    wall = {
        "/mode/vs/0": main["/mode/vs/0"],
        "/power/vs/0": {"x.com.samsung.da.power": "On"},
        "/wind/direction/vs/0": direction,
    }
    batch = [
        {"href": f"/{_SUBDEVICE_ID}{href}" if prefixed_hrefs else href, "rep": rep}
        for href, rep in wall.items()
    ]
    reads = []

    def get(path_segs, *, timeout, query=()):
        reads.append((list(path_segs), tuple(query)))
        if list(path_segs) != [_SUBDEVICE_ID, "device", "0"]:
            return 0x84, b""
        return 0x45, cbor2.dumps(
            batch if query else {"links": [{"href": item["href"]} for item in batch]}
        )

    monkeypatch.setattr(fake_of(transport), "get", get)
    coordinator = _coordinator(hass)
    coordinator._session = transport
    coordinator._identity = DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="R18_BIXBY_FAC_TIME_23K",
        name="",
        serial=None,
        device_types=(),
        raw={"/oic/res": []},
    )
    merged = await hass.async_add_executor_job(coordinator._enumerate_subdevices_blocking, main)
    coordinator._run_discovery(merged)
    for href, rep in coordinator._live_subdevice_resources(merged).items():
        coordinator._observe.apply(href, rep, source="poll")
    climates = {
        bound.subdevice.key: LocalThingsClimate(coordinator, bound)
        for bound in coordinator.bound
        if isinstance(bound.desc, ClimateDesc)
    }

    assert _SUBDEVICE_ID in climates
    assert climates[_SUBDEVICE_ID].swing_modes == ["off", "vertical", "horizontal", "both"]
    assert climates[_SUBDEVICE_ID].swing_mode == "off"
    assert climates[_SUBDEVICE_ID].hvac_mode == "cool"
    assert climates[""].hvac_mode == "off"
    assert climates[""].swing_modes == []
    assert not coordinator.resource("/wind/direction/vs/0")
    assert coordinator.resource(f"/{_SUBDEVICE_ID}/wind/direction/vs/0") == direction
    assert f"/{_SUBDEVICE_ID}/wind/direction/vs/0" in coordinator._warm_hrefs
    assert fake_of(transport).posts == []
    assert [read for read in reads if read[0][0] == _SUBDEVICE_ID] == [
        ([_SUBDEVICE_ID, "device", "0"], ()),
        ([_SUBDEVICE_ID, "device", "0"], ("if=oic.if.b",)),
    ]
