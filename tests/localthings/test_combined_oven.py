"""One physical Flex Duo device keeps independent cavity commands and identities."""

from collections import Counter
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import CONF_DEVICE_KEY, DOMAIN
from custom_components.localthings.entity import _is_included
from custom_components.localthings.oven_device import (
    async_combine_oven_devices,
)
from custom_components.localthings.registry.adapter import _key, flatten
from custom_components.localthings.registry.entities import PLATFORM_OF
from custom_components.localthings.registry.subdevices import MAIN
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401
    _FakeConnection,
    _lower,
    _routes,
    _seed,
)


async def _load(hass, monkeypatch, coordinator, *, offline=False):
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
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))
    if offline:
        coordinator.last_update_success = False
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


def _seed_split_registry(hass, coordinator):
    devices, entities = dr.async_get(hass), er.async_get(hass)
    with patch("custom_components.localthings.coordinator.combined_lower", return_value=None):
        upper = devices.async_get_or_create(
            config_entry_id=coordinator._entry.entry_id, **coordinator.device_info_for(MAIN)
        )
        lower = devices.async_get_or_create(
            config_entry_id=coordinator._entry.entry_id,
            **coordinator.device_info_for(_lower(coordinator)),
        )
        for bound in coordinator.bound:
            if not _is_included(bound, coordinator):
                continue
            entities.async_get_or_create(
                PLATFORM_OF[type(bound.desc)],
                DOMAIN,
                f"{DOMAIN}_{coordinator.device_key}_{_key(bound)}",
                config_entry=coordinator._entry,
                device_id=upper.id if bound.subdevice == MAIN else lower.id,
            )
    return upper, lower, _rows(hass, coordinator)


@pytest.mark.parametrize("initially_divided", [False, True])
async def test_upgrade_groups_entities_preserves_ids_and_changes_cavity_labels(
    hass, monkeypatch, initially_divided
):
    coordinator = await _discovered(hass, _routes(divided=initially_divided))
    upper, lower, old_rows = _seed_split_registry(hass, coordinator)
    entities = er.async_get(hass)
    old_start = old_rows["button", "subdevice1_start_cooking"]
    entities.async_update_entity(
        old_start.entity_id, new_entity_id="button.my_lower_start", name="My Lower Start"
    )
    old_rows = _rows(hass, coordinator)
    await _load(hass, monkeypatch, coordinator)

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), coordinator._entry.entry_id)
    assert [d.id for d in devices] == [upper.id]
    assert dr.async_get(hass).async_get(lower.id) is None
    rows = _rows(hass, coordinator)
    assert all(row.device_id == upper.id for row in rows.values())
    assert rows["button", "subdevice1_start_cooking"].entity_id == "button.my_lower_start"
    assert rows["button", "subdevice1_start_cooking"].name == "My Lower Start"
    for key, row in rows.items():
        if key in old_rows:
            assert row.entity_id == old_rows[key].entity_id
            assert row.unique_id == old_rows[key].unique_id

    counts = Counter(key.removeprefix("subdevice1_") for _, key in rows)
    for key in ("door_open", "divider", "reservoir_open", "power_switch", "remote_control"):
        assert counts[key] == 1
    for key in (
        "start_cooking",
        "stop",
        "oven_mode",
        "oven_setpoint",
        "cook_time_hours",
        "cook_time_minutes",
    ):
        assert counts[key] == 2
    assert ("binary_sensor", "subdevice1_divider") in rows
    assert ("binary_sensor", "divider") not in rows

    main_time = rows["number", "cook_time_minutes"].entity_id
    lower_time = rows["number", "subdevice1_cook_time_minutes"].entity_id
    for divided in (False, True, False, True):
        _FakeConnection.routes = _routes(divided=divided)
        for index in (0, 1):
            for href, rep in _seed(coordinator._session, index).items():
                coordinator._observe.apply(href, rep, source="poll")
        coordinator.async_set_updated_data(
            flatten(coordinator.bound, coordinator.entity_resources())
        )
        await hass.async_block_till_done()
        expected = "Upper" if divided else "Whole Oven"
        state = hass.states.get(main_time)
        assert state is not None and expected + " Cook Time Minutes" in state.name
        assert entities.async_get(main_time).original_name == expected + " Cook Time Minutes"
        assert (hass.states.get(lower_time).state == "unavailable") is not divided
        assert entities.async_get("button.my_lower_start").name == "My Lower Start"
    assert _rows(hass, coordinator).keys() == rows.keys()
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


async def test_old_layout_can_restore_device_and_entity_ids(hass, monkeypatch):
    coordinator = await _discovered(hass)
    upper, lower, old_rows = _seed_split_registry(hass, coordinator)
    devices = dr.async_get(hass)
    devices.async_update_device(lower.id, name_by_user="My Lower Oven")
    await _load(hass, monkeypatch, coordinator)
    assert devices.async_get(lower.id) is None
    await hass.config_entries.async_unload(coordinator._entry.entry_id)
    with patch("custom_components.localthings.coordinator.combined_lower", return_value=None):
        await _load(hass, monkeypatch, coordinator)
        rows = _rows(hass, coordinator)
        restored = devices.async_get(lower.id)
        assert restored is not None and restored.name_by_user == "My Lower Oven"
        for key, old in old_rows.items():
            assert rows[key].entity_id == old.entity_id
            assert rows[key].device_id == old.device_id
        assert {
            d.id for d in dr.async_entries_for_config_entry(devices, coordinator._entry.entry_id)
        } == {upper.id, lower.id}
        await hass.config_entries.async_unload(coordinator._entry.entry_id)


