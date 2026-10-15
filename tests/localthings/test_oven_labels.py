"""Upper/Oven labels change without moving entities between cavity devices."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.localthings.oven_labels import CAVITY_KEYS, main_cavity_divider
from custom_components.localthings.registry.adapter import flatten
from tests.localthings.test_oven_probe import _load, _rows
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401
    _FakeConnection,
    _routes,
)
from tests.test_subdevice_discovery import _coordinator, _discover


def _catalog(language):
    return json.loads(
        (
            Path(__file__).parents[2]
            / "custom_components/localthings/translations"
            / f"{language}.json"
        ).read_text()
    )


async def _set_divider(hass, coordinator, state):
    rep = {} if state is None else {PREFIX + "connected": state}
    coordinator._observe.apply("/connected/vs/1", rep, source="poll")
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))
    await hass.async_block_till_done()


@pytest.mark.parametrize("language", ["cs", "de", "en", "es", "fr", "it", "ko", "nl", "pl", "sk"])
@pytest.mark.parametrize("initially_divided", [False, True])
async def test_split_label_upgrade_preserves_devices_and_entities(
    hass, monkeypatch, language, initially_divided
):
    hass.config.language = language
    coordinator = await _discovered(hass, _routes(divided=initially_divided))
    with monkeypatch.context() as previous:
        previous.setattr(
            "custom_components.localthings.entity.main_cavity_divider", lambda *a: None
        )
        await _load(hass, previous, coordinator)
    old_rows = _rows(hass, coordinator)
    device_ids = {row.device_id for row in old_rows.values()}
    assert len(device_ids) == 2
    devices, entities = dr.async_get(hass), er.async_get(hass)
    for index, device_id in enumerate(sorted(device_ids)):
        devices.async_update_device(device_id, name_by_user=f"My Oven {index}")
    old_devices = {device_id: devices.async_get(device_id) for device_id in device_ids}
    custom = old_rows["number", "oven_setpoint"]
    entities.async_update_entity(custom.entity_id, name="My Setpoint")
    await hass.config_entries.async_unload(coordinator._entry.entry_id)
    await _load(hass, monkeypatch, coordinator)
    catalog = _catalog(language)
    reload_requested = Mock()
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reload_requested)
    _FakeConnection.log.clear()

    for state in ("On" if initially_divided else "Off", "Off", "On", None, "Off", "On"):
        await _set_divider(hass, coordinator, state)
        rows = _rows(hass, coordinator)
        assert rows.keys() == old_rows.keys()
        label = "whole" if state == "Off" else "upper"
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
                assert expected.get("state") == catalog["entity"][domain][old.translation_key].get(
                    "state"
                )
                assert hass.states.get(row.entity_id).name.endswith(
                    "My Setpoint" if row.entity_id == custom.entity_id else name
                )
            else:
                assert row.original_name == old.original_name
                assert row.translation_key == old.translation_key
        assert entities.async_get(custom.entity_id).name == "My Setpoint"
        for device_id, old in old_devices.items():
            current = devices.async_get(device_id)
            assert old is not None
            assert current is not None
            assert (current.identifiers, current.name, current.name_by_user) == (
                old.identifiers,
                old.name,
                old.name_by_user,
            )
    reload_requested.assert_not_called()
    assert not any(method == "PUT" for method, *_ in _FakeConnection.log)
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


@pytest.mark.parametrize(
    "fixture", ["oven_nv75n_dual_cook", "range_tp1x_da_ks_range_0101x", "oven_lcd_r18"]
)
async def test_ocf_cavity_labels_keep_both_devices(hass, monkeypatch, fixture):
    coordinator = _coordinator(hass)
    if fixture == "oven_lcd_r18":
        from tests.test_dual_cook_lower_zone import LOWER, _resources

        resources = _resources()
        coordinator.subdevices = [LOWER]
        coordinator._run_discovery(resources)
        for href, rep in resources.items():
            coordinator._observe.apply(href, rep, source="poll")
    else:
        await _discover(coordinator, fixture)
    await _load(hass, monkeypatch, coordinator)
    before = _rows(hass, coordinator)
    device_ids = {row.device_id for row in before.values()}
    assert len(device_ids) == 2
    for state, label in (("Off", "Oven"), ("On", "Upper")):
        await _set_divider(hass, coordinator, state)
        rows = _rows(hass, coordinator)
        assert rows.keys() == before.keys()
        assert {row.device_id for row in rows.values()} == device_ids
        assert rows["number", "cook_time"].original_name == label + " Cook Time"
        assert rows["number", "subdevice1_cook_time"].original_name == "Cook Time"
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


@pytest.mark.parametrize(
    "fixture",
    ["oven", "oven_tp2x_ks_walloven", "microwave_nw9300md", "airconditioner_fac_bora_2in1"],
)
async def test_no_cavity_labels_without_a_declared_divider_pair(hass, fixture):
    coordinator = _coordinator(hass)
    await _discover(coordinator, fixture)
    assert (
        main_cavity_divider(
            coordinator._cook_view(coordinator.discovery_resources), coordinator.subdevices
        )
        is None
    )
