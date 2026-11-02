"""Stable Main/Lower device labels preserve registry identities and user names."""

from copy import copy
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.localthings.const import CONF_DEVICE_KEY, CONF_DISABLED_SUBDEVICES, DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.subdevices import MAIN
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401 -- fake legacy transport
    _requests,
    _routes,
)
from tests.test_legacy_http_nw9300md import _routes as _combo_routes
from tests.test_subdevice_discovery import _coordinator, _discover


async def _load(hass, monkeypatch, coordinator):
    await hass.async_block_till_done()
    entry = coordinator._entry
    hass.config_entries.async_update_entry(
        entry, version=4, data={**entry.data, CONF_DEVICE_KEY: coordinator.device_key}
    )
    monkeypatch.setattr(
        "custom_components.localthings.LocalThingsCoordinator", lambda *args: coordinator
    )
    monkeypatch.setattr(coordinator, "async_load_mode_specs", AsyncMock())
    monkeypatch.setattr(coordinator, "async_config_entry_first_refresh", AsyncMock())
    monkeypatch.setattr(coordinator, "async_close", AsyncMock())
    monkeypatch.setattr(coordinator, "async_refresh", AsyncMock())
    monkeypatch.setattr(
        coordinator, "async_rehydrate", AsyncMock(return_value=coordinator.rehydrated)
    )
    if coordinator.rehydrated:
        coordinator._publish_data()
    else:
        coordinator.async_set_updated_data(
            flatten(coordinator.bound, coordinator.entity_resources())
        )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _rows(hass, coordinator):
    prefix = f"{DOMAIN}_{coordinator.device_key}_"
    return {
        (row.domain, row.unique_id.removeprefix(prefix)): row
        for row in er.async_entries_for_config_entry(
            er.async_get(hass), coordinator._entry.entry_id
        )
    }


async def _set_divider(hass, coordinator, state):
    coordinator._observe.apply("/connected/vs/1", {PREFIX + "connected": state}, source="poll")
    coordinator._publish_data()
    await hass.async_block_till_done()


@pytest.mark.parametrize("initially_divided", [False, True])
@pytest.mark.parametrize("custom_device_names", [False, True])
async def test_upgrade_keeps_ids_and_names_stable_through_divider_changes(
    hass, monkeypatch, initially_divided, custom_device_names
):
    coordinator = await _discovered(hass, _routes(divided=initially_divided))
    device_info_for = coordinator.device_info_for
    with monkeypatch.context() as previous:
        previous.setattr(
            coordinator,
            "device_info_for",
            lambda sub: coordinator.device_info if sub == MAIN else device_info_for(sub),
        )
        await _load(hass, previous, coordinator)
        entities, devices = er.async_get(hass), dr.async_get(hass)
        before = _rows(hass, coordinator)
        custom = before["number", "oven_setpoint"]
        custom = entities.async_update_entity(
            custom.entity_id, new_entity_id="number.my_oven_target", name="My setpoint"
        )
        before["number", "oven_setpoint"] = custom
        main_id = custom.device_id
        lower_id = before["number", "subdevice1_oven_setpoint"].device_id
        assert main_id is not None and lower_id is not None
        assert main_id != lower_id
        if custom_device_names:
            devices.async_update_device(main_id, name_by_user="My main cavity")
            devices.async_update_device(lower_id, name_by_user="My lower cavity")
        old_devices = {
            device_id: copy(devices.async_get(device_id)) for device_id in (main_id, lower_id)
        }
        await hass.config_entries.async_unload(coordinator._entry.entry_id)
    await _load(hass, monkeypatch, coordinator)

    for state in ("On" if initially_divided else "Off", "Off", "On", None, "Unexpected"):
        await _set_divider(hass, coordinator, state)
        after = _rows(hass, coordinator)
        assert after.keys() == before.keys()
        for key, row in after.items():
            old = before[key]
            assert (
                row.entity_id,
                row.unique_id,
                row.device_id,
                row.original_name,
                row.translation_key,
            ) == (
                old.entity_id,
                old.unique_id,
                old.device_id,
                old.original_name,
                old.translation_key,
            )
        assert after["number", "oven_setpoint"].name == "My setpoint"
        for device_id, old in old_devices.items():
            current = devices.async_get(device_id)
            assert current is not None and old is not None
            assert old.name is not None
            assert current.identifiers == old.identifiers
            assert current.name_by_user == old.name_by_user
            assert current.name == (old.name + " Main oven" if device_id == main_id else old.name)
        mode = after["select", "oven_mode"]
        main = devices.async_get(main_id)
        assert main is not None and main.name is not None
        assert hass.states.get(mode.entity_id).name.startswith(
            "My main cavity" if custom_device_names else main.name
        )
    assert not any(method == "PUT" for method, _ in _requests())
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


