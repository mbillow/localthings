"""Upgrade the divider-aware separate layout without losing labels or identities."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.localthings.oven_labels import CAVITY_KEYS
from custom_components.localthings.registry.adapter import flatten
from tests.localthings.test_combined_oven import _load, _rows, _seed_split_registry
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
async def test_split_label_upgrade_preserves_retained_entities(
    hass, monkeypatch, language, initially_divided
):
    hass.config.language = language
    coordinator = await _discovered(hass, _routes(divided=initially_divided))
    upper, lower, old_rows = _seed_split_registry(hass, coordinator)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    devices.async_update_device(upper.id, name_by_user="My Oven")
    catalog = _catalog(language)
    # PR #637 already has cavity-prefixed Upper labels and unprefixed Lower labels.
    for (domain, key), row in old_rows.items():
        base = key.removeprefix("subdevice1_")
        if base not in CAVITY_KEYS:
            continue
        source = "oven_mode_lcd_wall_16k" if base == "oven_mode" else base
        label = "whole" if not initially_divided else "upper"
        translation_key = "oven_cavity_" + source if key == base else source
        name = catalog["entity"][domain][translation_key]["name"].format(
            cavity=catalog["selector"]["instance_name"]["options"][f"oven_cavity_{label}"]
        )
        entities.async_update_entity(
            row.entity_id, original_name=name, translation_key=translation_key
        )
    old_rows = _rows(hass, coordinator)
    custom = old_rows["number", "oven_setpoint"]
    entities.async_update_entity(custom.entity_id, name="My Setpoint")
    await _load(hass, monkeypatch, coordinator)
    removed = {
        ("binary_sensor", "divider"),
        ("binary_sensor", "subdevice1_door_open"),
        ("binary_sensor", "subdevice1_reservoir_open"),
        ("binary_sensor", "subdevice1_power_switch"),
        ("binary_sensor", "subdevice1_remote_control"),
    }
    expected_rows = (old_rows.keys() - removed) | {("sensor", "connection_mode")}
    reload_requested = Mock()
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reload_requested)
    _FakeConnection.log.clear()

    for state in ("On" if initially_divided else "Off", "Off", "On", None, "Off", "On"):
        await _set_divider(hass, coordinator, state)
        rows = _rows(hass, coordinator)
        assert rows.keys() == expected_rows
        for (domain, key), row in rows.items():
            assert row.device_id == upper.id
            if (domain, key) not in old_rows:
                continue
            old = old_rows[domain, key]
            assert (row.entity_id, row.unique_id) == (
                old.entity_id,
                old.unique_id,
            )
            base = key.removeprefix("subdevice1_")
            if base in CAVITY_KEYS:
                label = "lower" if key != base else "whole" if state == "Off" else "upper"
                source = old.translation_key.removeprefix("oven_cavity_")
                expected = catalog["entity"][domain]["oven_cavity_" + source]
                name = expected["name"].format(
                    cavity=catalog["selector"]["instance_name"]["options"][f"oven_cavity_{label}"]
                )
                assert row.original_name == name
                assert row.translation_key == "oven_cavity_" + source
                assert expected.get("state") == catalog["entity"][domain][source].get("state")
                assert hass.states.get(row.entity_id).name.endswith(
                    "My Setpoint" if row.entity_id == custom.entity_id else name
                )
        assert entities.async_get(custom.entity_id).name == "My Setpoint"
        current = devices.async_get(upper.id)
        assert current is not None
        assert (current.identifiers, current.name, current.name_by_user) == (
            upper.identifiers,
            upper.name,
            "My Oven",
        )
        assert devices.async_get(lower.id) is None
    reload_requested.assert_not_called()
    assert not any(method == "PUT" for method, *_ in _FakeConnection.log)
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


@pytest.mark.parametrize(
    "fixture", ["oven_nv75n_dual_cook", "range_tp1x_da_ks_range_0101x", "oven_lcd_r18"]
)
async def test_ocf_cavity_labels_keep_the_combined_device(hass, monkeypatch, fixture):
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
    assert len(device_ids) == 1
    for state, label in (("Off", "Oven"), ("On", "Upper")):
        await _set_divider(hass, coordinator, state)
        rows = _rows(hass, coordinator)
        assert rows.keys() == before.keys()
        assert {row.device_id for row in rows.values()} == device_ids
        assert rows["number", "cook_time"].original_name == label + " Cook Time"
        assert rows["number", "subdevice1_cook_time"].original_name == "Lower Cook Time"
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


@pytest.mark.parametrize(
    "fixture",
    ["oven", "oven_tp2x_ks_walloven", "microwave_nw9300md", "airconditioner_fac_bora_2in1"],
)
async def test_no_cavity_labels_without_a_declared_divider_pair(hass, fixture):
    coordinator = _coordinator(hass)
    await _discover(coordinator, fixture)
    assert coordinator.combined_oven_lower is None
