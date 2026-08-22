"""Tests for the localthings config flow."""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar, cast
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_BYPASS_REMOTE_CONTROL,
    CONF_CA_CERT_PEM,
    CONF_CA_KEY_PEM,
    CONF_CLOUD_COURSES_ENABLED,
    CONF_DEVICE_KEY,
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEARN_MODES,
    CONF_LEARNED_MODES,
    CONF_OCF_DEVICE_ID,
    CONF_PORT,
    CONF_SERIAL,
    DOMAIN,
)

from .conftest import (
    ENTRY_DATA,
    MOCK_CA_CERT_PEM,
    MOCK_CA_KEY_PEM,
    MOCK_DEVICE_KEY,
    MOCK_HOST,
    MOCK_LEAF_CERT_PEM,
    MOCK_MODEL,
    MOCK_PORT,
    MOCK_SERIAL,
    _probe_result,
)


async def test_form_first_device(hass: HomeAssistant) -> None:
    """First device: form asks for host, CA cert, and CA key."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    data_schema = result["data_schema"]
    assert data_schema is not None
    assert CONF_CA_CERT_PEM in data_schema.schema
    assert CONF_CA_KEY_PEM in data_schema.schema
    assert CONF_OCF_DEVICE_ID in data_schema.schema


async def test_form_second_device_reuses_creds(hass: HomeAssistant) -> None:
    """Second device: form only asks for host; CA cert/key schema fields absent."""
    existing = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user_reuse"
    data_schema = result["data_schema"]
    assert data_schema is not None
    assert CONF_CA_CERT_PEM not in data_schema.schema
    assert CONF_CA_KEY_PEM not in data_schema.schema
    assert CONF_OCF_DEVICE_ID in data_schema.schema


async def test_normal_setup_does_not_enter_identity_discovery(
    hass: HomeAssistant, mock_probe
) -> None:
    """Leaving the advanced field blank preserves the original call contract."""

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
            CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    mock_probe.assert_called_once_with(
        MOCK_HOST,
        MOCK_CA_CERT_PEM,
        MOCK_CA_KEY_PEM,
        None,
    )


async def test_identity_setup_passes_canonical_di_and_route_interface(
    hass: HomeAssistant, mock_probe
) -> None:
    """The opt-in path supplies one exact di and HA's route-selected IPv4 source."""

    target = "1BB10CD6-3214-4BC5-842E-19A0FE2D8123"
    source_ip = "10.0.0.10"
    with patch(
        "homeassistant.components.network.async_get_source_ip",
        new=AsyncMock(return_value=source_ip),
    ) as source:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: MOCK_HOST,
                CONF_OCF_DEVICE_ID: target,
                CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
                CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    source.assert_awaited_once_with(hass, target_ip=MOCK_HOST)
    mock_probe.assert_called_once_with(
        MOCK_HOST,
        MOCK_CA_CERT_PEM,
        MOCK_CA_KEY_PEM,
        None,
        target.lower(),
        source_ip,
    )
    assert CONF_OCF_DEVICE_ID not in result["data"]


async def test_invalid_identity_is_a_field_error_without_network_io(
    hass: HomeAssistant, mock_probe
) -> None:
    """A malformed or nil di is rejected before route selection or probing."""

    with patch(
        "homeassistant.components.network.async_get_source_ip",
        new=AsyncMock(),
    ) as source:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: MOCK_HOST,
                CONF_OCF_DEVICE_ID: "00000000-0000-0000-0000-000000000000",
                CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
                CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_OCF_DEVICE_ID: "invalid_ocf_device_id"}
    mock_probe.assert_not_called()
    source.assert_not_awaited()


async def test_identity_setup_reports_missing_ipv4_route(hass: HomeAssistant, mock_probe) -> None:
    """Route selection failure is explicit and never enters the blocking probe."""

    with patch(
        "homeassistant.components.network.async_get_source_ip",
        new=AsyncMock(side_effect=OSError("no route")),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: MOCK_HOST,
                CONF_OCF_DEVICE_ID: MOCK_DEVICE_KEY,
                CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
                CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "identity_discovery_unavailable"}
    mock_probe.assert_not_called()


