"""Tests for the localthings config flow."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_BYPASS_REMOTE_CONTROL, CONF_CA_CERT_PEM, CONF_CA_KEY_PEM,
    CONF_HOST, CONF_LEAF_CERT_PEM, CONF_LEAF_KEY_PEM, CONF_PORT, DOMAIN,
)

from .conftest import (
    ENTRY_DATA, MOCK_CA_CERT_PEM, MOCK_CA_KEY_PEM, MOCK_HOST, MOCK_PORT, MOCK_SERIAL,
)


async def test_form_first_device(hass: HomeAssistant) -> None:
    """First device: form asks for host, CA cert, and CA key."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': 'user'}
    )
    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'user'
    assert CONF_CA_CERT_PEM in result['data_schema'].schema
    assert CONF_CA_KEY_PEM in result['data_schema'].schema


async def test_form_second_device_reuses_creds(hass: HomeAssistant) -> None:
    """Second device: form only asks for host; CA cert/key schema fields absent."""
    existing = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': 'user'}
    )
    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'user_reuse'
    assert CONF_CA_CERT_PEM not in result['data_schema'].schema
    assert CONF_CA_KEY_PEM not in result['data_schema'].schema


async def test_successful_setup(hass: HomeAssistant, mock_probe) -> None:
    """Happy path: valid IP connects, entry created with discovered port."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': 'user'}
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {CONF_HOST: MOCK_HOST, CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM, CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM},
    )
    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert result['data'][CONF_HOST] == MOCK_HOST
    assert result['data'][CONF_PORT] == MOCK_PORT
    assert result['data'][CONF_CA_CERT_PEM] == MOCK_CA_CERT_PEM


def test_order_candidates_prefers_known_ports() -> None:
    """Live ports are ordered with the historically known DTLS ports first,
    then the rest ascending."""
    from custom_components.localthings.config_flow import _order_candidates

    assert _order_candidates([49160, 49153, 49155, 49154]) == [
        49154, 49155, 49153, 49160,
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
        s.bind(('127.0.0.1', 0))
    ports = [s.getsockname()[1] for s in reserve]
    live_sock, live_port = reserve[0], ports[0]
    reserve[1].close()
    reserve[2].close()
    closed_ports = ports[1:]

    try:
        result = _find_live_ports(
            '127.0.0.1', [closed_ports[0], live_port, closed_ports[1]], 0.8,
        )
    finally:
        live_sock.close()

    assert result == [live_port]


def test_find_live_ports_rescues_preferred_ports_the_sweep_missed(
    socket_enabled, monkeypatch,
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
    from custom_components.localthings.config_flow import _find_live_ports

    # Bind an OS-assigned port and immediately close it, same technique
    # test_find_live_ports_detects_silent_port uses for its "closed" ports --
    # once closed, loopback refuses datagrams to it, standing in for the
    # sweep wrongly ruling out a port we have strong prior evidence for.
    reserved = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reserved.bind(('127.0.0.1', 0))
    preferred_port = reserved.getsockname()[1]
    reserved.close()
    monkeypatch.setattr(config_flow, 'PREFERRED_PROBE_PORTS', [preferred_port])

    live_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    live_sock.bind(('127.0.0.1', 0))
    live_port = live_sock.getsockname()[1]

    try:
        result = _find_live_ports('127.0.0.1', [preferred_port, live_port], 0.8)
    finally:
        live_sock.close()

    assert set(result) == {preferred_port, live_port}


async def test_probe_uses_discovered_low_port(hass: HomeAssistant, monkeypatch) -> None:
    """A device that only answers on 49153 — outside the historical
    49154/49155 pair — is found by the liveness sweep and its port is stored
    on the config entry (issue #13)."""
    import cbor2

    from custom_components.localthings import config_flow

    device0 = [
        {'rt': ['x.com.samsung.devcol']},
        {'href': '/information/vs/0', 'rep': {
            'x.com.samsung.da.modelNum':
                'DA_WM_TP1_21_COMMON|20375141|20010002001811424AA30217008A0000',
            'x.com.samsung.da.description':
                'DA_WM_TP1_21_COMMON_WW5000C/DC92-03495A_B048',
            'x.com.samsung.da.serialNum': 'DISHWASHER-49153',
        }},
        {'href': '/otninformation/vs/0', 'rep': {'otnStatus': 'None'}},
    ]

    class _FakeSession:
        def __init__(self, host, port, cert_pem=None, key_pem=None):
            self.host, self.port = host, port

        def connect(self):
            pass

        def start_reader(self):
            pass

        def get(self, path, timeout=15.0):
            return 0x45, cbor2.dumps(device0)

        def close(self):
            pass

    monkeypatch.setattr(config_flow, '_fetch_samsung_uuid', lambda: 'test-uuid')
    monkeypatch.setattr(
        config_flow, '_mint_leaf_cert',
        lambda ca_cert, ca_key, uuid: ('FULLCHAIN', 'LEAFKEY'),
    )
    monkeypatch.setattr(
        config_flow, '_find_live_ports',
        lambda host, ports, timeout: [49153],
    )
    monkeypatch.setattr(
        'smartthings_local.protocol.dtls_session.DtlsCoapSession', _FakeSession,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': 'user'}
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {CONF_HOST: MOCK_HOST, CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM, CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM},
    )
    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert result['data'][CONF_PORT] == 49153


async def test_cannot_connect(hass: HomeAssistant) -> None:
    """Failed probe: form re-shown with cannot_connect error."""
    from custom_components.localthings.config_flow import CannotConnect

    with patch(
        'custom_components.localthings.config_flow._probe_and_validate',
        side_effect=CannotConnect('no port'),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': 'user'}
        )
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'],
            {CONF_HOST: MOCK_HOST, CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM, CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM},
        )
    assert result['type'] == FlowResultType.FORM
    assert result['errors']['base'] == 'cannot_connect'


