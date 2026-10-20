"""Hide only the verified oven's absent Sound option."""

from copy import deepcopy

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_unsupported_oven_sound_switches
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.adapter import _key, flatten
from custom_components.localthings.registry.by_type import oven as oven_registry
from custom_components.localthings.registry.capabilities import oven
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SwitchDesc
from custom_components.localthings.registry.subdevices import MAIN
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    UPPER,
    _discovered,
    _fake_https,  # noqa: F401 -- fake legacy transport
    _requests,
    _routes,
)


def _sound(coordinator):
    return next(b for b in coordinator.bound if b.subdevice == MAIN and b.desc.key == "sound")


@pytest.mark.parametrize("divided", [False, True])
async def test_captured_oven_has_no_sound_control_and_keeps_energy_saving(hass, divided):
    coordinator = await _discovered(hass, _routes(divided=divided))
    included = [b for b in coordinator.bound if _is_included(b, coordinator)]
    assert not any(b.desc.key == "sound" for b in included)
    energy = next(b for b in included if b.desc.key == "energy_saving")
    original = next(d for d in oven.OVEN_MODE.entities if d.key == "energy_saving")
    assert energy.desc is original
    state = flatten(coordinator.bound, coordinator.last_resources)
    assert state["energy_saving"] is True
    assert "diagnosis_status" in state
    assert any(b.desc.key == "diagnosis_start" for b in included)
    assert not any(method == "PUT" for method, _ in _requests())


@pytest.mark.parametrize("token,expected", [("Sound_On", True), ("Sound_Off", False)])
async def test_an_explicit_sound_token_keeps_the_existing_control(hass, token, expected):
    upper = deepcopy(UPPER)
    upper["Mode"]["options"].append(token)
    coordinator = await _discovered(hass, _routes(upper=upper))
    sound = _sound(coordinator)
    assert _is_included(sound, coordinator)
    assert flatten(coordinator.bound, coordinator.last_resources)["sound"] is expected
    original = next(d for d in oven.OVEN_MODE.entities if d.key == "sound")
    assert isinstance(original, SwitchDesc)
    assert sound.desc.write_fn is original.write_fn
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{DOMAIN}_{coordinator.device_key}_sound",
        config_entry=coordinator._entry,
    )
    _drop_unsupported_oven_sound_switches(hass, coordinator._entry, coordinator)
    assert registry.async_get(old.entity_id) is not None


@pytest.mark.parametrize(
    "model", [None, 16, "LCD_OV_WALL", "LCD_OV_WALL_16K_OTHER", "LCD_OV_WALL_20K"]
)
async def test_other_boards_keep_the_unchanged_sound_description(hass, model):
    coordinator = await _discovered(hass)
    resources = deepcopy(coordinator.canonical_resources(MAIN))
    resources["/information/vs/0"][PREFIX + "modelNum"] = model
    bound = discover(resources, oven_registry.REGISTRY.capabilities)
    sound = next(b for b in bound if b.desc.key == "sound")
    assert sound.desc is next(d for d in oven.OVEN_MODE.entities if d.key == "sound")
    assert "sound" in flatten(bound, resources)


@pytest.mark.parametrize("disabled", [False, True])
async def test_cleanup_removes_only_the_exact_sound_switch(hass, disabled, caplog):
    coordinator = await _discovered(hass)
    registry = er.async_get(hass)
    uid = f"{DOMAIN}_{coordinator.device_key}_sound"
    old = registry.async_get_or_create(
        "switch",
        DOMAIN,
        uid,
        config_entry=coordinator._entry,
        disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
    )
    old = registry.async_update_entity(old.entity_id, new_entity_id="switch.custom_sound_name")
    keep = [
        registry.async_get_or_create(platform, domain, unique_id, config_entry=coordinator._entry)
        for platform, domain, unique_id in (
            ("sensor", DOMAIN, uid),
            ("switch", DOMAIN, uid + "_other"),
            ("switch", "other_integration", uid),
            ("switch", DOMAIN, f"{DOMAIN}_{coordinator.device_key}_energy_saving"),
        )
    ]
    with caplog.at_level("INFO", logger="custom_components.localthings"):
        _drop_unsupported_oven_sound_switches(hass, coordinator._entry, coordinator)
        _drop_unsupported_oven_sound_switches(hass, coordinator._entry, coordinator)
    assert registry.async_get(old.entity_id) is None
    assert all(registry.async_get(e.entity_id) for e in keep)
    assert caplog.text.count(f"Removing {old.entity_id}:") == 1


@pytest.mark.parametrize(
    "rep",
    [{}, {"rt": ["x.com.samsung.da.mode"]}, {PREFIX + "options": None}, {PREFIX + "options": ""}],
)
async def test_cleanup_preserves_switch_without_a_complete_options_list(hass, rep):
    coordinator = await _discovered(hass)
    resources = deepcopy(coordinator.last_resources)
    resources["/mode/vs/0"] = rep
    coordinator._run_discovery(resources, from_snapshot=True)
    coordinator._rehydrate_resources = resources
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{DOMAIN}_{coordinator.device_key}_sound",
        config_entry=coordinator._entry,
    )
    _drop_unsupported_oven_sound_switches(hass, coordinator._entry, coordinator)
    assert registry.async_get(old.entity_id) is not None


async def test_cleanup_leaves_another_config_entry_untouched(hass):
    coordinator = await _discovered(hass)
    other = MockConfigEntry(domain=DOMAIN)
    other.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "switch", DOMAIN, f"{DOMAIN}_{coordinator.device_key}_sound", config_entry=other
    )
    _drop_unsupported_oven_sound_switches(hass, coordinator._entry, coordinator)
    assert registry.async_get(old.entity_id) is not None


async def test_offline_snapshot_applies_the_same_sound_gate(hass):
    online = await _discovered(hass)
    await online._async_save_snapshot(online.last_resources, online.subdevices)
    restored = LocalThingsCoordinator(hass, online._entry)
    assert await restored.async_rehydrate()
    assert restored.last_resources == {}
    assert not _is_included(_sound(restored), restored)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "switch", DOMAIN, f"{DOMAIN}_{restored.device_key}_sound", config_entry=online._entry
    )
    _drop_unsupported_oven_sound_switches(hass, online._entry, restored)
    assert registry.async_get(old.entity_id) is None


@pytest.mark.parametrize(
    "name", ["microwave_nw9300md", "range_tp1x_da_ks_range_0101x", "oven_tp2x_ks_walloven"]
)
async def test_previously_affected_appliances_keep_their_sound_controls(hass, name):
    from tests.test_subdevice_discovery import _coordinator, _discover

    coordinator = _coordinator(hass)
    await _discover(coordinator, name)
    sound = [b for b in coordinator.bound if b.desc.key == "sound" and _is_included(b, coordinator)]
    assert sound
    registry = er.async_get(hass)
    controls = [
        registry.async_get_or_create(
            "switch",
            DOMAIN,
            f"{DOMAIN}_{coordinator.device_key}_{_key(b)}",
            config_entry=coordinator._entry,
        )
        for b in sound
    ]
    _drop_unsupported_oven_sound_switches(hass, coordinator._entry, coordinator)
    assert all(registry.async_get(e.entity_id) for e in controls)
    assert all(b.desc.exists_fn is None for b in sound)