async def test_successful_setup(hass: HomeAssistant, mock_probe) -> None:
    """Happy path: valid IP connects, entry created with discovered port."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
            CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == MOCK_HOST
    assert result["data"][CONF_PORT] == MOCK_PORT
    assert result["data"][CONF_CA_CERT_PEM] == MOCK_CA_CERT_PEM


async def test_setup_normalizes_messy_pasted_pem(hass: HomeAssistant, mock_probe) -> None:
    """A PEM with a leading UTF-8 BOM, CRLF line endings, and a stray blank
    line -- the kind a Windows text editor's copy produces, as opposed to a
    `type` dump (issue #291) -- must still be accepted and stored in its
    normalized form, not rejected with an opaque InvalidHeader."""
    messy_cert = "\ufeff" + MOCK_CA_CERT_PEM.replace("\n", "\r\n") + "\r\n\r\n"
    messy_key = "\ufeff" + MOCK_CA_KEY_PEM.replace("\n", "\r\n")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: messy_cert,
            CONF_CA_KEY_PEM: messy_key,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CA_CERT_PEM] == MOCK_CA_CERT_PEM
    assert result["data"][CONF_CA_KEY_PEM] == MOCK_CA_KEY_PEM


def test_normalize_pem_strips_bom_crlf_and_blank_lines() -> None:
    """Unit-level check of the helper itself, isolated from the flow."""
    from custom_components.localthings.config_flow import _normalize_pem

    messy = "\ufeff-----BEGIN CERTIFICATE-----\r\nTEST-CA\r\n\r\n-----END CERTIFICATE-----\r\n"
    assert _normalize_pem(messy) == (
        "-----BEGIN CERTIFICATE-----\nTEST-CA\n-----END CERTIFICATE-----"
    )
    # A clean PEM (the `type`-dump case) passes through unchanged.
    clean = "-----BEGIN CERTIFICATE-----\nTEST-CA\n-----END CERTIFICATE-----"
    assert _normalize_pem(clean) == clean


def test_order_candidates_prefers_known_ports() -> None:
    """Live ports are ordered with the historically known DTLS ports first,
    then the rest ascending."""
    from custom_components.localthings.config_flow import _order_candidates

    assert _order_candidates([49160, 49153, 49155, 49154]) == [
        49154,
        49155,
        49153,
        49160,
    ]
    assert _order_candidates([49153]) == [49153]


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

    from custom_components.localthings.config_flow import _find_live_ports

    reserve = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(3)]
    for s in reserve:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in reserve]
    live_sock, live_port = reserve[0], ports[0]
    reserve[1].close()
    reserve[2].close()
    closed_ports = ports[1:]

    try:
        result = _find_live_ports(
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

    from custom_components.localthings import config_flow
    from custom_components.localthings.config_flow import _sweep_ports

    # Bind an OS-assigned port and immediately close it, same technique
    # test_find_live_ports_detects_silent_port uses for its "closed" ports --
    # once closed, loopback refuses datagrams to it, standing in for the
    # sweep wrongly ruling out a port we have strong prior evidence for.
    reserved = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reserved.bind(("127.0.0.1", 0))
    preferred_port = reserved.getsockname()[1]
    reserved.close()
    monkeypatch.setattr(config_flow, "PREFERRED_PROBE_PORTS", [preferred_port])

    live_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    live_sock.bind(("127.0.0.1", 0))
    live_port = live_sock.getsockname()[1]

    try:
        sweep, candidates = _sweep_ports("127.0.0.1", [preferred_port, live_port], 0.8)
    finally:
        live_sock.close()

    # The sweep's own verdict stays honest -- it really didn't see the
    # preferred port -- and the rescue shows up only in the candidate list.
    assert sweep.live == [live_port]
    assert set(candidates) == {preferred_port, live_port}


WASHER_DEVICE0 = [
    {"rt": ["x.com.samsung.devcol"]},
    {
        "href": "/information/vs/0",
        "rep": {
            "x.com.samsung.da.modelNum": "DA_WM_TP1_21_COMMON|20375141|20010002001811424AA30217008A0000",  # noqa: E501
            "x.com.samsung.da.description": "DA_WM_TP1_21_COMMON_WW5000C/DC92-03495A_B048",
            "x.com.samsung.da.serialNum": "DISHWASHER-49153",
        },
    },
    {"href": "/otninformation/vs/0", "rep": {"otnStatus": "None"}},
]


class FakeSession:
    """Stand-in for DtlsCoapSession that answers /device/0 for any path.

    `reject_certs` models a device whose DTLS stack breaks the handshake off
    itself -- the library raises ConnectionError for that, as opposed to the
    TimeoutError it raises when nothing answers at all.
    """

    instances: ClassVar[list[FakeSession]] = []
    reject_certs: ClassVar[set[str]] = set()
    # smartthings-local >= 0.1.3 ("redacted typed failures") no longer puts
    # the alert in connect()'s exception text -- set True to model that, so
    # a test can check the diagnostic-handshake fallback (_resolve_alert)
    # instead of the legacy _alert_name text-parsing path.
    redact_rejection: ClassVar[bool] = False

    def __init__(self, host, port, cert_pem=None, key_pem=None, **kwargs):
        self.host, self.port, self.cert_pem = host, port, cert_pem
        FakeSession.instances.append(self)

    def connect(self):
        if self.cert_pem in FakeSession.reject_certs:
            if FakeSession.redact_rejection:
                from smartthings_local.errors import SessionError

                raise SessionError()
            raise ConnectionError(
                "DTLS handshake error: [('SSL routines', '', 'sslv3 alert bad certificate')]"
            )

    def start_reader(self):
        pass

    def get(self, path, timeout=15.0):
        import cbor2

        return 0x45, cbor2.dumps(WASHER_DEVICE0)

    def close(self):
        pass


@pytest.fixture
def fake_dtls(monkeypatch):
    """Wire the probe path up to FakeSession with no real network anywhere."""
    from custom_components.localthings import config_flow

    FakeSession.instances = []
    FakeSession.reject_certs = set()
    FakeSession.redact_rejection = False
    monkeypatch.setattr(config_flow, "_fetch_samsung_uuid", lambda: "test-uuid")
    monkeypatch.setattr(
        config_flow,
        "_mint_leaf_cert",
        lambda ca_cert, ca_key, uuid: ("FULLCHAIN", "LEAFKEY"),
    )
    monkeypatch.setattr(
        "smartthings_local.protocol.dtls_session.DtlsCoapSession",
        FakeSession,
    )
    return FakeSession


class _FakeProbeResult:
    def __init__(self, port, live):
        self.port, self.outcome = port, "live" if live else "dead"
        self.is_dtls_server = live

    def __repr__(self):
        return f"<ProbeResult {self.port} {self.outcome}>"


def _patch_clienthello(monkeypatch, live_ports):
    """Patch the library's ClientHello probe to report `live_ports` as DTLS."""
    from custom_components.localthings import config_flow

    calls: list[int] = []

    def _probe(host, port, **kwargs):
        calls.append(port)
        return _FakeProbeResult(port, port in live_ports)

    monkeypatch.setattr(config_flow, "_clienthello_probe", _probe)
    return calls


async def test_clienthello_probe_picks_the_confirmed_port(
    hass: HomeAssistant, monkeypatch, fake_dtls
) -> None:
    """Issue #211: the stateless ClientHello probe identifies the real DTLS
    port, so exactly one port gets a full certificate handshake -- not every
    port the UDP sweep couldn't rule out, each costing 12s to time out."""
    from custom_components.localthings import config_flow

    probed = _patch_clienthello(monkeypatch, {49153})

    def _no_sweep(host, ports, timeout):
        raise AssertionError("UDP sweep must not run once a port is confirmed")

    monkeypatch.setattr(config_flow, "_find_live_ports", _no_sweep)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
            CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PORT] == 49153
    # The whole range is probed (cheaply, in parallel) but only the confirmed
    # port is handed a handshake.
    assert set(probed) == set(config_flow.PROBE_PORT_RANGE)
    assert [s.port for s in FakeSession.instances] == [49153]


async def test_probe_uses_discovered_low_port(hass: HomeAssistant, monkeypatch, fake_dtls) -> None:
    """A device that only answers on 49153 — outside the historical
    49154/49155 pair — is found by the liveness sweep and its port is stored
    on the config entry (issue #13).

    The sweep is the fallback now: it runs when the ClientHello probe confirms
    nothing, which covers both a path that eats our ClientHello and an install
    still on smartthings-local < 0.1.2.
    """
    from custom_components.localthings import config_flow

    _patch_clienthello(monkeypatch, set())
    monkeypatch.setattr(
        config_flow,
        "_sweep_ports",
        lambda host, ports, timeout: (_sweep_result(live=[49153]), [49153]),
    )

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
            CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PORT] == 49153


async def test_probe_falls_back_when_library_lacks_the_clienthello_probe(
    hass: HomeAssistant, monkeypatch, fake_dtls
) -> None:
    """An install whose smartthings-local predates the probe still adds
    devices -- port detection degrades to the UDP sweep rather than failing."""
    from custom_components.localthings import config_flow

    def _missing(host, port, **kwargs):
        raise ImportError("no module named dtls_probe")

    monkeypatch.setattr(config_flow, "_clienthello_probe", _missing)
    monkeypatch.setattr(
        config_flow,
        "_sweep_ports",
        lambda host, ports, timeout: (_sweep_result(live=[49154]), [49154]),
    )

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
            CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PORT] == 49154


def test_identity_scan_probes_only_advertised_ports(monkeypatch) -> None:
    """Identity candidates are statelessly verified without unioning a fixed band."""

    from custom_components.localthings import config_flow, endpoint_discovery

    endpoint = endpoint_discovery.IdentityEndpoint(MOCK_HOST, (41000, 46060))
    monkeypatch.setattr(
        endpoint_discovery,
        "discover_identity_endpoint",
        lambda host, target_device_id, interface_address: endpoint,
    )
    calls = []

    def scan(host, ports):
        calls.append((host, ports))
        return [46060]

    monkeypatch.setattr(config_flow, "_clienthello_scan", scan)

    result = config_flow._scan_identity_endpoint(
        MOCK_HOST,
        MOCK_DEVICE_KEY,
        "10.0.0.10",
    )

    assert calls == [(MOCK_HOST, [41000, 46060])]
    assert result.candidates == [46060]
    assert result.confirmed == [46060]
    assert result.swept is None


