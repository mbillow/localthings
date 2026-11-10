"""Drum lamps are late-discovered lights, with independent non-heating writes."""

from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from tests.conftest import _load_device

from .conftest import FakeObserveSession
from .test_offline_setup import _reachable

HREF = "/course/vs/0"
OPTIONS = "x.com.samsung.da.options"


@pytest.mark.parametrize("family", ["washer", "dryer"])
async def test_lamp_late_arrival_absence_and_return_preserve_identity(hass, mock_entry, family):
    resources = _load_device(family)
    resources[HREF] = {OPTIONS: ["Course_16"]}
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        registry = er.async_get(hass)
        uid = f"{DOMAIN}_{coordinator.device_key}_drum_light"
        assert registry.async_get_entity_id("light", DOMAIN, uid) is None
        coordinator._cache.apply_rep(
            HREF, {OPTIONS: ["Course_16", "DrumLight_On"]}, source="observe"
        )
        await hass.async_block_till_done()
        entity_id = registry.async_get_entity_id("light", DOMAIN, uid)
        assert entity_id is not None
        assert hass.states.get(entity_id).state == "on"
        registry.async_update_entity(entity_id, name="My lamp")
        coordinator._cache.apply_rep(HREF, {OPTIONS: ["Course_16"]}, source="observe")
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "unavailable"
        coordinator._cache.apply_rep(
            HREF, {OPTIONS: ["Course_16", "DrumLight_Off"]}, source="observe"
        )
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "off"
        assert registry.async_get(entity_id).name == "My lamp"
        assert registry.async_get_entity_id("light", DOMAIN, uid) == entity_id
        assert registry.async_get_entity_id("switch", DOMAIN, uid) is None


@pytest.mark.parametrize("family", ["washer", "dryer"])
@pytest.mark.parametrize("confirmed", [True, False])
async def test_light_service_works_without_smart_control_and_confirms(
    hass, mock_entry, family, confirmed
):
    resources = _load_device(family)
    resources[HREF] = {OPTIONS: ["Course_16", "DrumLight_Off", "WrinklePrevent_On"]}
    resources["/remotectrl/vs/0"] = {"x.com.samsung.da.remoteControlEnabled": "false"}
    resources["/wm/setinfo/vs/0"]["x.com.samsung.da.isModelSettingWithoutSC"] = "false"
    resources["/remotectrl/0"] = {"value": False}
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        # Explicitly model a board that does not waive Smart Control itself.
        for href, rep in resources.items():
            if "x.com.samsung.da.isModelSettingWithoutSC" in rep:
                coordinator._cache.apply_rep(
                    href, {"x.com.samsung.da.isModelSettingWithoutSC": False}, source="test"
                )
        fake = FakeObserveSession()
        coordinator._session = fake
        uid = f"{DOMAIN}_{coordinator.device_key}_drum_light"
        entity_id = er.async_get(hass).async_get_entity_id("light", DOMAIN, uid)
        after = {
            OPTIONS: [
                "Course_16",
                "DrumLight_On" if confirmed else "DrumLight_Off",
                "WrinklePrevent_On",
            ]
        }
        with (
            patch.object(fake, "read", return_value=(0x45, after)),
            patch.object(LocalThingsCoordinator, "_CONFIRM_DELAY_S", 0),
        ):
            if confirmed:
                await hass.services.async_call(
                    "light", "turn_on", {"entity_id": entity_id}, blocking=True
                )
                assert hass.states.get(entity_id).state == "on"
                assert set(coordinator.cached_resource(HREF)[OPTIONS]) == {
                    "Course_16",
                    "WrinklePrevent_On",
                    "DrumLight_On",
                }
            else:
                with pytest.raises(HomeAssistantError) as err:
                    await hass.services.async_call(
                        "light", "turn_on", {"entity_id": entity_id}, blocking=True
                    )
                assert err.value.translation_key == "command_not_confirmed"
            assert fake.writes == [(["course", "vs", "0"], {OPTIONS: ["DrumLight_On"]})]
            # A cycle write retains its Smart Control gate.
            cycle = next(b for b in coordinator.bound if b.desc.key == "cycle")
            with pytest.raises(ServiceValidationError) as err:
                await coordinator.async_send_command(cycle, "16")
            assert err.value.translation_key == "remote_control_disabled"
