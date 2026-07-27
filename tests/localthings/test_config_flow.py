"""Tests for the localthings config flow."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_BYPASS_REMOTE_CONTROL, CONF_CA_CERT_PEM, CONF_CA_KEY_PEM,
    CONF_HOST, CONF_PORT, DOMAIN,
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
    assert result['description_placeholders']['one_ui_version'] == '9.0 Space Heater'

    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {},
    )
    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert result['data'][CONF_HOST] == MOCK_HOST


async def test_unknown_type_without_version_uses_localized_step(
    hass: HomeAssistant,
) -> None:
    """No English placeholder sentinel leaks into a translated description."""
    probe_result = {
        'port': MOCK_PORT,
        'serial': MOCK_SERIAL,
        'leaf_cert_pem': 'leaf cert',
        'leaf_key_pem': 'leaf key',
        'one_ui_version': '',
        'device_type_recognized': False,
    }
    with patch(
        'custom_components.localthings.config_flow._probe_and_validate',
        return_value=probe_result,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': 'user'}
        )
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'],
            {
                CONF_HOST: MOCK_HOST,
                CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM,
                CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM,
            },
        )

    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'confirm_unknown_type_no_version'
    assert not result.get('description_placeholders')


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
    """A washer's probe response (no oneUiVersion) must still resolve via
    the modelNum/description fallback so setup doesn't warn about an
    unrecognized device type."""
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

    info_resource = resources.get('/information/vs/0', {})
    one_ui_version = (
        resources.get('/otninformation/vs/0', {}).get('swVersionInfo', {}).get('oneUiVersion', '')
    )
    from custom_components.localthings.registry.by_type import for_device, for_device_by_model
    recognized = bool(
        (one_ui_version and for_device(one_ui_version) is not None)
        or for_device_by_model(
            info_resource.get('x.com.samsung.da.modelNum', ''),
            info_resource.get('x.com.samsung.da.description', ''),
        ) is not None
    )
    assert recognized is True


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