def test_identity_scan_rejects_multiple_live_dtls_endpoints(monkeypatch) -> None:
    from custom_components.localthings import config_flow, endpoint_discovery

    monkeypatch.setattr(
        endpoint_discovery,
        "discover_identity_endpoint",
        lambda host, target_device_id, interface_address: endpoint_discovery.IdentityEndpoint(
            MOCK_HOST, (41000, 46060)
        ),
    )
    monkeypatch.setattr(config_flow, "_clienthello_scan", lambda host, ports: list(ports))

    with pytest.raises(config_flow.IdentityTargetAmbiguous):
        config_flow._scan_identity_endpoint(MOCK_HOST, MOCK_DEVICE_KEY, "10.0.0.10")


def test_identity_scan_rejects_no_live_dtls_endpoint_without_fallback(monkeypatch) -> None:
    from custom_components.localthings import config_flow, endpoint_discovery

    monkeypatch.setattr(
        endpoint_discovery,
        "discover_identity_endpoint",
        lambda host, target_device_id, interface_address: endpoint_discovery.IdentityEndpoint(
            MOCK_HOST, (46060,)
        ),
    )
    monkeypatch.setattr(config_flow, "_clienthello_scan", lambda host, ports: [])
    monkeypatch.setattr(
        config_flow,
        "_scan_ports",
        lambda host: pytest.fail("the fixed-band fallback must not run"),
    )

    with pytest.raises(config_flow.IdentityNoDtlsServer):
        config_flow._scan_identity_endpoint(MOCK_HOST, MOCK_DEVICE_KEY, "10.0.0.10")


@pytest.mark.parametrize(
    ("reason", "error_key"),
    [
        ("unavailable", "identity_discovery_unavailable"),
        ("not_found", "identity_target_not_found"),
        ("ambiguous", "identity_target_ambiguous"),
        ("invalid_advertisement", "identity_advertisement_invalid"),
        ("address_mismatch", "identity_advertisement_invalid"),
        ("future_reason", "identity_advertisement_invalid"),
    ],
)
def test_identity_discovery_reasons_map_to_stable_ui_errors(reason, error_key) -> None:
    from custom_components.localthings import config_flow

    assert config_flow._identity_discovery_failure(reason).error_key == error_key


def test_probe_identity_path_has_no_fixed_scan_fallback(monkeypatch) -> None:
    """An explicit di selects one fail-closed scan path and reaches auth with that same di."""

    from custom_components.localthings import config_flow

    scan = config_flow._PortScan([46060], [46060])
    calls = []
    monkeypatch.setattr(
        config_flow,
        "_scan_ports",
        lambda host: pytest.fail("the fixed-band scan must not run for an exact di"),
    )
    monkeypatch.setattr(
        config_flow,
        "_scan_identity_endpoint",
        lambda host, target_device_id, interface_address: scan,
    )
    monkeypatch.setattr(config_flow, "_mint_credentials", lambda ca, key: ("CERT", "KEY"))

    def handshake(host, actual_scan, cert, key, expected_device_id):
        calls.append((host, actual_scan, cert, key, expected_device_id))
        return {"port": 46060}

    monkeypatch.setattr(config_flow, "_handshake_and_read", handshake)

    result = config_flow._probe_and_validate(
        MOCK_HOST,
        MOCK_CA_CERT_PEM,
        MOCK_CA_KEY_PEM,
        target_device_id=MOCK_DEVICE_KEY,
        interface_address="10.0.0.10",
    )

    assert calls == [(MOCK_HOST, scan, "CERT", "KEY", MOCK_DEVICE_KEY)]
    assert result == {"port": 46060, "leaf_cert_pem": "CERT", "leaf_key_pem": "KEY"}


@pytest.mark.parametrize("actual_device_id", [None, "0d431826-546c-428f-86d1-63d8798f8742"])
def test_authenticated_identity_must_match_exact_di_before_device_dump(
    monkeypatch, actual_device_id
) -> None:
    """A missing or mismatched di cannot fall back to pi, serial, or host."""

    from custom_components.localthings import config_flow
    from custom_components.localthings.registry import identity as identity_module

    identity = identity_module.DeviceIdentity(
        manufacturer="Samsung",
        model="model",
        name="washer",
        serial=None,
        device_id=actual_device_id,
        platform_id=MOCK_DEVICE_KEY,
    )
    monkeypatch.setattr(identity_module, "read_identity", lambda sess, serial: identity)

    class Session:
        def get(self, *args, **kwargs):
            pytest.fail("/device/0 must not be read before exact di validation")

    with pytest.raises(config_flow.IdentityMismatch):
        config_flow._read_device(Session(), MOCK_HOST, 46060, MOCK_DEVICE_KEY)


def test_authenticated_identity_match_allows_device_dump(monkeypatch) -> None:
    from custom_components.localthings import config_flow
    from custom_components.localthings.registry import identity as identity_module

    identity = identity_module.DeviceIdentity(
        manufacturer="Samsung",
        model="model",
        name="washer",
        serial=None,
        device_id=MOCK_DEVICE_KEY.upper(),
        platform_id=None,
        device_types=("oic.d.washer",),
    )
    monkeypatch.setattr(identity_module, "read_identity", lambda sess, serial: identity)

    class Session:
        def get(self, path, timeout=15.0):
            import cbor2

            assert path == ["device", "0"]
            return 0x45, cbor2.dumps(WASHER_DEVICE0)

    result = config_flow._read_device(Session(), MOCK_HOST, 46060, MOCK_DEVICE_KEY)

    assert result["port"] == 46060
    assert result["device_key"] == MOCK_DEVICE_KEY


@pytest.mark.parametrize(
    "raw_device_id",
    [
        f"uuid:{MOCK_DEVICE_KEY}",
        f"URN:UUID:{MOCK_DEVICE_KEY}",
        bytes.fromhex(MOCK_DEVICE_KEY.replace("-", "")),
    ],
)
def test_authenticated_raw_di_is_canonicalized_for_device_key(
    monkeypatch,
    raw_device_id,
) -> None:
    from custom_components.localthings import config_flow
    from custom_components.localthings.registry import identity as identity_module

    identity = identity_module.DeviceIdentity(
        manufacturer="Samsung",
        model="model",
        name="washer",
        serial=None,
        device_id=None,
        platform_id=None,
        device_types=("oic.d.washer",),
        raw={"/oic/d": {"di": raw_device_id}},
    )
    monkeypatch.setattr(identity_module, "read_identity", lambda sess, serial: identity)

    class Session:
        def get(self, path, timeout=15.0):
            import cbor2

            assert path == ["device", "0"]
            return 0x45, cbor2.dumps(WASHER_DEVICE0)

    result = config_flow._read_device(Session(), MOCK_HOST, 46060, MOCK_DEVICE_KEY)

    assert result["device_key"] == MOCK_DEVICE_KEY


