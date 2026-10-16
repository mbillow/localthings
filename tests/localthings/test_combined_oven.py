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
    CAVITY_KEYS,
    OPTION_KEYS,
    async_combine_oven_devices,
    combined_lower,
    shared_keys,
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
from tests.test_subdevice_discovery import _coordinator, _discover


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
    removed = {
        ("binary_sensor", "divider"),
        ("binary_sensor", "subdevice1_door_open"),
        ("binary_sensor", "subdevice1_reservoir_open"),
        ("binary_sensor", "subdevice1_power_switch"),
        ("binary_sensor", "subdevice1_remote_control"),
    }
    assert rows.keys() == (old_rows.keys() - removed) | {("sensor", "connection_mode")}
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
        expected = "Upper" if divided else "Oven"
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


async def test_unidentified_sibling_keeps_separate_devices(hass):
    coordinator = await _discovered(hass)
    coordinator._observe.apply(
        "/information/vs/1", {PREFIX + "description": "OTHER"}, source="poll"
    )
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


async def _ocf_coordinator(hass, fixture):
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
    return coordinator


@pytest.mark.parametrize(
    "fixture", ["oven_nv75n_dual_cook", "range_tp1x_da_ks_range_0101x", "oven_lcd_r18"]
)
async def test_ocf_cavities_migrate_without_losing_model_specific_entities(
    hass, monkeypatch, fixture
):
    coordinator = await _ocf_coordinator(hass, fixture)
    upper, lower, old_rows = _seed_split_registry(hass, coordinator)
    await _load(hass, monkeypatch, coordinator)
    rows = _rows(hass, coordinator)
    assert rows.keys() == old_rows.keys() | {("sensor", "connection_mode")}
    assert dr.async_get(hass).async_get(lower.id) is None
    for key, row in rows.items():
        assert row.device_id == upper.id
        if key not in old_rows:
            continue
        assert row.entity_id == old_rows[key].entity_id
        assert row.unique_id == old_rows[key].unique_id
        if row.translation_key and row.translation_key.startswith("oven_cavity_"):
            label = "Lower" if key[1].startswith("subdevice1_") else "Upper"
            assert row.original_name.startswith(label + " ")
    assert ("number", "cook_time") in rows
    assert ("number", "subdevice1_cook_time") in rows
    if fixture == "oven_nv75n_dual_cook":
        # Identical values in one capture do not prove these resources are shared.
        for domain, key in (
            ("switch", "power_switch"),
            ("binary_sensor", "door_open"),
            ("binary_sensor", "remote_control"),
        ):
            assert (domain, key) in rows
            assert (domain, "subdevice1_" + key) in rows
    for divided in (False, True):
        coordinator._observe.apply(
            "/connected/vs/1", {PREFIX + "connected": "On" if divided else "Off"}, source="poll"
        )
        coordinator.async_set_updated_data(
            flatten(coordinator.bound, coordinator.entity_resources())
        )
        await hass.async_block_till_done()
        label = "Upper" if divided else "Oven"
        assert hass.states.get(rows["number", "cook_time"].entity_id).name.endswith(
            label + " Cook Time"
        )
        assert (
            hass.states.get(rows["number", "subdevice1_cook_time"].entity_id).state == "unavailable"
        ) is not divided
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


@pytest.mark.parametrize(
    "fixture",
    ["oven", "oven_tp2x_ks_walloven", "microwave_nw9300md", "airconditioner_fac_bora_2in1"],
)
async def test_single_cavity_and_other_appliances_keep_their_layout(hass, fixture):
    coordinator = await _ocf_coordinator(hass, fixture)
    assert coordinator.combined_oven_lower is None


async def test_pair_detection_does_not_depend_on_branding_or_index_one(hass):
    from custom_components.localthings.registry.subdevices import Subdevice

    coordinator = await _ocf_coordinator(hass, "oven_nv75n_dual_cook")
    old = coordinator.subdevices[0]
    lower = Subdevice(kind="indexed", key="2", seed_path=("device", "2"))
    resources = {
        lower.to_actual(canonical)
        if (canonical := old.to_canonical(href)) is not None
        else href: rep
        for href, rep in coordinator.last_resources.items()
    }
    for sub in (MAIN, lower):
        resources[sub.to_actual("/information/vs/0")] = {PREFIX + "modelNum": "UNKNOWN-OVEN"}
    assert combined_lower(resources, [lower], "oven") == lower
    assert not shared_keys(resources, lower)
    assert combined_lower(resources, [lower], "airconditioner") is None
    assert combined_lower(resources, [lower, old], "oven") is None
    del resources[lower.to_actual("/oven/vs/0")]
    assert combined_lower(resources, [lower], "oven") is None


