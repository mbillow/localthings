"""Cabinet light availability follows door updates without removing the entity."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.capabilities.fridge import _cabinet_light_available


def _entity_id(hass, entry, key):
    return next(
        entity.entity_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if entity.unique_id.endswith(f"_{key}")
    )


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


@pytest.mark.parametrize("compartment", ["cooler", "freezer", "cvroom"])
async def test_cabinet_light_recovers_after_door_closes(
    hass: HomeAssistant, mock_entry, mock_coordinator_session, fridge_resources, compartment
) -> None:
    door_href = f"/door/{compartment}/0"
    fridge_resources[door_href] = {"openState": "Close"}
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    light = _entity_id(hass, mock_entry, "cabinet_light_switch")
    gradual = _entity_id(hass, mock_entry, "cabinet_light_dim")
    brightness = _entity_id(hass, mock_entry, "day_brightness")
    brightness_state = _state(hass, brightness)
    assert _state(hass, light) == "off"

    # A duplicate aggregate report must not keep the control unavailable
    # after an individual door notification reports that it closed.
    coordinator._observe.apply(
        "/doors/vs/0",
        {"x.com.samsung.da.items": [{"x.com.samsung.da.openState": "Open"}]},
        source="observe",
    )
    for door_state, light_state in (("Open", "unavailable"), ("Close", "off")):
        coordinator._observe.apply(door_href, {"openState": door_state}, source="observe")
        await hass.async_block_till_done()
        assert _state(hass, light) == light_state
        assert _state(hass, gradual) == "on"
        assert _state(hass, brightness) == brightness_state
        assert _entity_id(hass, mock_entry, "cabinet_light_switch") == light

    coordinator.async_set_update_error(UpdateFailed("offline"))
    await hass.async_block_till_done()
    assert _state(hass, light) == "unavailable"
    assert _state(hass, gradual) == "unavailable"


@pytest.mark.parametrize(
    ("resources", "available"),
    [
        ({}, True),
        ({"/door/cooler/0": {}}, True),
        ({"/door/cooler/0": {"x.com.samsung.da.openState": "Open"}}, False),
        (
            {"/doors/vs/0": {"x.com.samsung.da.items": [{"x.com.samsung.da.openState": "Open"}]}},
            False,
        ),
        (
            {
                "/door/cooler/0": {},
                "/doors/vs/0": {"x.com.samsung.da.items": [{"x.com.samsung.da.openState": "Open"}]},
            },
            False,
        ),
        (
            {"/doors/vs/0": {"x.com.samsung.da.items": [{"x.com.samsung.da.openState": "Close"}]}},
            True,
        ),
    ],
)
def test_cabinet_light_uses_reported_door_state(resources, available):
    assert _cabinet_light_available({}, resources) is available