def test_authenticated_identity_rejects_malformed_raw_di_before_device_dump(
    monkeypatch,
) -> None:
    """Untrusted CBOR types fail as an identity mismatch, without a fallback."""

    from custom_components.localthings import config_flow
    from custom_components.localthings.registry import identity as identity_module

    identity = identity_module.DeviceIdentity(
        manufacturer="Samsung",
        model="model",
        name="washer",
        serial=None,
        device_id=MOCK_DEVICE_KEY,
        platform_id=None,
        raw={"/oic/d": {"di": []}},
    )
    monkeypatch.setattr(identity_module, "read_identity", lambda sess, serial: identity)

    class Session:
        def get(self, *args, **kwargs):
            pytest.fail("/device/0 must not be read after malformed raw di")

    with pytest.raises(config_flow.IdentityMismatch):
        config_flow._read_device(Session(), MOCK_HOST, 46060, MOCK_DEVICE_KEY)


async def test_entry_stores_resolved_identity(hass: HomeAssistant, monkeypatch, fake_dtls) -> None:
    """The probe's identity lands on the entry (issue #236), so the
    coordinator can key its device and entities before the first poll."""
    from custom_components.localthings import config_flow
    from custom_components.localthings.const import (
        CONF_DEVICE_TYPE,
        CONF_MANUFACTURER,
        CONF_MODEL,
        CONF_SERIAL,
    )

    _patch_clienthello(monkeypatch, {49154})

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
            CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SERIAL] == "DISHWASHER-49153"
    assert result["data"][CONF_MODEL] == "DA_WM_TP1_21_COMMON"
    assert result["data"][CONF_MANUFACTURER] == "Samsung"
    assert result["data"][CONF_DEVICE_TYPE] == "washer"
    assert result["title"] == f"Samsung Washer ({MOCK_HOST})"
    assert result["result"].version == config_flow.LocalThingsConfigFlow.VERSION


async def test_second_device_reuses_the_existing_leaf(
    hass: HomeAssistant, monkeypatch, fake_dtls
) -> None:
    """Adding a second appliance skips the Samsung-cloud round trip: every
    device accepts the same leaf, and the existing entry already has one
    (issue #211). That makes the add independent of cloud reachability, not
    just faster."""
    from custom_components.localthings import config_flow

    existing = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="localthings_other")
    existing.add_to_hass(hass)
    _patch_clienthello(monkeypatch, {49154})

    def _no_cloud():
        raise AssertionError("must not contact Samsung's cloud when a leaf is available")

    monkeypatch.setattr(config_flow, "_fetch_samsung_uuid", _no_cloud)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: MOCK_HOST}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LEAF_CERT_PEM] == MOCK_LEAF_CERT_PEM
    assert FakeSession.instances[0].cert_pem == MOCK_LEAF_CERT_PEM


async def test_rejected_reused_leaf_is_reminted(
    hass: HomeAssistant, monkeypatch, fake_dtls
) -> None:
    """The UUID behind the shared leaf does rotate. A confirmed-live device
    rejecting the reused one is unambiguous enough to mint a fresh cert and
    try again, so credential reuse stays self-correcting."""
    existing = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="localthings_other")
    existing.add_to_hass(hass)
    _patch_clienthello(monkeypatch, {49154})
    FakeSession.reject_certs = {MOCK_LEAF_CERT_PEM}

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: MOCK_HOST}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Freshly minted, and it's the fresh one that got stored.
    assert result["data"][CONF_LEAF_CERT_PEM] == "FULLCHAIN"
    assert [s.cert_pem for s in FakeSession.instances] == [MOCK_LEAF_CERT_PEM, "FULLCHAIN"]


async def test_rejected_reused_leaf_is_reminted_against_a_redacted_library(
    hass: HomeAssistant, monkeypatch, fake_dtls
) -> None:
    """Same flow as test_rejected_reused_leaf_is_reminted, but against a
    connect() failure shaped like smartthings-local >= 0.1.3 -- a fixed,
    redacted exception with no alert text at all (see errors.py's
    "Classified errors"). The re-mint decision has to come from
    _resolve_alert's diagnostic-handshake fallback instead of _alert_name."""
    from custom_components.localthings import config_flow

    existing = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="localthings_other")
    existing.add_to_hass(hass)
    _patch_clienthello(monkeypatch, {49154})
    FakeSession.reject_certs = {MOCK_LEAF_CERT_PEM}
    FakeSession.redact_rejection = True

    class _DiagnosticResult:
        alert = (2, "bad_certificate")

    diagnosed: list[int] = []

    def _diagnostic_alert(host, port, cert_pem, key_pem):
        diagnosed.append(port)
        return _DiagnosticResult()

    monkeypatch.setattr(config_flow, "_diagnostic_alert", _diagnostic_alert)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: MOCK_HOST}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LEAF_CERT_PEM] == "FULLCHAIN"
    assert [s.cert_pem for s in FakeSession.instances] == [MOCK_LEAF_CERT_PEM, "FULLCHAIN"]
    assert diagnosed == [49154]


def test_diagnostic_handshake_runs_once_not_once_per_failing_port(monkeypatch) -> None:
    """_diagnostic_alert commits association state on the device (see its
    own docstring) -- running it once per failing candidate instead of once
    overall would both add latency (each is its own bounded handshake) and
    multiply that pollution right before _probe_and_validate might retry a
    real handshake against these very same ports. Three candidates fail
    here; the diagnostic must run exactly once, against the confirmed-live
    port, not three times against every candidate in scan order."""
    from custom_components.localthings import config_flow

    FakeSession.instances = []
    FakeSession.reject_certs = {MOCK_LEAF_CERT_PEM}
    FakeSession.redact_rejection = True
    monkeypatch.setattr("smartthings_local.protocol.dtls_session.DtlsCoapSession", FakeSession)

    diagnosed: list[int] = []

    class _DiagnosticResult:
        alert = (2, "bad_certificate")

    def _diagnostic_alert(host, port, cert_pem, key_pem):
        diagnosed.append(port)
        return _DiagnosticResult()

    monkeypatch.setattr(config_flow, "_diagnostic_alert", _diagnostic_alert)

    scan = _scan(confirmed=[49153, 49154], candidates=[49153, 49154, 49155])
    with pytest.raises(config_flow.CertRejected):
        config_flow._handshake_and_read(MOCK_HOST, scan, MOCK_LEAF_CERT_PEM, "KEY")

    assert diagnosed == [49153]  # confirmed-live, and only once


async def test_unconfirmed_port_failure_is_not_reminted(
    hass: HomeAssistant, monkeypatch, fake_dtls
) -> None:
    """A timeout means nothing answered, which a fresh certificate can't fix
    -- so the re-mint retry stays scoped to a device that proved it is there
    and broke the handshake off itself."""
    from custom_components.localthings import config_flow

    existing = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="localthings_other")
    existing.add_to_hass(hass)
    _patch_clienthello(monkeypatch, set())
    monkeypatch.setattr(
        config_flow,
        "_sweep_ports",
        lambda host, ports, timeout: (_sweep_result(live=[49154]), [49154]),
    )

    def _timeout(self):
        raise TimeoutError("DTLS handshake timeout")

    monkeypatch.setattr(FakeSession, "connect", _timeout)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: MOCK_HOST}
    )

    assert result["type"] == FlowResultType.FORM
    errors = result["errors"]
    assert errors is not None
    assert errors["base"] == "no_dtls_server"
    assert len(FakeSession.instances) == 1


# ---------------------------------------------------------------------------
# Failure classification: one "cannot connect" used to cover all of these
# ---------------------------------------------------------------------------