async def test_recognized_type_skips_confirmation_step(
    hass: HomeAssistant, mock_probe
) -> None:
    """A recognized device type creates the entry with no extra step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': 'user'}
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {CONF_HOST: MOCK_HOST, CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM, CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM},
    )
    assert result['type'] == FlowResultType.CREATE_ENTRY


async def test_unknown_type_shows_confirmation_step(
    hass: HomeAssistant, mock_probe_unknown_type
) -> None:
    """An unrecognized device type shows a confirmation step before creating the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': 'user'}
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {CONF_HOST: MOCK_HOST, CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM, CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM},
    )
    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'confirm_unknown_type'

    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {},
    )
    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert result['data'][CONF_HOST] == MOCK_HOST


async def test_unknown_type_step_description_makes_no_version_claim(
    hass: HomeAssistant, mock_probe_unknown_type
) -> None:
    """One confirmation step covers every unrecognized device. It used to be
    two, differing only in whether they blamed a missing oneUiVersion -- a
    distinction that stopped existing when detection stopped reading it."""
    import json
    from pathlib import Path

    steps = json.loads(
        (Path(__file__).parents[2] / 'custom_components' / 'localthings'
         / 'translations' / 'en.json').read_text()
    )['config']['step']

    assert 'confirm_unknown_type_no_version' not in steps
    description = steps['confirm_unknown_type']['description']
    assert 'oneUiVersion' not in description
    assert '{' not in description   # no unfilled placeholder


async def test_duplicate_device_aborted(hass: HomeAssistant, mock_probe) -> None:
    """Second add of same serial: flow aborts.

    When a device already exists the form only asks for host (CA creds are
    reused), so we only submit CONF_HOST in the second configure call.
    """
    existing = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id=f'localthings_{MOCK_SERIAL}',
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': 'user'}
    )
    # Second-device form only has CONF_HOST; CA creds are reused from existing.
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'],
        {CONF_HOST: MOCK_HOST},
    )
    assert result['type'] == FlowResultType.ABORT
    assert result['reason'] == 'already_configured'


