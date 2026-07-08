"""Tests for the diagnostics platform."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.diagnostics import async_get_config_entry_diagnostics
from custom_components.localthings.registry.redact import REDACTED


async def test_diagnostics_shape_and_redaction(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_entry)

    assert diagnostics["device_type"] == 'refrigerator'
    assert diagnostics["one_ui_version"] == '7.0 Refrigerator'
    assert diagnostics["unbound_hrefs"] == []
    assert diagnostics["integration_version"] == '0.1.0'
    assert diagnostics["smartthings_local_version"]

    resources = diagnostics["resources"]
    assert resources['/information/vs/0']['x.com.samsung.da.serialNum'] == REDACTED
    assert resources['/wirelessinfo/vs/0']['macaddressWiFi'] == REDACTED
    # Ordinary state survives.
    assert resources['/status/lock/vs/0']['x.com.samsung.da.ado.devicecontrol'] == 'On'