def _sweep_result(live=(), refused=(), unreachable=()):
    from custom_components.localthings.config_flow import _SweepResult

    return _SweepResult(list(live), list(refused), list(unreachable))


def _scan(confirmed=(), swept=None, candidates=(49154,)):
    from custom_components.localthings.config_flow import _PortScan

    return _PortScan(list(candidates), list(confirmed), swept)


def _openssl_alert(name: str) -> ConnectionError:
    """How the library surfaces a fatal alert received from the appliance."""
    return ConnectionError(f"DTLS handshake error: [('SSL routines', '', '{name}')]")


def test_cert_alert_is_reported_as_a_certificate_problem() -> None:
    """The single most common real setup mistake -- CA credentials that
    aren't the AC14K_M CA the appliance trusts -- used to render as "check
    the IP address is reachable and the CA credentials are correct", which
    is half wrong and gives no way to tell which half."""
    from custom_components.localthings.config_flow import (
        CertRejected,
        _classify_handshake_failure,
    )

    for alert in ("tlsv1 alert unknown ca", "sslv3 alert bad certificate"):
        err = _classify_handshake_failure(
            MOCK_HOST, _scan(confirmed=[49154]), [(49154, _openssl_alert(alert))]
        )
        assert isinstance(err, CertRejected), alert
        assert err.error_key == "cert_rejected"


def test_classify_handshake_failure_uses_a_resolved_alert_over_exception_text() -> None:
    """_handshake_and_read passes its own resolved `alerts` mapping (built
    via _resolve_alert, which is what actually classifies a failure against
    smartthings-local >= 0.1.3's redacted exceptions) -- it must win even
    when the exception text itself says nothing."""
    from custom_components.localthings.config_flow import (
        CertRejected,
        _classify_handshake_failure,
    )

    err = _classify_handshake_failure(
        MOCK_HOST,
        _scan(confirmed=[49154]),
        [(49154, RuntimeError("session operation failed"))],
        {49154: "bad_certificate"},
    )
    assert isinstance(err, CertRejected)
    assert err.error_key == "cert_rejected"


def test_resolve_alert_prefers_exception_text_over_the_diagnostic_handshake() -> None:
    """A library still stamping the alert into its exception text (< 0.1.3)
    answers for free; the diagnostic handshake must not run at all then."""
    from custom_components.localthings.config_flow import _resolve_alert

    def _must_not_run(*args, **kwargs):
        raise AssertionError("must not run the diagnostic handshake")

    with patch("custom_components.localthings.config_flow._diagnostic_alert", _must_not_run):
        name = _resolve_alert(
            _openssl_alert("tlsv1 alert unknown ca"), MOCK_HOST, 49154, "CERT", "KEY"
        )
    assert name == "unknown_ca"


def test_resolve_alert_falls_back_to_the_diagnostic_handshake() -> None:
    """smartthings-local >= 0.1.3 redacts the exception text (see errors.py's
    "Classified errors"), so the only way left to learn *why* a handshake
    failed is the library's own classification of the raw alert record."""
    from smartthings_local.errors import SessionError

    from custom_components.localthings import config_flow

    class _Result:
        alert = (2, "bad_certificate")

    with patch.object(config_flow, "_diagnostic_alert", lambda *a, **k: _Result()):
        name = config_flow._resolve_alert(SessionError(), MOCK_HOST, 49154, "CERT", "KEY")
    assert name == "bad_certificate"


def test_resolve_alert_ignores_a_non_fatal_alert() -> None:
    """ProbeResult.alert is set for a *received* alert of either level, but
    only a fatal one (2) means the appliance actually broke off the
    handshake over it -- a warning-level alert (e.g. close_notify on an
    otherwise ordinary close) is not evidence of a rejection. The old
    exception-text path never had this ambiguity: OpenSSL's exception only
    ever rendered for a fatal alert, so nothing pre-0.1.3 could confuse the
    two -- the diagnostic-handshake fallback must not introduce the mix-up."""
    from smartthings_local.errors import SessionError

    from custom_components.localthings import config_flow

    class _Result:
        alert = (1, "close_notify")  # warning level, not fatal

    with patch.object(config_flow, "_diagnostic_alert", lambda *a, **k: _Result()):
        name = config_flow._resolve_alert(SessionError(), MOCK_HOST, 49154, "CERT", "KEY")
    assert name is None


def test_resolve_alert_is_none_when_the_diagnostic_handshake_also_fails() -> None:
    """A best-effort extra probe: its own failure must not raise out of
    _resolve_alert, it just leaves the caller with no alert to report."""
    from smartthings_local.errors import SessionError, SessionTimeoutError

    from custom_components.localthings import config_flow

    def _boom(*args, **kwargs):
        raise SessionTimeoutError()

    with patch.object(config_flow, "_diagnostic_alert", _boom):
        name = config_flow._resolve_alert(SessionError(), MOCK_HOST, 49154, "CERT", "KEY")
    assert name is None


def test_non_cert_alert_is_kept_distinct_from_a_cert_problem() -> None:
    """A cipher or version mismatch is also a deliberate refusal, but no
    amount of fiddling with CA credentials will fix it."""
    from custom_components.localthings.config_flow import (
        HandshakeFailed,
        _classify_handshake_failure,
    )

    err = _classify_handshake_failure(
        MOCK_HOST,
        _scan(confirmed=[49154]),
        [(49154, _openssl_alert("tlsv1 alert protocol version"))],
    )
    assert isinstance(err, HandshakeFailed)
    assert err.error_key == "handshake_failed"


def test_confirmed_port_that_times_out_is_reported_as_a_stuck_session() -> None:
    """The ClientHello probe proved a DTLS server is right there, so this is
    not a connectivity or credentials problem -- it's the appliance still
    holding the association from the last attempt, which clears itself."""
    from custom_components.localthings.config_flow import (
        HandshakeTimeout,
        _classify_handshake_failure,
    )

    err = _classify_handshake_failure(
        MOCK_HOST, _scan(confirmed=[49154]), [(49154, TimeoutError("handshake timeout"))]
    )
    assert isinstance(err, HandshakeTimeout)
    assert err.error_key == "handshake_timeout"


def test_every_port_refused_is_reported_as_closed_ports() -> None:
    """ICMP port-unreachable on the whole range means the host is up and
    answering -- it just isn't exposing a local API. Cloud-only firmware and
    a wrong-but-live IP both land here."""
    from custom_components.localthings.config_flow import (
        PROBE_PORT_RANGE,
        PortsClosed,
        _classify_handshake_failure,
    )

    err = _classify_handshake_failure(
        MOCK_HOST,
        _scan(swept=_sweep_result(refused=PROBE_PORT_RANGE)),
        [(49154, TimeoutError("handshake timeout"))],
    )
    assert isinstance(err, PortsClosed)
    assert err.error_key == "ports_closed"


def test_unreachable_host_is_not_reported_as_closed_ports() -> None:
    """A wrong IP on the local subnet never answers ARP, so the kernel fails
    every send with EHOSTUNREACH -- no port is "live", but nothing refused
    us either. Lumping that in with a genuine refusal would tell the user
    their appliance is on cloud-only firmware when in fact there is nothing
    at that address at all."""
    from custom_components.localthings.config_flow import (
        PROBE_PORT_RANGE,
        NoResponse,
        _classify_handshake_failure,
    )

    err = _classify_handshake_failure(
        MOCK_HOST,
        _scan(swept=_sweep_result(unreachable=PROBE_PORT_RANGE)),
        [(49154, TimeoutError("handshake timeout"))],
    )
    assert isinstance(err, NoResponse)
    assert err.error_key == "no_response"


