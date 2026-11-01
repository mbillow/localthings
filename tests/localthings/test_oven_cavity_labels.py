"""Divided wall oven labels follow the divider without replacing devices or entities."""

from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.localthings.const import CONF_DEVICE_KEY, CONF_DISABLED_SUBDEVICES, DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import LocalThingsEntity
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.oven_labels import CAVITY_KEYS, cavity_divider
from custom_components.localthings.registry.subdevices import MAIN, Subdevice
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401 -- fake legacy transport
    _requests,
    _routes,
)
from tests.test_legacy_http_nw9300md import _routes as _combo_routes
from tests.test_subdevice_discovery import _coordinator, _discover
from tests.test_translations import _languages
from tests.test_translations import _load as _catalog


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


@pytest.mark.parametrize("language", _languages())
@pytest.mark.parametrize("initially_divided", [False, True])
async def test_upgrade_and_live_labels_preserve_identity_and_user_names(
    hass, monkeypatch, language, initially_divided
):
    hass.config.language = language
    coordinator = await _discovered(hass, _routes(divided=initially_divided))
    with monkeypatch.context() as previous:
        previous.setattr("custom_components.localthings.entity.cavity_divider", lambda *args: None)
        await _load(hass, previous, coordinator)
    old_rows = _rows(hass, coordinator)
    device_ids = {row.device_id for row in old_rows.values()}
    assert len(device_ids) == 2
    devices, entities = dr.async_get(hass), er.async_get(hass)
    for index, device_id in enumerate(sorted(device_ids)):
        devices.async_update_device(device_id, name_by_user=f"My Oven {index}")
    old_devices = {device_id: devices.async_get(device_id) for device_id in device_ids}
    custom = old_rows["number", "oven_setpoint"]
    custom = entities.async_update_entity(
        custom.entity_id, new_entity_id="number.my_oven_target", name="My Setpoint"
    )
    old_rows["number", "oven_setpoint"] = custom
    await hass.config_entries.async_unload(coordinator._entry.entry_id)
    await _load(hass, monkeypatch, coordinator)
    reload_requested = Mock()
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reload_requested)
    catalog = _catalog(language)

    for state in ("On" if initially_divided else "Off", "Off", "On", None, "Unexpected"):
        await _set_divider(hass, coordinator, state)
        rows = _rows(hass, coordinator)
        assert rows.keys() == old_rows.keys()
        label = "oven" if state == "Off" else "upper"
        for (domain, key), row in rows.items():
            old = old_rows[domain, key]
            assert (row.entity_id, row.unique_id, row.device_id) == (
                old.entity_id,
                old.unique_id,
                old.device_id,
            )
            if key in CAVITY_KEYS:
                expected = catalog["entity"][domain]["oven_cavity_" + old.translation_key]
                name = expected["name"].format(
                    cavity=catalog["selector"]["instance_name"]["options"][f"oven_cavity_{label}"]
                )
                assert row.original_name == name
                assert row.translation_key == "oven_cavity_" + old.translation_key
                assert hass.states.get(row.entity_id).name.endswith(
                    "My Setpoint" if row.entity_id == custom.entity_id else name
                )
            else:
                assert row.original_name == old.original_name
                assert row.translation_key == old.translation_key
        current = entities.async_get(custom.entity_id)
        assert current is not None
        assert current.name == "My Setpoint"
        for device_id, old in old_devices.items():
            current = devices.async_get(device_id)
            assert current is not None and old is not None
            assert (current.identifiers, current.name, current.name_by_user) == (
                old.identifiers,
                old.name,
                old.name_by_user,
            )
    reload_requested.assert_not_called()
    assert not any(method == "PUT" for method, _ in _requests())
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


@pytest.mark.parametrize("divided", [False, True])
async def test_offline_snapshot_uses_saved_label_and_updates_on_reconnect(
    hass, monkeypatch, divided
):
    online = await _discovered(hass, _routes(divided=divided))
    await online._async_save_snapshot(online.last_resources, online.subdevices)
    restored = LocalThingsCoordinator(hass, online._entry)
    restored.async_set_update_error(UpdateFailed("device offline"))
    assert await restored.async_rehydrate()
    assert restored.last_resources == {}
    await _load(hass, monkeypatch, restored)
    rows = _rows(hass, restored)
    target = rows["number", "oven_setpoint"]
    assert target.original_name == ("Upper" if divided else "Oven") + " setpoint"
    assert hass.states.get(target.entity_id).state == "unavailable"
    restored._rehydrate_resources = None
    for href, rep in online.last_resources.items():
        restored._observe.apply(href, rep, source="poll")
    restored.last_update_success = True
    await _set_divider(hass, restored, "Off" if divided else "On")
    updated = _rows(hass, restored)["number", "oven_setpoint"]
    assert updated.entity_id == target.entity_id
    assert updated.original_name == ("Oven" if divided else "Upper") + " setpoint"
    await hass.config_entries.async_unload(restored._entry.entry_id)


