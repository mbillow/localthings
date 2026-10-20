"""Read-only Power on the measured NV51K7770S and its registry transition."""

from copy import deepcopy

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_oven_power_switches
from custom_components.localthings.binary_sensor import LocalThingsBinarySensor
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.adapter import _key, flatten
from custom_components.localthings.registry.by_type import oven as oven_registry
from custom_components.localthings.registry.capabilities import common, oven
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import BinarySensorDesc, SwitchDesc
from custom_components.localthings.registry.subdevices import MAIN
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401 -- installs the fake legacy transport
    _lower,
    _requests,
    _routes,
)


def _power(coordinator):
    return [
        b
        for b in coordinator.bound
        if b.desc.key == "power_switch" and _is_included(b, coordinator)
    ]


@pytest.mark.parametrize("divided", [False, True])
async def test_power_reports_idle_and_live_state_without_a_control(hass, divided):
    coordinator = await _discovered(hass, _routes(divided=divided))
    power = _power(coordinator)
    cavities = {MAIN, _lower(coordinator)} if divided else {MAIN}
    measured = [b for b in power if b.subdevice in cavities]
    assert {b.subdevice for b in measured} == cavities
    assert len(measured) == len(cavities)
    before_requests = _requests()
    for bound in measured:
        assert isinstance(bound.desc, BinarySensorDesc)
        entity = LocalThingsBinarySensor(coordinator, bound)
        assert entity.device_class == "power"
        assert entity.translation_key == "power_switch"
        resources = coordinator.canonical_resources(bound.subdevice)
        assert resources["/operational/state/vs/0"][PREFIX + "state"] == "Ready"
        assert resources["/mode/vs/0"][PREFIX + "modes"] == ["NoOperation"]
        coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.last_resources))
        assert entity.is_on is True  # The fixture reports On without a cook running.
        for raw, expected in (("Off", False), ("On", True), (None, None), ("Unknown", None)):
            coordinator._observe.apply(bound.href, {PREFIX + "power": raw}, source="poll")
            coordinator.async_set_updated_data(
                flatten(coordinator.bound, coordinator.last_resources)
            )
            assert entity.is_on is expected
    assert _requests() == before_requests


@pytest.mark.parametrize(
    "model", [None, 16, "LCD_OV_WALL", "LCD_OV_WALL_16K_OTHER", "LCD_OV_WALL_20K", "NV7000BS"]
)
async def test_other_oven_boards_keep_the_existing_switch(hass, model):
    coordinator = await _discovered(hass)
    resources = deepcopy(coordinator.canonical_resources(MAIN))
    resources["/information/vs/0"][PREFIX + "modelNum"] = model
    bound = discover(resources, oven_registry.REGISTRY.capabilities)
    power = [
        b
        for b in bound
        if b.desc.key == "power_switch"
        and (b.desc.exists_fn is None or b.desc.exists_fn(resources[b.href], resources))
    ]
    assert len(power) == 1
    assert isinstance(power[0].desc, SwitchDesc)
    assert power[0].desc in common.POWER_VS_FALLBACK.entities


async def test_native_power_precedence_and_shared_laundry_rules_are_unchanged(hass):
    coordinator = await _discovered(hass)
    resources = deepcopy(coordinator.canonical_resources(MAIN))
    resources["/power/0"] = {"value": True}
    bound = discover(resources, oven_registry.REGISTRY.capabilities)
    assert {b.href for b in bound if b.desc.key == "power_switch"} == {"/power/0"}
    assert not any(b.capability is oven.OVEN_POWER_STATUS for b in bound)
    for allowed in (True, False):
        resources["/wm/setinfo/vs/0"] = {PREFIX + "isModelSettingPowerOnOff": str(allowed)}
        for capability in common.POWER:
            included = [
                d for d in capability.entities if d.exists_fn is None or d.exists_fn({}, resources)
            ]
            assert len(included) == 1
            assert isinstance(included[0], SwitchDesc if allowed else BinarySensorDesc)