def test_total_silence_is_reported_as_no_response() -> None:
    """Not one ICMP refusal across a nine-port ephemeral range: a host that
    is really there answers for at least some of it, so this reads as
    nothing at that address rather than as a device that won't talk."""
    from custom_components.localthings.config_flow import (
        PROBE_PORT_RANGE,
        NoResponse,
        _classify_handshake_failure,
    )

    err = _classify_handshake_failure(
        MOCK_HOST,
        _scan(swept=_sweep_result(live=PROBE_PORT_RANGE)),
        [(49154, TimeoutError("handshake timeout"))],
    )
    assert isinstance(err, NoResponse)
    assert err.error_key == "no_response"


def test_partially_open_range_is_reported_as_no_dtls_server() -> None:
    """Some ports answered, some refused -- something is listening at that
    address, it just isn't a DTLS appliance."""
    from custom_components.localthings.config_flow import (
        NoDtlsServer,
        _classify_handshake_failure,
    )

    err = _classify_handshake_failure(
        MOCK_HOST,
        _scan(swept=_sweep_result(live=[49154, 49155], refused=[49153])),
        [(49154, TimeoutError("timeout"))],
    )
    assert isinstance(err, NoDtlsServer)
    assert err.error_key == "no_dtls_server"


async def test_cert_rejection_surfaces_its_own_error_in_the_form(
    hass: HomeAssistant, monkeypatch, fake_dtls
) -> None:
    """End to end: an appliance that rejects the certificate tells the user
    that, rather than the blanket connectivity message."""
    _patch_clienthello(monkeypatch, {49154})
    FakeSession.reject_certs = {"FULLCHAIN"}

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
            CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
        },
    )

    assert result["type"] == FlowResultType.FORM
    errors = result["errors"]
    assert errors is not None
    assert errors["base"] == "cert_rejected"


async def test_unreachable_cloud_gateway_is_reported_separately(
    hass: HomeAssistant, monkeypatch, fake_dtls
) -> None:
    """Minting a certificate needs Samsung's cloud once, for the UUID. Losing
    that is an internet problem on Home Assistant's side, not anything about
    the appliance or the CA credentials the old message pointed at."""
    from custom_components.localthings import config_flow

    _patch_clienthello(monkeypatch, {49154})

    def _no_cloud():
        raise OSError("Name or service not known")

    monkeypatch.setattr(config_flow, "_fetch_samsung_uuid", _no_cloud)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
            CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
        },
    )

    assert result["type"] == FlowResultType.FORM
    errors = result["errors"]
    assert errors is not None
    assert errors["base"] == "cloud_unreachable"


async def test_unusable_device0_is_reported_separately(
    hass: HomeAssistant, monkeypatch, fake_dtls
) -> None:
    """Authenticating fine and then getting something we can't read is
    neither a connectivity nor a credentials problem, and saying so saves a
    user checking both."""
    _patch_clienthello(monkeypatch, {49154})
    monkeypatch.setattr(FakeSession, "get", lambda self, path, timeout=15.0: (0x84, b""))

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
            CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
        },
    )

    assert result["type"] == FlowResultType.FORM
    errors = result["errors"]
    assert errors is not None
    assert errors["base"] == "unexpected_response"


def test_every_error_key_the_flow_can_raise_has_a_message() -> None:
    """A key with no catalog entry renders as the bare key in the UI, so the
    taxonomy and the strings have to stay in step."""
    import json
    from pathlib import Path

    from custom_components.localthings import config_flow

    keys = {
        cls.error_key
        for cls in vars(config_flow).values()
        if isinstance(cls, type)
        and issubclass(cls, (config_flow.CannotConnect, config_flow.InvalidCA))
    }
    keys.add("unknown")
    keys.add("invalid_ocf_device_id")

    catalog = json.loads(
        (
            Path(__file__).parents[2]
            / "custom_components"
            / "localthings"
            / "translations"
            / "en.json"
        ).read_text()
    )["config"]["error"]

    assert keys <= set(catalog)
    # And nothing unreachable left behind in the catalog either.
    assert set(catalog) == keys


async def test_cannot_connect(hass: HomeAssistant) -> None:
    """Failed probe: form re-shown with cannot_connect error."""
    from custom_components.localthings.config_flow import CannotConnect

    with patch(
        "custom_components.localthings.config_flow._probe_and_validate",
        side_effect=CannotConnect("no port"),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: MOCK_HOST,
                CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
                CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
            },
        )
    assert result["type"] == FlowResultType.FORM
    errors = result["errors"]
    assert errors is not None
    assert errors["base"] == "cannot_connect"


async def test_recognized_type_skips_confirmation_step(hass: HomeAssistant, mock_probe) -> None:
    """A recognized device type creates the entry with no extra step."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
            CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_unknown_type_shows_confirmation_step(
    hass: HomeAssistant, mock_probe_unknown_type
) -> None:
    """An unrecognized device type shows a confirmation step before creating the entry."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
            CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm_unknown_type"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == MOCK_HOST


async def test_unknown_type_step_description_makes_no_version_claim(
    hass: HomeAssistant, mock_probe_unknown_type
) -> None:
    """One confirmation step covers every unrecognized device. It used to be
    two, differing only in whether they blamed a missing oneUiVersion -- a
    distinction that stopped existing when detection stopped reading it."""
    import json
    import re
    from pathlib import Path

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: MOCK_HOST,
            CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
            CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
        },
    )
    assert result["step_id"] == "confirm_unknown_type"

    steps = json.loads(
        (
            Path(__file__).parents[2]
            / "custom_components"
            / "localthings"
            / "translations"
            / "en.json"
        ).read_text()
    )["config"]["step"]

    assert "confirm_unknown_type_no_version" not in steps
    description = steps["confirm_unknown_type"]["description"]
    assert "oneUiVersion" not in description
    # {model} is the only placeholder, and the step must supply it -- an
    # unfilled one renders as literal braces to the user.
    placeholders = re.findall(r"{(\w+)}", description)
    assert placeholders == ["model"]
    supplied = result["description_placeholders"]
    assert supplied is not None
    assert supplied["model"] == MOCK_MODEL