@pytest.mark.parametrize("language", ["cs", "de", "en", "es", "fr", "it", "ko", "nl", "pl", "sk"])
async def test_ocf_names_use_each_locales_existing_labels(hass, monkeypatch, language):
    import json
    from pathlib import Path

    catalog = json.loads(
        (
            Path(__file__).parents[2]
            / "custom_components/localthings/translations"
            / f"{language}.json"
        ).read_text()
    )
    hass.config.language = language
    coordinator = await _ocf_coordinator(hass, "oven_nv75n_dual_cook")
    await _load(hass, monkeypatch, coordinator)
    for (domain, key), row in _rows(hass, coordinator).items():
        if not row.translation_key or not row.translation_key.startswith("oven_cavity_"):
            continue
        base = row.translation_key.removeprefix("oven_cavity_")
        source = catalog["entity"][domain][base]
        translated = catalog["entity"][domain][row.translation_key]
        label = "lower" if key.startswith("subdevice1_") else "upper"
        assert row.original_name == translated["name"].format(
            cavity=catalog["selector"]["instance_name"]["options"][f"oven_cavity_{label}"]
        )
        assert source["name"] in row.original_name
        assert translated.get("state") == source.get("state")
    for platform, items in catalog["entity"].items():
        for key, value in items.items():
            if key in CAVITY_KEYS | OPTION_KEYS and "name" in value:
                assert "oven_cavity_" + key in items, (language, platform, key)
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


@pytest.mark.parametrize("fixture", ["range_tp1x_da_ks_range_0101x", "oven_lcd_r18"])
async def test_combined_ocf_action_preserves_routes_and_remote_permission(
    hass, monkeypatch, fixture
):
    from homeassistant.util.unit_system import METRIC_SYSTEM

    coordinator = await _ocf_coordinator(hass, fixture)
    is_range = fixture.startswith("range")
    hass.config.units = US_CUSTOMARY_SYSTEM if is_range else METRIC_SYSTEM
    # Explicit test inputs: permit both cavities, then revoke only Lower.
    for index in (0, 1):
        coordinator._observe.apply(
            f"/remotectrl/vs/{index}", {PREFIX + "remoteControlEnabled": "true"}, source="poll"
        )
    put = AsyncMock()
    monkeypatch.setattr(coordinator, "_async_put", put)
    monkeypatch.setattr(coordinator, "async_request_refresh", AsyncMock())
    await _load(hass, monkeypatch, coordinator)
    device = dr.async_entries_for_config_entry(dr.async_get(hass), coordinator._entry.entry_id)[0]
    for cavity, index in (("lower", 1), ("upper", 0)):
        mode = cavity.title() + ("ConvectionBake" if is_range else "Convection")
        data = {
            "device_id": device.id,
            "cavity": cavity,
            "mode": mode,
            "temperature": 350 if is_range else 160,
            "duration": "00:05:00",
        }
        await hass.services.async_call(DOMAIN, "start_cooking", data, blocking=True)
        path, body, href = put.call_args.args
        assert path == ["device", str(index)] and href == f"/device/{index}"
        elements = {item["href"]: item["rep"] for item in body if "rep" in item}
        assert elements[f"/mode/vs/{index}"][PREFIX + "modes"] == [mode]
        assert set(elements) == {
            f"/mode/vs/{index}",
            f"/temperatures/vs/{index}",
            f"/operational/state/vs/{index}",
        }
        assert elements[f"/temperatures/vs/{index}"][PREFIX + "items"][0][PREFIX + "desired"] == (
            "350" if is_range else "160"
        )
    put.reset_mock()
    coordinator._observe.apply("/connected/vs/1", {PREFIX + "connected": "Off"}, source="poll")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "start_cooking",
            {
                "device_id": device.id,
                "cavity": "lower",
                "mode": "LowerConvectionBake" if is_range else "LowerConvection",
                "duration": "00:05:00",
            },
            blocking=True,
        )
    put.assert_not_called()
    coordinator._observe.apply("/connected/vs/1", {PREFIX + "connected": "On"}, source="poll")
    coordinator._observe.apply(
        "/remotectrl/vs/1", {PREFIX + "remoteControlEnabled": "false"}, source="poll"
    )
    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            DOMAIN, "start_cooking", {"device_id": device.id, "cavity": "lower"}, blocking=True
        )
    assert error.value.translation_key == "remote_control_disabled"
    put.assert_not_called()
    assert coordinator._remote_control_enabled(MAIN)
    await hass.config_entries.async_unload(coordinator._entry.entry_id)


async def test_mode_spec_cavity_grouping_survives_offline_startup(hass, monkeypatch):
    coordinator = await _ocf_coordinator(hass, "oven_lcd_r18")
    coordinator._rehydrate_resources = coordinator.last_resources
    coordinator._cache.links.clear()
    coordinator._canonical_cache.clear()
    await _load(hass, monkeypatch, coordinator, offline=True)
    assert (
        len(dr.async_entries_for_config_entry(dr.async_get(hass), coordinator._entry.entry_id)) == 1
    )
    rows = _rows(hass, coordinator)
    assert rows["number", "subdevice1_cook_time"].original_name == "Lower Cook Time"
    assert hass.states.get(rows["number", "subdevice1_cook_time"].entity_id).state == "unavailable"
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
