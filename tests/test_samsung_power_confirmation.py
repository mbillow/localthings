"""ARTIK051's erroneous power refusal must be checked against the appliance."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import CONF_HOST, CONF_MODEL, DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.transport import Transport

POWER = "x.com.samsung.da.power"


@pytest.fixture
def power_coordinator(hass, monkeypatch):
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "192.0.2.1", CONF_MODEL: "ARTIK051_PRAC_20K"}
    )
    entry.add_to_hass(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    session = MagicMock()
    session.write.return_value = (0x80, None)
    coordinator._session = cast(Transport, session)
    monkeypatch.setattr("custom_components.localthings.coordinator.asyncio.sleep", AsyncMock())
    return coordinator, session


@pytest.mark.parametrize("power", ["On", "Off"])
async def test_bad_request_is_accepted_only_after_live_confirmation(power_coordinator, power):
    coordinator, session = power_coordinator
    session.read.return_value = (0x45, {POWER: power})
    await coordinator._async_put(["power", "vs", "0"], {POWER: power}, "/power/vs/0")
    session.read.assert_called_once_with(["power", "vs", "0"], timeout=5.0)
    session.write.assert_called_once()


@pytest.mark.parametrize(
    "readback", [(0x45, {POWER: "Off"}), (0x80, {POWER: "On"}), (0x45, None), (0x45, {})]
)
async def test_unconfirmed_write_is_still_refused(power_coordinator, readback):
    coordinator, session = power_coordinator
    coordinator._observe.apply("/power/vs/0", {POWER: "On"}, source="poll")
    session.read.return_value = readback
    with pytest.raises(HomeAssistantError):
        await coordinator._async_put(["power", "vs", "0"], {POWER: "On"}, "/power/vs/0")


async def test_failed_read_is_not_success(power_coordinator):
    coordinator, session = power_coordinator
    session.read.side_effect = TimeoutError
    with pytest.raises(HomeAssistantError):
        await coordinator._async_put(["power", "vs", "0"], {POWER: "On"}, "/power/vs/0")


@pytest.mark.parametrize(
    ("code", "model", "href", "body"),
    [
        (0x83, "ARTIK051_PRAC_20K", "/power/vs/0", {POWER: "On"}),
        (0x80, "TP6X_RAC_16K", "/power/vs/0", {POWER: "On"}),
        (0x80, "ARTIK051_PRAC_20K", "/mode/vs/0", {POWER: "On"}),
        (0x80, "ARTIK051_PRAC_20K", "/power/vs/0", {POWER: "On", "extra": True}),
    ],
)
async def test_other_refusals_are_unchanged(power_coordinator, hass, code, model, href, body):
    coordinator, session = power_coordinator
    hass.config_entries.async_update_entry(
        coordinator._entry, data={CONF_HOST: "192.0.2.1", CONF_MODEL: model}
    )
    session.write.return_value = (code, None)
    with pytest.raises(HomeAssistantError):
        await coordinator._async_put(href.strip("/").split("/"), body, href)
    session.read.assert_not_called()


async def test_accepted_write_needs_no_workaround(power_coordinator):
    coordinator, session = power_coordinator
    session.write.return_value = (0x44, None)
    await coordinator._async_put(["power", "vs", "0"], {POWER: "On"}, "/power/vs/0")
    session.read.assert_not_called()