async def test_duplicate_device_aborted(hass: HomeAssistant, mock_probe) -> None:
    """Second add of the same *device key*: flow aborts.

    Keyed on the OCF device UUID rather than the serialNum (issue #381), so
    this is now the check that two genuinely distinct units can no longer
    trip -- see test_same_serial_on_two_units_is_not_a_duplicate.

    When a device already exists the form only asks for host (CA creds are
    reused), so we only submit CONF_HOST in the second configure call.
    """
    existing = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id=f"localthings_{MOCK_DEVICE_KEY}",
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    # Second-device form only has CONF_HOST; CA creds are reused from existing.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: MOCK_HOST},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_same_serial_on_two_units_is_not_a_duplicate(hass: HomeAssistant, mock_probe) -> None:
    """Issue #381: two Samsung air purifiers of one model ship the identical,
    well-formed serialNum, so keying the entry on it turned the second one
    away as already configured. The OCF device UUID differs between them,
    and it is what the entry is keyed on now, so both can be added.

    Deliberately holds the serial *constant* across the two probes and varies
    only the device key -- the exact shape of the bug report.
    """
    first = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_HOST: "192.168.0.3"},
        unique_id=f"localthings_{MOCK_DEVICE_KEY}",
    )
    first.add_to_hass(hass)

    second_probe = {
        **_probe_result(recognized=True),
        "device_key": "3771f8bf-c184-3a2d-d885-e4c9818736d2",
        "serial": MOCK_SERIAL,
    }
    with patch(
        "custom_components.localthings.config_flow._probe_and_validate",
        return_value=second_probe,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.0.14"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_KEY] == "3771f8bf-c184-3a2d-d885-e4c9818736d2"
    # Both entries exist, and the shared serial is still recorded on each --
    # it is what corroborates a later change of key.
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2
    assert result["data"][CONF_SERIAL] == first.data[CONF_SERIAL]


async def test_re_adding_during_the_migration_window_is_still_a_duplicate(
    hass: HomeAssistant, mock_probe
) -> None:
    """An entry created before v4 keeps its serial-keyed unique_id until its
    first *live* poll adopts the UUID, which can be a long while for an
    appliance that is off (it loads from its snapshot meanwhile, issue
    #295). The UUID check can't see such an entry, so without a second
    check on the legacy key, re-adding this very appliance in that window
    would be waved through -- and the two entries would collide the moment
    the older one re-keyed, with rekey_entry resolving the collision by
    deleting the duplicate rows and taking the original's entity_ids,
    history and automations with them.
    """
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={k: v for k, v in ENTRY_DATA.items() if k != CONF_DEVICE_KEY},
        unique_id=f"localthings_{MOCK_SERIAL}",
        version=3,
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: ENTRY_DATA[CONF_HOST]}
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_the_migration_window_check_still_separates_two_same_serial_units(
    hass: HomeAssistant, mock_probe
) -> None:
    """The legacy-key check above matches on the host as well as the serial,
    so it cannot undo the fix: issue #381's two units share a serial but sit
    at different addresses, and the second must still be addable while the
    first is mid-migration."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={
            **{k: v for k, v in ENTRY_DATA.items() if k != CONF_DEVICE_KEY},
            CONF_HOST: "192.168.0.3",
        },
        unique_id=f"localthings_{MOCK_SERIAL}",
        version=3,
    )
    existing.add_to_hass(hass)

    second_probe = {
        **_probe_result(recognized=True),
        "device_key": "3771f8bf-c184-3a2d-d885-e4c9818736d2",
        "serial": MOCK_SERIAL,
    }
    with patch(
        "custom_components.localthings.config_flow._probe_and_validate",
        return_value=second_probe,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.0.14"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_KEY] == "3771f8bf-c184-3a2d-d885-e4c9818736d2"


def test_probe_reads_the_device_key_from_oic_d_without_an_extra_round_trip(monkeypatch):
    """`_read_device` already fetches /oic/p and /oic/d for the device-type
    signal, so keying on the OCF UUID costs no additional GET -- it reads
    the identity that call already returned."""
    from custom_components.localthings.config_flow import _read_device

    device0 = [
        {"rt": ["x.com.samsung.devcol"]},
        {
            "href": "/information/vs/0",
            "rep": {
                "x.com.samsung.da.modelNum": "AVT-WW-TP1-23-AXX500|10251941",
                "x.com.samsung.da.serialNum": "BS7SP9AW400114A",
            },
        },
    ]

    class _Session:
        def __init__(self):
            self.paths = []

        def get(self, path, timeout=10.0):
            self.paths.append(tuple(path))
            table = {
                ("oic", "p"): {"mnmn": "Samsung Electronics", "pi": "PLATFORM-UUID"},
                ("oic", "d"): {"di": "CCFD73B3-AEB4-792A-1100-68F06F5D603B"},
                ("device", "0"): device0,
            }
            body = table.get(tuple(path))
            if body is None:
                return 0x84, b""
            import cbor2

            return 0x45, cbor2.dumps(body)

    sess = _Session()
    info = _read_device(sess, "192.168.0.3", MOCK_PORT)

    assert info["device_key"] == "ccfd73b3-aeb4-792a-1100-68f06f5d603b"
    assert info["serial"] == "BS7SP9AW400114A"
    # Exactly the three reads the probe already made before this change.
    assert sess.paths == [("oic", "p"), ("oic", "d"), ("oic", "res"), ("device", "0")]


def test_probe_marks_washer_as_recognized(monkeypatch):
    """A washer reports no oneUiVersion at all -- its consumer-model code
    must still resolve so setup doesn't warn about an unrecognized type."""

    device0 = [
        {"rt": ["x.com.samsung.devcol"]},
        {
            "href": "/information/vs/0",
            "rep": {
                "x.com.samsung.da.modelNum": "DA_WM_TP1_21_COMMON|20375141|20010002001811424AA30217008A0000",  # noqa: E501
                "x.com.samsung.da.description": "DA_WM_TP1_21_COMMON_WW5000C/DC92-03495A_B048",
                "x.com.samsung.da.serialNum": "TEST-SERIAL",
            },
        },
        {"href": "/otninformation/vs/0", "rep": {"otnStatus": "None"}},
    ]
    from custom_components.localthings.registry.batch import parse_device0_batch

    resources = parse_device0_batch(device0)

    from custom_components.localthings.registry.by_type import resolve

    assert resolve(resources) is not None


async def test_options_flow_init_shows_menu(hass: HomeAssistant) -> None:
    """The options flow's entry point is now a menu (issue #54's debug
    panel lives alongside the remote-control settings), not the settings
    form directly."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_SERIAL}")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "init"
    assert set(cast(Iterable[str], result["menu_options"])) == {
        "settings",
        "forget_learned_modes",
        "debug_write",
    }


async def test_options_flow_default_is_off(hass: HomeAssistant) -> None:
    """The bypass defaults to False, so devices that never touch this
    option see no change in the remote-control write block."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_SERIAL}")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "settings"
    data_schema = result["data_schema"]
    assert data_schema is not None
    assert data_schema({})[CONF_BYPASS_REMOTE_CONTROL] is False


async def test_options_flow_can_enable_bypass(hass: HomeAssistant) -> None:
    """Submitting the form with the toggle on stores it in entry.options,
    where coordinator.async_send_command reads it (issue #54)."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_SERIAL}")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_BYPASS_REMOTE_CONTROL: True}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_BYPASS_REMOTE_CONTROL] is True


async def test_learned_modes_option_defaults_to_on(hass: HomeAssistant) -> None:
    """Issue #327's remembering is on by default -- a device that hides a
    mode it's in should just work, not need the option found first."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_SERIAL}")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )

    data_schema = result["data_schema"]
    assert data_schema is not None
    assert data_schema({})[CONF_LEARN_MODES] is True


async def test_learned_modes_option_can_be_turned_off(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_SERIAL}")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_BYPASS_REMOTE_CONTROL: False, CONF_LEARN_MODES: False},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_LEARN_MODES] is False


