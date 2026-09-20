"""Tests for pre-authentication host probing."""

from __future__ import annotations


class _FakeLiveness:
    """Stands in for smartthings_local's DtlsLivenessResult."""

    def __init__(self, port: int, responder_port: int | None) -> None:
        self.port = port
        self.responder_port = responder_port
        self.is_dtls_server = responder_port is not None


class _FakeProbeSet:
    """Stands in for smartthings_local's DtlsPortProbeResult."""

    def __init__(self, outcome: str, selected_port: int | None, results: tuple) -> None:
        self.outcome = outcome
        self.selected_port = selected_port
        self.results = results

    @property
    def live_ports(self) -> tuple[int, ...]:
        return tuple(r.port for r in self.results if r.is_dtls_server)

    @property
    def responder_ports(self) -> tuple[int, ...]:
        return tuple(
            dict.fromkeys(
                r.responder_port
                for r in self.results
                if r.is_dtls_server and r.responder_port is not None
            )
        )


def test_order_candidates_prefers_known_ports() -> None:
    """Live ports are ordered with the historically known DTLS ports first,
    then the rest ascending."""
    from custom_components.localthings.probing import order_candidates

    assert order_candidates([49160, 49153, 49155, 49154]) == [
        49154,
        49155,
        49153,
        49160,
    ]
    assert order_candidates([49153]) == [49153]


def test_find_live_ports_detects_silent_port(socket_enabled) -> None:
    """The UDP liveness sweep flags a bound-but-silent port as live and drops
    ports that refuse with ICMP port-unreachable.

    A bound, never-recv'd UDP socket stands in for a device that listens but
    stays silent (open|filtered), like the dishwasher in issue #13 on 49153.
    Two sibling ports are reserved then closed so loopback refuses datagrams
    to them, standing in for the closed ports the scan should discard.

    `socket_enabled` lifts pytest-socket's default block (the HA test harness
    disables real sockets); this test genuinely needs loopback UDP to exercise
    the ICMP-unreachable path.
    """
    import socket

    from custom_components.localthings.probing import find_live_ports

    reserve = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(3)]
    for s in reserve:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in reserve]
    live_sock, live_port = reserve[0], ports[0]
    reserve[1].close()
    reserve[2].close()
    closed_ports = ports[1:]

    try:
        result = find_live_ports(
            "127.0.0.1",
            [closed_ports[0], live_port, closed_ports[1]],
            0.8,
        )
    finally:
        live_sock.close()

    assert result.live == [live_port]
    # Refused, not unreachable: loopback is up and answered. That distinction
    # is what stops a wrong-but-live IP and an address with nothing on it
    # producing the same message.
    assert result.refused == sorted(closed_ports)
    assert result.unreachable == []


def test_sweep_ports_rescues_preferred_ports_the_sweep_missed(
    socket_enabled,
    monkeypatch,
) -> None:
    """Issue #192: a segregated VLAN made the ICMP-based sweep call three
    closed ports live while missing the one port (a historically confirmed
    DTLS port) that nmap showed as genuinely open|filtered. The sweep's
    verdict on a preferred port shouldn't be trusted blindly -- it must
    always come back as a candidate even if the sweep marked it dead, so the
    config flow gets a real handshake attempt against it.

    Uses an OS-assigned port monkeypatched into PREFERRED_PROBE_PORTS rather
    than the real 49154/49155, so this doesn't depend on those specific
    system ports being free on whatever machine runs the suite.
    """
    import socket

    from custom_components.localthings import probing
    from custom_components.localthings.probing import sweep_ports

    # Bind an OS-assigned port and immediately close it, same technique
    # test_find_live_ports_detects_silent_port uses for its "closed" ports --
    # once closed, loopback refuses datagrams to it, standing in for the
    # sweep wrongly ruling out a port we have strong prior evidence for.
    reserved = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reserved.bind(("127.0.0.1", 0))
    preferred_port = reserved.getsockname()[1]
    reserved.close()
    monkeypatch.setattr(probing, "PREFERRED_PROBE_PORTS", [preferred_port])

    live_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    live_sock.bind(("127.0.0.1", 0))
    live_port = live_sock.getsockname()[1]

    try:
        sweep, candidates = sweep_ports("127.0.0.1", [preferred_port, live_port], 0.8)
    finally:
        live_sock.close()

    # The sweep's own verdict stays honest -- it really didn't see the
    # preferred port -- and the rescue shows up only in the candidate list.
    assert sweep.live == [live_port]
    assert set(candidates) == {preferred_port, live_port}


