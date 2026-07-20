"""Tests for the localthings config flow."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_CA_CERT_PEM, CONF_CA_KEY_PEM, CONF_HOST, CONF_PORT, DOMAIN,
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


def test_find_live_ports_detects_silent_port() -> None:
    """The UDP liveness sweep flags a bound-but-silent port as live and drops
    ports that refuse with ICMP port-unreachable.

    A bound, never-recv'd UDP socket stands in for a device that listens but
    stays silent (open|filtered), like the dishwasher in issue #13 on 49153.
    Two sibling ports are reserved then closed so loopback refuses datagrams
    to them, standing in for the closed ports the scan should discard.
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