def test_probe_marks_washer_as_recognized(monkeypatch):
    """A washer reports no oneUiVersion at all -- its consumer-model code
    must still resolve so setup doesn't warn about an unrecognized type."""
    from custom_components.localthings import config_flow

    device0 = [
        {'rt': ['x.com.samsung.devcol']},
        {'href': '/information/vs/0', 'rep': {
            'x.com.samsung.da.modelNum':
                'DA_WM_TP1_21_COMMON|20375141|20010002001811424AA30217008A0000',
            'x.com.samsung.da.description':
                'DA_WM_TP1_21_COMMON_WW5000C/DC92-03495A_B048',
            'x.com.samsung.da.serialNum': 'TEST-SERIAL',
        }},
        {'href': '/otninformation/vs/0', 'rep': {'otnStatus': 'None'}},
    ]
    from custom_components.localthings.registry.batch import parse_device0_batch
    resources = parse_device0_batch(device0)

    from custom_components.localthings.registry.by_type import resolve
    assert resolve(resources) is not None


async def test_options_flow_init_shows_menu(hass: HomeAssistant) -> None:
    """The options flow's entry point is now a menu (issue #54's debug
    panel lives alongside the remote-control settings), not the settings
    form directly."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f'localthings_{MOCK_SERIAL}')
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result['type'] == FlowResultType.MENU
    assert result['step_id'] == 'init'
    assert set(result['menu_options']) == {'settings', 'debug_write'}


async def test_options_flow_default_is_off(hass: HomeAssistant) -> None:
    """The bypass defaults to False, so devices that never touch this
    option see no change in the remote-control write block."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f'localthings_{MOCK_SERIAL}')
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'next_step_id': 'settings'}
    )

    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'settings'
    assert result['data_schema']({})[CONF_BYPASS_REMOTE_CONTROL] is False


async def test_options_flow_can_enable_bypass(hass: HomeAssistant) -> None:
    """Submitting the form with the toggle on stores it in entry.options,
    where coordinator.async_send_command reads it (issue #54)."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f'localthings_{MOCK_SERIAL}')
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'next_step_id': 'settings'}
    )
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={CONF_BYPASS_REMOTE_CONTROL: True}
    )

    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_BYPASS_REMOTE_CONTROL] is True


async def test_options_flow_reflects_previously_saved_value(hass: HomeAssistant) -> None:
    """Reopening the form shows the currently-saved choice as the default,
    not always False."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id=f'localthings_{MOCK_SERIAL}',
        options={CONF_BYPASS_REMOTE_CONTROL: True},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'next_step_id': 'settings'}
    )

    assert result['data_schema']({})[CONF_BYPASS_REMOTE_CONTROL] is True


async def test_options_flow_debug_write_shows_hrefs_from_coordinator(
    hass: HomeAssistant, mock_coordinator_session,
) -> None:
    """The debug panel's href dropdown is populated from the live
    coordinator's cached resources, not a static list."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f'localthings_{MOCK_SERIAL}')
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'next_step_id': 'debug_write'}
    )

    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'debug_write'


async def test_options_flow_debug_write_aborts_when_device_not_loaded(
    hass: HomeAssistant,
) -> None:
    """No coordinator in hass.data (device never finished loading) means
    the debug panel has nothing to write to or read from."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f'localthings_{MOCK_SERIAL}')
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'next_step_id': 'debug_write'}
    )

    assert result['type'] == FlowResultType.ABORT
    assert result['reason'] == 'not_loaded'