def test_clienthello_scan_selects_the_port_that_answered(monkeypatch) -> None:
    """Issue #486: an OCF stack answers a first flight from its own ephemeral
    DTLS socket whatever port was addressed, so every port dialled looks live.
    The port to dial is the one a reply came *from*."""
    from custom_components.localthings import probing

    dialled = [49152, 49153, 49154]

    def _probe_ports(host, ports, *, preferred_port=None, **kwargs):
        # Every dialled port "answers", all from 49155 -- the shape that made
        # the old scan return the whole range.
        results = tuple(_FakeLiveness(port=p, responder_port=49155) for p in ports)
        return _FakeProbeSet(outcome="selected", selected_port=49155, results=results)

    monkeypatch.setattr("smartthings_local.protocol.dtls_probe.probe_dtls_ports", _probe_ports)
    assert probing._clienthello_scan("10.0.0.1", dialled) == [49155]


def test_clienthello_scan_returns_every_responder_when_ambiguous(monkeypatch) -> None:
    """Two distinct responder ports and no preferred port: the library declines
    to choose, and both are still proven servers worth a handshake."""
    from custom_components.localthings import probing

    def _probe_ports(host, ports, *, preferred_port=None, **kwargs):
        results = (
            _FakeLiveness(port=49152, responder_port=49155),
            _FakeLiveness(port=49153, responder_port=49154),
        )
        return _FakeProbeSet(outcome="ambiguous", selected_port=None, results=results)

    monkeypatch.setattr("smartthings_local.protocol.dtls_probe.probe_dtls_ports", _probe_ports)
    assert probing._clienthello_scan("10.0.0.1", [49152, 49153]) == [49154, 49155]


def test_clienthello_scan_passes_the_preferred_port_through(monkeypatch) -> None:
    """The advertised port is the tie-breaker the library selects on."""
    from custom_components.localthings import probing

    seen: dict = {}

    def _probe_ports(host, ports, *, preferred_port=None, **kwargs):
        seen["ports"] = list(ports)
        seen["preferred"] = preferred_port
        return _FakeProbeSet(
            outcome="selected",
            selected_port=46060,
            results=(_FakeLiveness(port=46060, responder_port=46060),),
        )

    monkeypatch.setattr("smartthings_local.protocol.dtls_probe.probe_dtls_ports", _probe_ports)
    assert probing._clienthello_scan("10.0.0.1", [46060, 49152], preferred=46060) == [46060]
    assert seen["preferred"] == 46060


class _FakeDiscovery:
    """Stands in for smartthings_local's OcfSecurePortDiscoveryResult."""

    def __init__(self, ports=(), response_received=False, error_code=None) -> None:
        self.ports = ports
        self.response_received = response_received
        self.error_code = error_code
        self.attempts = 1

    @property
    def found(self) -> bool:
        return bool(self.ports)


class _FakeRead:
    """Stands in for smartthings_local's PlaintextOcfResourceResult."""

    def __init__(self, body=None) -> None:
        import cbor2

        self.payload = cbor2.dumps(body) if body is not None else b""
        self.code = 0x45 if body is not None else None
        self.error_code = None if body is not None else "no_ocf_response"

    @property
    def complete(self) -> bool:
        return self.code is not None and self.error_code is None

    @property
    def successful(self) -> bool:
        code = self.code
        return self.complete and code is not None and code >> 5 == 2


def test_discover_advertised_ports_reads_the_secure_port(monkeypatch) -> None:
    """Measured on both appliances: 5683 and 49153 answer and both name 49154."""
    from custom_components.localthings import probing

    def _discover(host, *, discovery_port, **kwargs):
        if discovery_port in (5683, 49153):
            return _FakeDiscovery(ports=(49154,), response_received=True)
        return _FakeDiscovery(error_code="no_ocf_response")

    monkeypatch.setattr(
        "smartthings_local.protocol.ocf_discovery.discover_ocf_secure_ports", _discover
    )
    assert probing._discover_advertised_ports("10.0.0.1") == ((49154,), 5683)


def test_discover_advertised_ports_falls_through_to_the_second_candidate(monkeypatch) -> None:
    """A board that does not answer 5683, for whatever reason, is still found
    on the second candidate."""
    from custom_components.localthings import probing

    def _discover(host, *, discovery_port, **kwargs):
        if discovery_port == 49153:
            return _FakeDiscovery(ports=(49155,), response_received=True)
        return _FakeDiscovery(error_code="no_ocf_response")

    monkeypatch.setattr(
        "smartthings_local.protocol.ocf_discovery.discover_ocf_secure_ports", _discover
    )
    assert probing._discover_advertised_ports("10.0.0.1") == ((49155,), 49153)


def test_discover_advertised_ports_is_silent_on_a_non_appliance(monkeypatch) -> None:
    from custom_components.localthings import probing

    monkeypatch.setattr(
        "smartthings_local.protocol.ocf_discovery.discover_ocf_secure_ports",
        lambda host, **kwargs: _FakeDiscovery(error_code="no_ocf_response"),
    )
    assert probing._discover_advertised_ports("10.0.0.1") == ((), None)