@pytest.mark.parametrize(
    "fixture",
    [
        "oven_nv75n_dual_cook",
        "oven_tp2x_ks_walloven",
        "microwave_nw9300md",
        "range_tp1x_da_ks_range_0101x",
        "airconditioner_fac_bora_2in1",
    ],
)
async def test_other_devices_keep_original_entity_labels(hass, fixture):
    coordinator = _coordinator(hass)
    await _discover(coordinator, fixture)
    for bound in coordinator.bound:
        entity = LocalThingsEntity(coordinator, bound)
        assert entity._oven_divider_href is None
        assert "cavity" not in entity.translation_placeholders


@pytest.mark.parametrize("divided", [False, True])
async def test_combo_without_a_matching_wall_oven_pair_keeps_its_labels(hass, monkeypatch, divided):
    coordinator = await _discovered(hass, _combo_routes(divided=divided))
    await _load(hass, monkeypatch, coordinator)
    before = _rows(hass, coordinator)
    assert before["number", "subdevice2_oven_setpoint"].original_name == "Setpoint"
    for state in ("Off", "On", None):
        coordinator._observe.apply("/connected/vs/3", {PREFIX + "connected": state}, source="poll")
        coordinator._observe.apply("/connected/vs/1", {PREFIX + "connected": "Off"}, source="poll")
        coordinator._publish_data()
        await hass.async_block_till_done()
        rows = _rows(hass, coordinator)
        for (domain, key), row in rows.items():
            old = before[domain, key]
            assert (row.entity_id, row.unique_id, row.device_id) == (
                old.entity_id,
                old.unique_id,
                old.device_id,
            )
            assert row.original_name == old.original_name
            assert row.translation_key == old.translation_key
    assert not any(method == "PUT" for method, _ in _requests())
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


async def test_disabled_lower_does_not_supply_labels_from_its_saved_snapshot(hass, monkeypatch):
    online = await _discovered(hass, _routes(divided=False))
    await online._async_save_snapshot(online.last_resources, online.subdevices)
    hass.config_entries.async_update_entry(
        online._entry, options={CONF_DISABLED_SUBDEVICES: {"1": "Lower oven"}}
    )
    restored = LocalThingsCoordinator(hass, online._entry)
    assert await restored.async_rehydrate()
    assert restored.subdevices == []
    await _load(hass, monkeypatch, restored)
    target = _rows(hass, restored)["number", "oven_setpoint"]
    assert target.original_name == "Setpoint"
    assert target.translation_key == "oven_setpoint"
    await hass.config_entries.async_unload(restored._entry.entry_id)


@pytest.mark.parametrize("description", ["LCD_OV_WALL_16K", "ANOTHER_WALL_OVEN"])
@pytest.mark.parametrize("index", ["1", "3"])
def test_pairing_uses_reported_descriptions_not_model_or_slot(description, index):
    lower = Subdevice(kind="indexed", key=index, seed_path=("device", index))
    resources = {
        "/information/vs/0": {
            PREFIX + "description": description,
            PREFIX + "deviceSubType": "WallOven",
        },
        lower.to_actual("/information/vs/0"): {
            PREFIX + "description": description + "_DIV",
            PREFIX + "deviceSubType": "WallOven",
        },
        lower.to_actual("/connected/vs/0"): {},
    }
    assert cavity_divider(resources, MAIN, [lower]) == lower.to_actual("/connected/vs/0")
    assert cavity_divider(resources, lower, [lower]) is None
    assert cavity_divider(resources, MAIN, []) is None


@pytest.mark.parametrize(
    "href,field,value",
    [
        ("/information/vs/0", "deviceSubType", "MicrowaveOven"),
        ("/information/vs/1", "deviceSubType", "MicrowaveOven"),
        ("/information/vs/0", "description", None),
        ("/information/vs/0", "description", 17),
        ("/information/vs/0", "description", ""),
        ("/information/vs/1", "description", "UNRELATED_DIV"),
    ],
)
async def test_incomplete_or_unrelated_pair_keeps_existing_labels(hass, href, field, value):
    coordinator = await _discovered(hass)
    resources = coordinator.last_resources
    resources[href] = {**resources[href], PREFIX + field: value}
    assert cavity_divider(resources, MAIN, coordinator.subdevices) is None


async def test_missing_divider_report_keeps_upper_and_ambiguous_pairs_stay_unchanged(hass):
    coordinator = await _discovered(hass)
    resources = coordinator.last_resources
    resources.pop("/connected/vs/1")
    assert cavity_divider(resources, MAIN, coordinator.subdevices) == "/connected/vs/1"
    resources["/connected/vs/1"] = {}
    resources["/connected/vs/2"] = {}
    resources["/information/vs/2"] = resources["/information/vs/1"]
    second = Subdevice(kind="indexed", key="2", seed_path=("device", "2"))
    assert cavity_divider(resources, MAIN, [*coordinator.subdevices, second]) is None


def test_all_languages_keep_existing_names_and_state_mappings():
    for language in _languages():
        catalog = _catalog(language)
        for entries in catalog["entity"].values():
            for key, value in entries.items():
                if not key.startswith("oven_cavity_"):
                    continue
                original = entries[key.removeprefix("oven_cavity_")]
                assert (
                    value["name"].removeprefix("{cavity} ").casefold()
                    == original["name"].casefold()
                )
                assert value.get("state") == original.get("state")
                assert value["name"].count("{cavity}") == 1