@pytest.mark.parametrize("divided", [False, True])
async def test_offline_start_retains_device_labels_on_reconnect(hass, monkeypatch, divided):
    online = await _discovered(hass, _routes(divided=divided))
    await online._async_save_snapshot(online.last_resources, online.subdevices)
    restored = LocalThingsCoordinator(hass, online._entry)
    restored.async_set_update_error(UpdateFailed("device offline"))
    assert await restored.async_rehydrate()
    assert restored.last_resources == {}
    await _load(hass, monkeypatch, restored)
    rows = _rows(hass, restored)
    devices = dr.async_get(hass)
    main = devices.async_get(rows["number", "oven_setpoint"].device_id)
    lower = devices.async_get(rows["number", "subdevice1_oven_setpoint"].device_id)
    assert main is not None and main.name is not None
    assert lower is not None and lower.name is not None
    assert main.name.endswith("Main oven")
    assert lower.name.endswith("Lower oven")
    assert hass.states.get(rows["number", "oven_setpoint"].entity_id).state == "unavailable"
    restored._rehydrate_resources = None
    for href, rep in online.last_resources.items():
        restored._observe.apply(href, rep, source="poll")
    restored.last_update_success = True
    await _set_divider(hass, restored, "Off" if divided else "On")
    assert restored.device_info_for(MAIN)["name"] == main.name
    assert restored.device_info_for(restored.subdevices[0])["name"] == lower.name
    await hass.config_entries.async_unload(restored._entry.entry_id)


async def test_other_ovens_with_a_lower_cavity_use_main_label(hass):
    coordinator = _coordinator(hass)
    await _discover(coordinator, "oven_nv75n_dual_cook")
    name = coordinator.device_info_for(MAIN)["name"]
    assert name is not None and name.endswith("Main oven")


@pytest.mark.parametrize(
    "fixture",
    [
        "oven_tp2x_ks_walloven",
        "microwave_nw9300md",
        "range_tp1x_da_ks_range_0101x",
        "airconditioner_fac_bora_2in1",
    ],
)
async def test_unrelated_master_devices_keep_their_names(hass, fixture):
    coordinator = _coordinator(hass)
    await _discover(coordinator, fixture)
    assert coordinator.device_info_for(MAIN) == coordinator.device_info


@pytest.mark.parametrize("divided", [False, True])
async def test_legacy_combo_keeps_its_master_and_cavity_labels(hass, divided):
    coordinator = await _discovered(hass, _combo_routes(divided=divided))
    assert coordinator.device_info_for(MAIN) == coordinator.device_info
    before = {s: coordinator.device_info_for(s)["name"] for s in coordinator.subdevices}
    for state in ("Off", "On", None):
        await _set_divider(hass, coordinator, state)
        assert {s: coordinator.device_info_for(s)["name"] for s in coordinator.subdevices} == before


async def test_disabled_lower_does_not_affect_main_device_name(hass):
    online = await _discovered(hass, _routes(divided=False))
    await online._async_save_snapshot(online.last_resources, online.subdevices)
    hass.config_entries.async_update_entry(
        online._entry, options={CONF_DISABLED_SUBDEVICES: {"1": "Lower oven"}}
    )
    restored = LocalThingsCoordinator(hass, online._entry)
    assert await restored.async_rehydrate()
    assert restored.subdevices == []
    assert restored.device_info_for(MAIN) == restored.device_info