async def test_options_flow_debug_edit_writes_and_shows_result(
    hass: HomeAssistant, mock_coordinator_session,
) -> None:
    """Picking an href, then submitting a payload, drives
    coordinator.async_raw_write and lands on the result menu with the
    device's response."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f'localthings_{MOCK_SERIAL}')
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'next_step_id': 'debug_write'}
    )
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'href': '/washer/vs/0'},
    )
    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'debug_edit'

    with patch(
        'custom_components.localthings.coordinator.LocalThingsCoordinator.async_raw_write',
        return_value=(0x44, {'a': 1}),
    ):
        result = await hass.config_entries.options.async_configure(
            result['flow_id'], user_input={'payload': {'a': 1}},
        )

    assert result['type'] == FlowResultType.MENU
    assert result['step_id'] == 'debug_result'
    assert result['description_placeholders']['code'] == '2.04 (0x44)'


async def test_options_flow_debug_edit_rejects_empty_payload(
    hass: HomeAssistant, mock_coordinator_session,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=f'localthings_{MOCK_SERIAL}')
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'next_step_id': 'debug_write'}
    )
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'href': '/washer/vs/0'},
    )
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'payload': {}},
    )

    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'debug_edit'
    assert result['errors'] == {'payload': 'empty_payload'}


async def test_options_flow_finish_preserves_existing_options(
    hass: HomeAssistant, mock_coordinator_session,
) -> None:
    """Finishing from the debug-result menu must not clobber a previously
    saved remote-control bypass setting."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id=f'localthings_{MOCK_SERIAL}',
        options={CONF_BYPASS_REMOTE_CONTROL: True},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'next_step_id': 'debug_write'}
    )
    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'href': '/washer/vs/0'},
    )
    with patch(
        'custom_components.localthings.coordinator.LocalThingsCoordinator.async_raw_write',
        return_value=(0x44, {'a': 1}),
    ):
        result = await hass.config_entries.options.async_configure(
            result['flow_id'], user_input={'payload': {'a': 1}},
        )
    assert result['type'] == FlowResultType.MENU
    assert result['step_id'] == 'debug_result'

    result = await hass.config_entries.options.async_configure(
        result['flow_id'], user_input={'next_step_id': 'finish'}
    )

    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_BYPASS_REMOTE_CONTROL] is True


def test_is_placeholder_serial_catches_nothing_svc():
    """Issue #83: the ARTIK051_DONGLE_REF firmware family reports the
    literal string 'Nothing(SVC)' as serialNum on every unit -- non-empty,
    so it must be caught by name, not by the plain `if not serial` check."""
    from custom_components.localthings.config_flow import _is_placeholder_serial
    assert _is_placeholder_serial('Nothing(SVC)') is True
    assert _is_placeholder_serial('nothing(svc)') is True
    assert _is_placeholder_serial('  Nothing(SVC)  ') is True


def test_is_placeholder_serial_accepts_real_serials():
    from custom_components.localthings.config_flow import _is_placeholder_serial
    assert _is_placeholder_serial('0A1B2C3D4E5F') is False
    assert _is_placeholder_serial('') is False


import time


def test_race_returns_first_winner():
    """First worker to return a valid info dict wins; wall-clock << sequential."""
    from custom_components.localthings.config_flow import _race_handshake, _CertRejected

    def worker(port):
        if port == 49155:
            return {"port": port, "serial": "WINNER", "ok": True}
        # False-positive port: simulate the library's connect() TimeoutError.
        raise TimeoutError(f"DTLS handshake timeout to 10.0.0.1:{port}")

    t0 = time.monotonic()
    info, cert_rejected, _ = _race_handshake("10.0.0.1", [49154, 49155, 49153], worker)
    elapsed = time.monotonic() - t0
    assert info == {"port": 49155, "serial": "WINNER", "ok": True}
    assert cert_rejected is False
    assert elapsed < 1.0   # winner is instant; no 12 s false-positive penalty


def test_race_all_timeout_no_cert_rejected():
    """All workers timeout → (None, False); not a cert problem, so no fallback."""
    from custom_components.localthings.config_flow import _race_handshake

    def worker(port):
        raise TimeoutError(f"DTLS handshake timeout to 10.0.0.1:{port}")

    info, cert_rejected, _ = _race_handshake("10.0.0.1", [49154, 49155], worker)
    assert info is None
    assert cert_rejected is False