@pytest.mark.parametrize("disabled", [False, True])
async def test_cleanup_removes_exact_renamed_switches_and_is_idempotent(hass, disabled, caplog):
    coordinator = await _discovered(hass)
    registry = er.async_get(hass)
    removed = []
    kept = []
    for i, bound in enumerate(_power(coordinator)):
        assert isinstance(bound.desc, BinarySensorDesc)
        uid = LocalThingsBinarySensor(coordinator, bound).unique_id
        assert uid is not None
        old = registry.async_get_or_create(
            "switch",
            DOMAIN,
            uid,
            config_entry=coordinator._entry,
            disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
        )
        old = registry.async_update_entity(old.entity_id, new_entity_id=f"switch.renamed_power_{i}")
        removed.append(old.entity_id)
        replacement = registry.async_get_or_create(
            "binary_sensor", DOMAIN, uid, config_entry=coordinator._entry
        )
        kept.append(replacement.entity_id)
        unrelated = registry.async_get_or_create(
            "switch", DOMAIN, uid + "_other", config_entry=coordinator._entry
        )
        kept.append(unrelated.entity_id)
    foreign = registry.async_get_or_create(
        "switch", "other_integration", removed[0], config_entry=coordinator._entry
    )
    kept.append(foreign.entity_id)
    with caplog.at_level("INFO", logger="custom_components.localthings"):
        _drop_oven_power_switches(hass, coordinator._entry, coordinator)
        _drop_oven_power_switches(hass, coordinator._entry, coordinator)
    assert len(removed) == 2
    assert all(registry.async_get(entity_id) is None for entity_id in removed)
    assert all(registry.async_get(entity_id) is not None for entity_id in kept)
    for entity_id in removed:
        assert caplog.text.count(f"Removing {entity_id},") == 1
    assert "update any automation or dashboard" in caplog.text


@pytest.mark.parametrize("guard", ["other_entry", "other_model", "missing_power", "missing_field"])
async def test_cleanup_requires_owning_entry_and_a_discovered_replacement(hass, guard):
    coordinator = await _discovered(hass)
    bound = next(b for b in _power(coordinator) if b.subdevice == MAIN)
    registry = er.async_get(hass)
    entry = coordinator._entry
    if guard == "other_entry":
        entry = MockConfigEntry(domain=DOMAIN)
        entry.add_to_hass(hass)
    old = registry.async_get_or_create(
        "switch", DOMAIN, f"{DOMAIN}_{coordinator.device_key}_{_key(bound)}", config_entry=entry
    )
    if guard != "other_entry":
        resources = deepcopy(coordinator.last_resources)
        if guard == "other_model":
            resources["/information/vs/0"][PREFIX + "modelNum"] = "LCD_OV_WALL_20K"
        elif guard == "missing_power":
            del resources[bound.href]
        else:
            resources[bound.href] = {"rt": ["x.com.samsung.da.power"]}
        coordinator._run_discovery(resources, from_snapshot=True)
        coordinator._rehydrate_resources = resources
    _drop_oven_power_switches(hass, coordinator._entry, coordinator)
    assert registry.async_get(old.entity_id) is not None


async def test_offline_snapshot_restores_status_without_using_stale_values(hass):
    online = await _discovered(hass)
    await online._async_save_snapshot(online.last_resources, online.subdevices)
    restored = LocalThingsCoordinator(hass, online._entry)
    assert await restored.async_rehydrate()
    assert restored.last_resources == {}
    assert {_key(b) for b in _power(restored)} == {_key(b) for b in _power(online)}
    registry = er.async_get(hass)
    for bound in _power(restored):
        assert isinstance(bound.desc, BinarySensorDesc)
        entity = LocalThingsBinarySensor(restored, bound)
        assert entity.is_on is None
        assert entity.unique_id is not None
        registry.async_get_or_create("switch", DOMAIN, entity.unique_id, config_entry=online._entry)
    _drop_oven_power_switches(hass, online._entry, restored)
    assert not [
        e
        for e in er.async_entries_for_config_entry(registry, online._entry.entry_id)
        if e.domain == "switch"
    ]


@pytest.mark.parametrize(
    "name",
    ["microwave_nw9300md", "range_tp1x_da_ks_range_0101x", "oven_tp2x_ks_walloven"],
)
async def test_previously_affected_appliances_keep_power_switches(hass, name):
    from tests.test_subdevice_discovery import _coordinator, _discover

    coordinator = _coordinator(hass)
    await _discover(coordinator, name)
    power = _power(coordinator)
    assert power
    registry = er.async_get(hass)
    controls = []
    for bound in power:
        assert isinstance(bound.desc, SwitchDesc)
        assert bound.capability is not oven.OVEN_POWER_STATUS
        old = registry.async_get_or_create(
            "switch",
            DOMAIN,
            f"{DOMAIN}_{coordinator.device_key}_{_key(bound)}",
            config_entry=coordinator._entry,
        )
        controls.append(old.entity_id)
    _drop_oven_power_switches(hass, coordinator._entry, coordinator)
    assert all(registry.async_get(entity_id) is not None for entity_id in controls)
    state = flatten(coordinator.bound, coordinator.last_resources)
    assert "sound" in state
    assert not any(key.endswith("reservoir_open") for key in state)