async def test_cloud_courses_enabled_option_defaults_to_on(hass: HomeAssistant) -> None:
    """Issue #364's toggle starts on -- devices that already have a working
    downloaded-cycle setup keep it without having to find the option first."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_SERIAL}")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )

    data_schema = result["data_schema"]
    assert data_schema is not None
    assert data_schema({})[CONF_CLOUD_COURSES_ENABLED] is True


async def test_cloud_courses_enabled_option_can_be_turned_off(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_SERIAL}")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_BYPASS_REMOTE_CONTROL: False,
            CONF_LEARN_MODES: True,
            CONF_CLOUD_COURSES_ENABLED: False,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_CLOUD_COURSES_ENABLED] is False


@pytest.mark.parametrize(
    ("stored", "listed"),
    [
        ({"/mode/convenient/vs/0": ["Quiet"]}, "Quiet"),
        # Malformed -- nothing writes this shape, but a hand-edited
        # .storage can hold it, and this step is the one screen that can
        # clear it, so it must not be the one screen that trips over it.
        ({"/mode/convenient/vs/0": None}, "(none)"),
    ],
)
async def test_forget_learned_modes_clears_the_entry(hass: HomeAssistant, stored, listed) -> None:
    """The reset step works on an unloaded entry too, by dropping the
    persisted copy directly -- that's all a reload would restore from."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_LEARNED_MODES: stored},
        unique_id=f"localthings_{MOCK_SERIAL}",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "forget_learned_modes"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["description_placeholders"] == {"codes": listed}

    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_LEARNED_MODES] == {}


async def test_options_flow_reflects_previously_saved_value(hass: HomeAssistant) -> None:
    """Reopening the form shows the currently-saved choice as the default,
    not always False."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id=f"localthings_{MOCK_SERIAL}",
        options={CONF_BYPASS_REMOTE_CONTROL: True},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )

    data_schema = result["data_schema"]
    assert data_schema is not None
    assert data_schema({})[CONF_BYPASS_REMOTE_CONTROL] is True


async def test_options_flow_debug_write_shows_hrefs_from_coordinator(
    hass: HomeAssistant,
    mock_coordinator_session,
) -> None:
    """The debug panel's href dropdown is populated from the live
    coordinator's cached resources, not a static list."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_SERIAL}")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "debug_write"}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "debug_write"


async def test_options_flow_debug_write_aborts_when_device_not_loaded(
    hass: HomeAssistant,
) -> None:
    """No coordinator in hass.data (device never finished loading) means
    the debug panel has nothing to write to or read from."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_SERIAL}")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "debug_write"}
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "not_loaded"


async def test_options_flow_debug_edit_writes_and_shows_result(
    hass: HomeAssistant,
    mock_coordinator_session,
) -> None:
    """Picking an href, then submitting a payload, calls the write_resource
    service (issue #300) -- which drives
    coordinator.async_raw_write_sequence -- and lands on the result menu
    with the device's response."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_SERIAL}")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "debug_write"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"href": "/washer/vs/0"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "debug_edit"

    with patch(
        "custom_components.localthings.coordinator.LocalThingsCoordinator.async_raw_write_sequence",
        return_value={
            "results": [
                {
                    "href": "/washer/vs/0",
                    "code": "2.04",
                    "raw_code": 0x44,
                    "accepted": True,
                    "before": {},
                    "after": {"a": 1},
                    "changed": True,
                }
            ]
        },
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"payload": {"a": 1}},
        )

    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "debug_result"
    description_placeholders = result["description_placeholders"]
    assert description_placeholders is not None
    assert description_placeholders["code"] == "2.04 (0x44)"


async def test_options_flow_debug_edit_rejects_empty_payload(
    hass: HomeAssistant,
    mock_coordinator_session,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_SERIAL}")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "debug_write"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"href": "/washer/vs/0"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"payload": {}},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "debug_edit"
    assert result["errors"] == {"payload": "empty_payload"}


async def test_options_flow_finish_preserves_existing_options(
    hass: HomeAssistant,
    mock_coordinator_session,
) -> None:
    """Finishing from the debug-result menu must not clobber a previously
    saved remote-control bypass setting."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id=f"localthings_{MOCK_SERIAL}",
        options={CONF_BYPASS_REMOTE_CONTROL: True},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "debug_write"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"href": "/washer/vs/0"},
    )
    with patch(
        "custom_components.localthings.coordinator.LocalThingsCoordinator.async_raw_write_sequence",
        return_value={
            "results": [
                {
                    "href": "/washer/vs/0",
                    "code": "2.04",
                    "raw_code": 0x44,
                    "accepted": True,
                    "before": {},
                    "after": {"a": 1},
                    "changed": True,
                }
            ]
        },
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"payload": {"a": 1}},
        )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "debug_result"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "finish"}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_BYPASS_REMOTE_CONTROL] is True


def test_is_placeholder_serial_catches_nothing_svc():
    """Issue #83: the ARTIK051_DONGLE_REF firmware family reports the
    literal string 'Nothing(SVC)' as serialNum on every unit -- non-empty,
    so it must be caught by name, not by the plain `if not serial` check."""
    from custom_components.localthings.registry.identity import is_placeholder_serial

    assert is_placeholder_serial("Nothing(SVC)") is True
    assert is_placeholder_serial("nothing(svc)") is True
    assert is_placeholder_serial("  Nothing(SVC)  ") is True


def test_is_placeholder_serial_accepts_real_serials():
    from custom_components.localthings.registry.identity import is_placeholder_serial

    assert is_placeholder_serial("0A1B2C3D4E5F") is False
    assert is_placeholder_serial("") is False


def test_is_placeholder_serial_catches_all_same_hex_digit():
    """Issue #189: the DA_WM_A51_20_COMMON (ARTIK051) laundry board family
    reports a flash-unset sentinel instead of 'Nothing(SVC)' -- every
    character the same repeated hex digit. A washer and a dryer, two
    different physical units, both reported the literal serialNum
    'FFFFFFFFFFFFFFF', colliding on the config-entry unique_id."""
    from custom_components.localthings.registry.identity import is_placeholder_serial

    assert is_placeholder_serial("FFFFFFFFFFFFFFF") is True
    assert is_placeholder_serial("ffffffffffffffff") is True
    assert is_placeholder_serial("00000000") is True
    # Too short to be the flash-unset sentinel -- a real serial could
    # plausibly repeat one hex digit seven times by chance.
    assert is_placeholder_serial("FFFFFFF") is False


def test_resolve_serial_falls_back_to_host():
    """Both sides of the identity -- the config flow's probe and the
    coordinator's first poll -- now resolve a serial through one function, so
    they cannot disagree about what a device is called. They used to: the
    flow fell back to `host:port` and the coordinator to `host`."""
    from custom_components.localthings.registry.identity import resolve_serial

    assert resolve_serial("REAL-SERIAL", "10.0.0.5") == "REAL-SERIAL"
    assert resolve_serial("  REAL-SERIAL  ", "10.0.0.5") == "REAL-SERIAL"
    assert resolve_serial("Nothing(SVC)", "10.0.0.5") == "10.0.0.5"
    assert resolve_serial("", "10.0.0.5") == "10.0.0.5"
    assert resolve_serial(None, "10.0.0.5") == "10.0.0.5"