def test_race_all_cert_rejected_classified():
    """All workers raise _CertRejected → cert_rejected=True (triggers fallback upstream)."""
    from custom_components.localthings.config_flow import _race_handshake, _CertRejected

    def worker(port):
        raise _CertRejected(f"alert handshake failure from 10.0.0.1:{port}")

    info, cert_rejected, _ = _race_handshake("10.0.0.1", [49154, 49155], worker)
    assert info is None
    assert cert_rejected is True


def test_race_mixed_cert_and_timeout_not_all_cert():
    """Mix of cert-reject and timeout → cert_rejected=False (ambiguous, no fallback)."""
    from custom_components.localthings.config_flow import _race_handshake, _CertRejected

    def worker(port):
        if port == 49154:
            raise _CertRejected("alert handshake failure")
        raise TimeoutError("DTLS handshake timeout")

    info, cert_rejected, _ = _race_handshake("10.0.0.1", [49154, 49155], worker)
    assert info is None
    assert cert_rejected is False


async def test_probe_race_winner_with_fake_session(hass: HomeAssistant, monkeypatch) -> None:
    """_probe_and_validate races candidates and returns the winner's port/info."""
    import cbor2
    from custom_components.localthings import config_flow

    device0 = [
        {'rt': ['x.com.samsung.devcol']},
        {'href': '/information/vs/0', 'rep': {
            'x.com.samsung.da.modelNum': 'TP2X_REF_20K|00136643|...',
            'x.com.samsung.da.description': 'TP2X_REF_20K',
            'x.com.samsung.da.serialNum': 'FRIDGE-RACE-001',
        }},
        {'href': '/otninformation/vs/0', 'rep': {}},
    ]

    class _FakeSession:
        def __init__(self, host, port, cert_pem=None, key_pem=None):
            self.host, self.port = host, port
        def connect(self):
            if self.port != 49155:
                raise TimeoutError(f"DTLS handshake timeout to {self.host}:{self.port}")
        def start_reader(self):
            pass
        def get(self, path, timeout=15.0):
            return 0x45, cbor2.dumps(device0)
        def close(self):
            pass

    monkeypatch.setattr(config_flow, '_fetch_samsung_uuid', lambda: 'test-uuid')
    monkeypatch.setattr(
        config_flow, '_mint_leaf_cert',
        lambda ca_cert, ca_key, uuid: ('FULLCHAIN', 'LEAFKEY'),
    )
    monkeypatch.setattr(
        config_flow, '_find_live_ports',
        lambda host, ports, timeout: [49154, 49155, 49153],
    )
    monkeypatch.setattr(
        'smartthings_local.protocol.dtls_session.DtlsCoapSession', _FakeSession,
    )

    info = await hass.async_add_executor_job(
        config_flow._probe_and_validate, '10.0.0.254', 'CA', 'CAKEY',
    )
    assert info['port'] == 49155
    assert info['serial'] == 'FRIDGE-RACE-001'
    assert info['leaf_cert_pem'] == 'FULLCHAIN'
    assert info['device_type_recognized'] is True


