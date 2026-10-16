"""Probe sensors appear after startup and retain their identity on reconnect."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.registry.adapter import flatten
from tests.conftest import _load_device_full
from tests.localthings.test_combined_oven import _load, _rows
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401
)
from tests.test_subdevice_discovery import _coordinator, _discover_with


def _probe_update(coordinator, family, connected, *, current=107, target=150):
    mode = coordinator.resource("/mode/vs/0")
    options = [o for o in mode[PREFIX + "options"] if not o.startswith("meatprobe_")]
    options.append("meatprobe_connected" if connected else "meatprobe_disconnected")
    coordinator._observe.apply("/mode/vs/0", {**mode, PREFIX + "options": options}, source="poll")
    if family == "ocf_fallback":
        for kind, value in (("current", current), ("desired", target)):
            coordinator._observe.apply(
                f"/temperature/{kind}/prob/0",
                {"temperature": value, "units": "F"},
                source="poll",
            )
    else:
        rep = coordinator.resource("/temperatures/vs/0")
        items = [i for i in rep[PREFIX + "items"] if i[PREFIX + "id"] != "1"]
        items.append(
            {
                PREFIX + "id": "1",
                PREFIX + "current": current,
                PREFIX + "desired": target,
                PREFIX + "unit": "Fahrenheit",
            }
        )
        coordinator._observe.apply(
            "/temperatures/vs/0", {**rep, PREFIX + "items": items}, source="poll"
        )
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))


@pytest.mark.parametrize("family", ["legacy", "ocf_vendor", "ocf_fallback"])
@pytest.mark.parametrize("initially_connected", [False, True])
async def test_probe_connects_without_reload_and_reuses_entities(
    hass, monkeypatch, family, initially_connected
):
    hass.config.units = US_CUSTOMARY_SYSTEM
    if family == "legacy":
        coordinator = await _discovered(hass)
    else:
        coordinator = _coordinator(hass)
        resources, links, seeds = _load_device_full("oven_nv75n_dual_cook")
        if family == "ocf_fallback":
            del resources["/temperatures/vs/0"]
        await _discover_with(coordinator, resources, links, seeds)
    if initially_connected:
        _probe_update(coordinator, family, True)
    await _load(hass, monkeypatch, coordinator)
    reload_requested = Mock()
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reload_requested)

    probe_keys = {("sensor", "food_probe_temp"), ("sensor", "food_probe_setpoint")}
    before = _rows(hass, coordinator)
    assert probe_keys.issubset(before) is initially_connected
    for current in (107, 108):
        _probe_update(coordinator, family, True, current=current)
        await hass.async_block_till_done()
        rows = _rows(hass, coordinator)
        assert rows.keys() == before.keys() | probe_keys
        for key, value in (("food_probe_temp", current), ("food_probe_setpoint", 150)):
            state = hass.states.get(rows["sensor", key].entity_id)
            assert state is not None and float(state.state) == value
            assert state.attributes["unit_of_measurement"] == "°F"
            name = "Temperature Probe" + (" Target" if key == "food_probe_setpoint" else "")
            if family != "legacy":
                name = "Upper " + name
            assert rows["sensor", key].original_name == name

    registry = er.async_get(hass)
    probe = rows["sensor", "food_probe_temp"]
    registry.async_update_entity(probe.entity_id, name="My Food Probe")
    for connected in (False, True, False, True):
        # Retained readings must not remain available after the socket disconnects.
        _probe_update(coordinator, family, connected, current=112)
        await hass.async_block_till_done()
        after = _rows(hass, coordinator)
        assert after.keys() == rows.keys()
        for key in probe_keys:
            assert after[key].entity_id == rows[key].entity_id
            assert after[key].unique_id == rows[key].unique_id
            state = hass.states.get(after[key].entity_id)
            assert state is not None
            assert (state.state == "unavailable") is not connected
        assert registry.async_get(probe.entity_id).name == "My Food Probe"
    reload_requested.assert_not_called()

    await hass.config_entries.async_unload(coordinator._entry.entry_id)
    # A deferred-add listener must not add entities after the platform unloads.
    restored = hass.states.get(probe.entity_id)
    assert restored is not None and restored.state == "unavailable"
    _probe_update(coordinator, family, True)
    await hass.async_block_till_done()
    assert hass.states.get(probe.entity_id) is restored

    _probe_update(coordinator, family, False)
    await _load(hass, monkeypatch, coordinator)
    _probe_update(coordinator, family, True)
    await hass.async_block_till_done()
    after_restart = _rows(hass, coordinator)
    for key in probe_keys:
        assert after_restart[key].entity_id == rows[key].entity_id
        assert hass.states.get(after_restart[key].entity_id).state != "unavailable"
    assert registry.async_get(probe.entity_id).name == "My Food Probe"
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


async def test_unloaded_pending_probe_listener_does_not_create_entities(hass, monkeypatch):
    coordinator = await _discovered(hass)
    await _load(hass, monkeypatch, coordinator)
    assert ("sensor", "food_probe_temp") not in _rows(hass, coordinator)
    await hass.config_entries.async_unload(coordinator._entry.entry_id)
    _probe_update(coordinator, "legacy", True)
    await hass.async_block_till_done()
    assert ("sensor", "food_probe_temp") not in _rows(hass, coordinator)


@pytest.mark.parametrize("language", ["cs", "de", "en", "es", "fr", "it", "ko", "nl", "pl", "sk"])
async def test_probe_label_upgrade_preserves_entity_ids(hass, monkeypatch, language):
    hass.config.language = language
    coordinator = await _discovered(hass)
    _probe_update(coordinator, "legacy", True)
    registry = er.async_get(hass)
    previous = {}
    for key, suffix in (("food_probe_temp", ""), ("food_probe_setpoint", "_target")):
        previous[key] = registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{DOMAIN}_{coordinator.device_key}_{key}",
            config_entry=coordinator._entry,
            suggested_object_id="old_oven_food_probe" + suffix,
            original_name="Oven Food Probe" + (" Target" if suffix else ""),
            translation_key="oven_cavity_" + key,
        )
    await _load(hass, monkeypatch, coordinator)
    catalog = json.loads(
        (
            Path(__file__).parents[2]
            / "custom_components/localthings/translations"
            / f"{language}.json"
        ).read_text()
    )
    rows = _rows(hass, coordinator)
    for key, old in previous.items():
        row = rows["sensor", key]
        assert row.entity_id == old.entity_id
        assert row.unique_id == old.unique_id
        assert row.original_name == catalog["entity"]["sensor"][key]["name"]
        assert row.translation_key == key
    await hass.config_entries.async_unload(coordinator._entry.entry_id)