async def test_combined_start_action_selects_lower_and_upper_without_crossing_settings(
    hass, monkeypatch
):
    coordinator = await _discovered(hass)
    hass.config.units = US_CUSTOMARY_SYSTEM
    await _load(hass, monkeypatch, coordinator)
    device = dr.async_entries_for_config_entry(dr.async_get(hass), coordinator._entry.entry_id)[0]
    for cavity, mode, path, temperature, duration in (
        ("lower", "LowerBake", "/devices/1", 350, "00:07:00"),
        ("upper", "UpperConvectionBake", "/devices/0", 375, "00:05:00"),
    ):
        _FakeConnection.log.clear()
        await hass.services.async_call(
            DOMAIN,
            "start_cooking",
            {
                "device_id": device.id,
                "cavity": cavity,
                "mode": mode,
                "temperature": temperature,
                "duration": duration,
            },
            blocking=True,
        )
        writes = [(href, body) for method, href, body, _ in _FakeConnection.log if method == "PUT"]
        assert writes == [
            (
                path,
                {
                    "Device": {
                        "Mode": {"modes": [mode]},
                        "Temperatures": [{"id": "0", "desired": temperature, "unit": "Fahrenheit"}],
                        "Operation": {"operationTime": duration},
                    }
                },
            ),
            (path, {"Device": {"Operation": {"state": "Run"}}}),
        ]
    coordinator._observe.apply("/connected/vs/1", {PREFIX + "connected": "Off"}, source="poll")
    _FakeConnection.log.clear()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "start_cooking", {"device_id": device.id, "cavity": "lower"}, blocking=True
        )
    assert not any(method == "PUT" for method, *_ in _FakeConnection.log)
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


@pytest.mark.parametrize("changed", ["upper_model", "lower_model", "lower_description"])
async def test_unverified_pair_keeps_separate_devices(hass, changed):
    coordinator = await _discovered(hass)
    href = "/information/vs/0" if changed == "upper_model" else "/information/vs/1"
    key = "description" if changed == "lower_description" else "modelNum"
    coordinator._observe.apply(href, {PREFIX + key: "OTHER"}, source="poll")
    assert coordinator.combined_oven_lower is None
    assert (
        coordinator.device_info_for(_lower(coordinator))["identifiers"]
        != coordinator.device_info["identifiers"]
    )


async def test_only_the_owning_entry_is_migrated(hass):
    coordinator = await _discovered(hass)
    other = MockConfigEntry(domain=DOMAIN)
    other.add_to_hass(hass)
    registry = er.async_get(hass)
    foreign = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{DOMAIN}_{coordinator.device_key}_subdevice1_remote_control",
        config_entry=other,
    )
    async_combine_oven_devices(hass, coordinator._entry, coordinator)
    assert registry.async_get(foreign.entity_id) == foreign


@pytest.mark.parametrize("language", ["cs", "de", "en", "es", "fr", "it", "ko", "nl", "pl", "sk"])
async def test_cavity_names_and_mode_states_are_translated(hass, monkeypatch, language):
    import json
    from pathlib import Path

    from custom_components.localthings.oven_device import CAVITY_KEYS

    catalog = json.loads(
        (
            Path(__file__).parents[2]
            / "custom_components"
            / "localthings"
            / "translations"
            / f"{language}.json"
        ).read_text()
    )
    hass.config.language = language
    coordinator = await _discovered(hass)
    await _load(hass, monkeypatch, coordinator)
    rows = _rows(hass, coordinator)
    for (domain, key), row in rows.items():
        base = key.removeprefix("subdevice1_")
        if base not in CAVITY_KEYS:
            continue
        expected = catalog["entity"][domain][row.translation_key]
        label = "lower" if key.startswith("subdevice1_") else "upper"
        name = expected["name"].format(
            cavity=catalog["selector"]["instance_name"]["options"][f"oven_cavity_{label}"]
        )
        assert row.original_name == name
        if base == "oven_mode":
            assert (
                expected["state"] == catalog["entity"]["select"]["oven_mode_lcd_wall_16k"]["state"]
            )
        if base == "start_cooking":
            assert catalog["entity"]["button"]["start"]["name"] in row.original_name
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


async def test_offline_snapshot_still_registers_one_device_and_one_set_of_shared_sensors(
    hass, monkeypatch
):
    coordinator = await _discovered(hass, _routes(divided=False))
    coordinator._rehydrate_resources = coordinator.last_resources
    coordinator._cache.links.clear()
    coordinator._canonical_cache.clear()
    await _load(hass, monkeypatch, coordinator, offline=True)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), coordinator._entry.entry_id)
    assert len(devices) == 1
    rows = _rows(hass, coordinator)
    for key in ("door_open", "divider", "reservoir_open", "remote_control", "power_switch"):
        assert sum(k.removeprefix("subdevice1_") == key for _, k in rows) == 1
    assert ("button", "subdevice1_start_cooking") in rows
    for (domain, key), row in rows.items():
        if key == "connection_mode" or row.disabled_by is not None:
            continue
        assert hass.states.get(row.entity_id).state == "unavailable", (domain, key)
    await hass.config_entries.async_unload(coordinator._entry.entry_id)