def test_read_plaintext_identity_pulls_di_and_model(monkeypatch) -> None:
    """The real /oic/d and /oic/p bodies from the refrigerator at 10.0.0.254."""
    from custom_components.localthings import probing

    bodies = {
        "/oic/d": {
            "rt": ["oic.wk.d", "oic.d.refrigerator"],
            "di": "62304e1f-eb40-741d-4aee-99175ed16e81",
            "n": "Samsung-Refrigerator",
        },
        "/oic/p": {
            "vid": "DA-REF-NORMAL-01011",
            "mnfv": "A-RFWW-TP1-24-T4-COM_20260617",
            "mnmo": "TP1X_REF_21K|00176141|00000850031813294103010041030000",
            "mnmn": "Samsung Electronics",
        },
    }
    monkeypatch.setattr(
        "smartthings_local.protocol.ocf_discovery.read_plaintext_ocf_resource",
        lambda host, href, **kwargs: _FakeRead(bodies.get(href)),
    )
    identity = probing._read_plaintext_identity("10.0.0.254", 5683)
    assert identity is not None
    assert identity.device_id == "62304e1f-eb40-741d-4aee-99175ed16e81"
    assert identity.model.startswith("TP1X_REF_21K|")
    assert identity.vendor_id == "DA-REF-NORMAL-01011"
    assert identity.name == "Samsung-Refrigerator"


def test_read_plaintext_identity_is_none_when_nothing_answers(monkeypatch) -> None:
    from custom_components.localthings import probing

    monkeypatch.setattr(
        "smartthings_local.protocol.ocf_discovery.read_plaintext_ocf_resource",
        lambda host, href, **kwargs: _FakeRead(None),
    )
    assert probing._read_plaintext_identity("10.0.0.1", 5683) is None


def _patch_tier(monkeypatch, *, advertised=(), plaintext_port=None, identity=None):
    """Stand in for the whole plaintext tier."""
    from custom_components.localthings import probing

    monkeypatch.setattr(
        probing, "_discover_advertised_ports", lambda host: (advertised, plaintext_port)
    )
    monkeypatch.setattr(probing, "_read_plaintext_identity", lambda host, port: identity)


def test_look_dials_an_advertised_port_outside_the_range(monkeypatch) -> None:
    """Issue #435: a Family Hub was seen at 46060, then 39181. No sweep of
    49152-49160 reaches either; the device's own advertisement does."""
    from custom_components.localthings import probing

    identity = probing.PlaintextIdentity(
        device_id="abc", model="TP1X_REF_21K|x", vendor_id="", firmware="", name=""
    )
    _patch_tier(monkeypatch, advertised=(46060,), plaintext_port=5683, identity=identity)

    seen: dict = {}

    def _scan(host, ports, preferred=None):
        seen["ports"], seen["preferred"] = list(ports), preferred
        return [46060]

    monkeypatch.setattr(probing, "_clienthello_scan", _scan)

    probe = probing.look("10.0.0.1")
    assert seen["ports"][0] == 46060
    assert seen["preferred"] == 46060
    assert probe.candidates == [46060]
    assert probe.advertised == (46060,)
    assert probe.plaintext is identity
    assert probe.plaintext_port == 5683


def test_look_falls_back_to_the_range_when_nothing_is_advertised(monkeypatch) -> None:
    from custom_components.localthings import probing

    _patch_tier(monkeypatch)

    seen: dict = {}

    def _scan(host, ports, preferred=None):
        seen["ports"], seen["preferred"] = list(ports), preferred
        return [49154]

    monkeypatch.setattr(probing, "_clienthello_scan", _scan)

    probe = probing.look("10.0.0.1")
    assert seen["ports"] == list(probing.PROBE_PORT_RANGE)
    assert seen["preferred"] is None
    assert probe.advertised == ()
    assert probe.plaintext is None


def test_look_skips_the_identity_read_when_no_plaintext_port_answered(monkeypatch) -> None:
    from custom_components.localthings import probing

    monkeypatch.setattr(probing, "_discover_advertised_ports", lambda host: ((), None))

    def _never(host, port):
        raise AssertionError("no plaintext port answered; nothing to read")

    monkeypatch.setattr(probing, "_read_plaintext_identity", _never)
    monkeypatch.setattr(probing, "_clienthello_scan", lambda host, ports, preferred=None: [])
    monkeypatch.setattr(
        probing, "sweep_ports", lambda host, ports, timeout: (probing.SweepResult([], [], []), [])
    )

    assert probing.look("10.0.0.1").plaintext is None