async def test_cached_leaf_skips_fetch_and_mint(hass: HomeAssistant, monkeypatch) -> None:
    """When cached_leaf is provided, UUID fetch + leaf mint are not called."""
    import cbor2
    from custom_components.localthings import config_flow

    device0 = [
        {'href': '/information/vs/0', 'rep': {
            'x.com.samsung.da.modelNum': 'TP2X_REF_20K|x', 'x.com.samsung.da.description': 'TP2X_REF_20K',
            'x.com.samsung.da.serialNum': 'CACHED-001'}},
        {'href': '/otninformation/vs/0', 'rep': {}},
    ]

    class _FakeSession:
        def __init__(self, host, port, cert_pem=None, key_pem=None): self.port = port
        def connect(self): pass
        def start_reader(self): pass
        def get(self, path, timeout=15.0): return 0x45, cbor2.dumps(device0)
        def close(self): pass

    fetch_calls = []
    mint_calls = []
    monkeypatch.setattr(config_flow, '_fetch_samsung_uuid', lambda: fetch_calls.append(1) or 'UUID')
    monkeypatch.setattr(config_flow, '_mint_leaf_cert', lambda *a, **k: mint_calls.append(1) or ('X', 'Y'))
    monkeypatch.setattr(config_flow, '_find_live_ports', lambda host, ports, t: [49155])
    monkeypatch.setattr('smartthings_local.protocol.dtls_session.DtlsCoapSession', _FakeSession)

    info = await hass.async_add_executor_job(
        config_flow._probe_and_validate,
        '10.0.0.254', 'CA', 'CAKEY', ('CACHEDFULLCHAIN', 'CACHEDLEAFKEY'),
    )
    assert info['leaf_cert_pem'] == 'CACHEDFULLCHAIN'
    assert info['leaf_key_pem'] == 'CACHEDLEAFKEY'
    assert info['port'] == 49155
    assert fetch_calls == []   # UUID fetch skipped
    assert mint_calls == []    # mint skipped


async def test_second_device_passes_cached_leaf(hass: HomeAssistant, monkeypatch) -> None:
    """The has_creds branch passes the existing entry's leaf as cached_leaf."""
    from custom_components.localthings import config_flow
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from .conftest import ENTRY_DATA, MOCK_HOST

    existing = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    existing.add_to_hass(hass)

    captured = {}

    def fake_probe(host, ca_cert, ca_key, cached_leaf=None, hass=None, entry=None):
        captured['cached_leaf'] = cached_leaf
        captured['hass'] = hass
        captured['entry'] = entry
        return {'port': 49155, 'serial': 'SECOND-DEV', 'leaf_cert_pem': 'L', 'leaf_key_pem': 'K',
                'one_ui_version': '', 'device_type_recognized': True}

    monkeypatch.setattr(config_flow, '_probe_and_validate', fake_probe)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'user'})
    assert result['step_id'] == 'user_reuse'
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {CONF_HOST: MOCK_HOST},
    )
    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert captured['cached_leaf'] == (
        ENTRY_DATA[CONF_LEAF_CERT_PEM], ENTRY_DATA[CONF_LEAF_KEY_PEM],
    )
    assert captured['hass'] is hass
    assert captured['entry'] is existing


