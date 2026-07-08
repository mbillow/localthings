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