async def test_fallback_re_mint_on_all_cert_rejected(hass: HomeAssistant, monkeypatch) -> None:
    """Cached leaf, all workers cert-reject → fetch+mint once, second race succeeds."""
    import cbor2
    from custom_components.localthings import config_flow

    device0 = [
        {'rt': ['x.com.samsung.devcol']},
        {'href': '/information/vs/0', 'rep': {
            'x.com.samsung.da.modelNum': 'TP2X_REF_20K|x', 'x.com.samsung.da.description': 'TP2X_REF_20K',
            'x.com.samsung.da.serialNum': 'ROT-001'}},
        {'href': '/otninformation/vs/0', 'rep': {}},
    ]

    class _RejectSession:
        def __init__(self, host, port, cert_pem=None, key_pem=None):
            self.cert_pem = cert_pem
        def connect(self):
            raise ConnectionError("DTLS handshake error: alert handshake failure")
        def start_reader(self): pass
        def get(self, path, timeout=15.0): return 0x45, cbor2.dumps(device0)
        def close(self): pass

    class _OkSession:
        def __init__(self, host, port, cert_pem=None, key_pem=None):
            self.cert_pem = cert_pem
        def connect(self): pass
        def start_reader(self): pass
        def get(self, path, timeout=15.0): return 0x45, cbor2.dumps(device0)
        def close(self): pass

    # First race (cached leaf 'OLDFULLCHAIN'): every candidate cert-rejects.
    # Second race (re-minted leaf 'NEWCHAIN'): ok. Decision keyed on cert_pem
    # so it's deterministic under parallel worker construction (no shared counter).
    def factory(host, port, cert_pem=None, key_pem=None):
        if cert_pem == 'OLDFULLCHAIN':
            return _RejectSession(host, port, cert_pem, key_pem)
        return _OkSession(host, port, cert_pem, key_pem)
    monkeypatch.setattr('smartthings_local.protocol.dtls_session.DtlsCoapSession', factory)

    fetch_calls = []
    monkeypatch.setattr(config_flow, '_fetch_samsung_uuid', lambda: fetch_calls.append(1) or 'NEW-UUID')
    monkeypatch.setattr(config_flow, '_mint_leaf_cert', lambda *a, **k: ('NEWCHAIN', 'NEWKEY'))
    monkeypatch.setattr(config_flow, '_find_live_ports', lambda host, ports, t: [49154, 49155, 49153])
    # _persist_refreshed_leaf needs an entry; pass a MockConfigEntry.
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    entry = MockConfigEntry(domain=DOMAIN, data={**ENTRY_DATA, CONF_HOST: '10.0.0.254'})
    entry.add_to_hass(hass)
    monkeypatch.setattr(config_flow, '_persist_refreshed_leaf', lambda *a, **k: None)

    info = await hass.async_add_executor_job(
        config_flow._probe_and_validate,
        '10.0.0.254', 'CA', 'CAKEY', ('OLDFULLCHAIN', 'OLDLEAFKEY'), hass, entry,
    )
    assert info['serial'] == 'ROT-001'
    assert info['leaf_cert_pem'] == 'NEWCHAIN'   # re-minted leaf used
    assert fetch_calls == [1]                     # UUID fetched exactly once (fallback)


async def test_persist_refreshed_leaf_updates_entry(hass: HomeAssistant, monkeypatch) -> None:
    """E2E: the fresh-mint fallback persists the refreshed leaf to the
    existing entry. Does NOT monkeypatch _persist_refreshed_leaf, so the
    real `asyncio.run_coroutine_threadsafe(...).result()` path runs."""
    import cbor2
    from custom_components.localthings import config_flow
    from custom_components.localthings.const import (
        CONF_LEAF_CERT_PEM, CONF_LEAF_KEY_PEM,
    )

    device0 = [
        {'rt': ['x.com.samsung.devcol']},
        {'href': '/information/vs/0', 'rep': {
            'x.com.samsung.da.modelNum': 'TP2X_REF_20K|x',
            'x.com.samsung.da.description': 'TP2X_REF_20K',
            'x.com.samsung.da.serialNum': 'PERSIST-001'}},
        {'href': '/otninformation/vs/0', 'rep': {}},
    ]

    class _RejectSession:
        def __init__(self, host, port, cert_pem=None, key_pem=None):
            self.cert_pem = cert_pem
        def connect(self):
            raise ConnectionError("DTLS handshake error: alert handshake failure")
        def start_reader(self): pass
        def get(self, path, timeout=15.0): return 0x45, cbor2.dumps(device0)
        def close(self): pass

    class _OkSession:
        def __init__(self, host, port, cert_pem=None, key_pem=None):
            self.cert_pem = cert_pem
        def connect(self): pass
        def start_reader(self): pass
        def get(self, path, timeout=15.0): return 0x45, cbor2.dumps(device0)
        def close(self): pass

    def factory(host, port, cert_pem=None, key_pem=None):
        if cert_pem == 'OLDFULLCHAIN':
            return _RejectSession(host, port, cert_pem, key_pem)
        return _OkSession(host, port, cert_pem, key_pem)
    monkeypatch.setattr('smartthings_local.protocol.dtls_session.DtlsCoapSession', factory)

    monkeypatch.setattr(config_flow, '_fetch_samsung_uuid', lambda: 'NEW-UUID')
    monkeypatch.setattr(config_flow, '_mint_leaf_cert', lambda *a, **k: ('NEWCHAIN', 'NEWKEY'))
    monkeypatch.setattr(config_flow, '_find_live_ports', lambda host, ports, t: [49154, 49155, 49153])

    from pytest_homeassistant_custom_component.common import MockConfigEntry
    entry = MockConfigEntry(domain=DOMAIN, data={**ENTRY_DATA, CONF_HOST: '10.0.0.254'})
    entry.add_to_hass(hass)

    info = await hass.async_add_executor_job(
        config_flow._probe_and_validate,
        '10.0.0.254', 'CA', 'CAKEY', ('OLDFULLCHAIN', 'OLDLEAFKEY'), hass, entry,
    )
    await hass.async_block_till_done()

    assert info['leaf_cert_pem'] == 'NEWCHAIN'
    assert entry.data[CONF_LEAF_CERT_PEM] == 'NEWCHAIN'
    assert entry.data[CONF_LEAF_KEY_PEM] == 'NEWKEY'


async def test_no_fallback_on_timeout(hass: HomeAssistant, monkeypatch) -> None:
    """Cached leaf, all workers timeout → no re-mint, CannotConnect."""
    from custom_components.localthings import config_flow
    from custom_components.localthings.config_flow import CannotConnect

    class _TimeoutSession:
        def __init__(self, host, port, cert_pem=None, key_pem=None): pass
        def connect(self): raise TimeoutError("DTLS handshake timeout")
        def start_reader(self): pass
        def get(self, path, timeout=15.0): raise AssertionError("should not reach GET")
        def close(self): pass
    monkeypatch.setattr('smartthings_local.protocol.dtls_session.DtlsCoapSession', _TimeoutSession)
    fetch_calls = []
    monkeypatch.setattr(config_flow, '_fetch_samsung_uuid', lambda: fetch_calls.append(1) or 'UUID')
    monkeypatch.setattr(config_flow, '_mint_leaf_cert', lambda *a, **k: ('X', 'Y'))
    monkeypatch.setattr(config_flow, '_find_live_ports', lambda host, ports, t: [49154, 49155])

    with pytest.raises(CannotConnect):
        await hass.async_add_executor_job(
            config_flow._probe_and_validate,
            '10.0.0.254', 'CA', 'CAKEY', ('OLDCHAIN', 'OLDKEY'), hass, None,
        )
    assert fetch_calls == []   # no UUID fetch (timeout is not a cert problem)


async def test_no_fallback_on_mixed_errors(hass: HomeAssistant, monkeypatch) -> None:
    """Cached leaf, mix of cert-reject + timeout → no re-mint, CannotConnect."""
    import cbor2
    from custom_components.localthings import config_flow
    from custom_components.localthings.config_flow import CannotConnect

    class _MixSession:
        def __init__(self, host, port, cert_pem=None, key_pem=None): self.port = port
        def connect(self):
            if self.port == 49154:
                raise ConnectionError("DTLS handshake error: alert handshake failure")
            raise TimeoutError("DTLS handshake timeout")
        def start_reader(self): pass
        def get(self, path, timeout=15.0): return 0x45, cbor2.dumps([])
        def close(self): pass
    monkeypatch.setattr('smartthings_local.protocol.dtls_session.DtlsCoapSession', _MixSession)
    fetch_calls = []
    monkeypatch.setattr(config_flow, '_fetch_samsung_uuid', lambda: fetch_calls.append(1) or 'UUID')
    monkeypatch.setattr(config_flow, '_mint_leaf_cert', lambda *a, **k: ('X', 'Y'))
    monkeypatch.setattr(config_flow, '_find_live_ports', lambda host, ports, t: [49154, 49155])

    with pytest.raises(CannotConnect):
        await hass.async_add_executor_job(
            config_flow._probe_and_validate,
            '10.0.0.254', 'CA', 'CAKEY', ('OLDCHAIN', 'OLDKEY'), hass, None,
        )
    assert fetch_calls == []   # mix → no fallback
