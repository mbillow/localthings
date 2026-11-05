"""Remove old washer rows independently of an oven cavity's current controls."""

from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_misidentified_washer_entities
from custom_components.localthings.const import CONF_DISABLED_SUBDEVICES, DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.subdevices import MAIN, Subdevice
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401 -- fake legacy transport
    _routes,
)
from tests.test_legacy_http_nw9300md import _routes as combo_routes


@pytest.mark.parametrize("combo", [False, True])
@pytest.mark.parametrize("startup", ["live", "snapshot", "disabled_lower"])
async def test_cavity_cleanup_without_start_preserves_valid_and_unrelated_rows(
    hass, combo, startup
):
    coordinator = await _discovered(hass, (combo_routes if combo else _routes)(divided=False))
    lower_key = "3" if combo else "1"
    cavities = [MAIN, *coordinator.subdevices]
    entry = coordinator._entry
    lower_mode = f"/mode/vs/{lower_key}"
    coordinator._observe.apply(
        lower_mode,
        {
            **coordinator.last_resources[lower_mode],
            cook.MODE_SPEC: [{"mode": "LowerBake", "control": "Setting"}],
        },
        source="poll",
    )
    if startup != "live":
        await coordinator._async_save_snapshot(coordinator.last_resources, coordinator.subdevices)
        if startup == "disabled_lower":
            hass.config_entries.async_update_entry(
                entry, options={CONF_DISABLED_SUBDEVICES: {lower_key: "Lower oven"}}
            )
        coordinator = LocalThingsCoordinator(hass, entry)
        assert await coordinator.async_rehydrate()
        assert coordinator.last_resources == {}
    # A settings-only specification omits Start, including with #721 applied.
    assert not any(
        b.subdevice.key == lower_key
        and b.desc.key == "start_cooking"
        and _is_included(b, coordinator)
        for b in coordinator.bound
    )
    registry = er.async_get(hass)
    prefix = f"{DOMAIN}_{coordinator.device_key}_"
    removed, kept = [], []
    for cavity in cavities:
        for platform, key in (
            ("button", "start"),
            ("button", "pause"),
            ("number", "delay_start_hours"),
            ("select", "cycle"),
            ("select", "spin_speed"),
            ("sensor", "progress"),
            ("sensor", "water_liters"),
            ("binary_sensor", "detergent_low"),
            ("switch", "auto_softener"),
            ("time", "delay_finish_at"),
        ):
            row = registry.async_get_or_create(
                platform, DOMAIN, f"{prefix}{cavity.key_prefix}{key}", config_entry=entry
            )
            row = registry.async_update_entity(
                row.entity_id, new_entity_id=f"{platform}.renamed_{cavity.key_prefix}{key}"
            )
            target = kept if startup == "disabled_lower" and cavity.key == lower_key else removed
            target.append(row)
        for platform, key in (
            ("button", "start_cooking"),
            ("button", "stop"),
            ("button", "diagnosis_start"),
            ("number", "cook_time"),
            ("binary_sensor", "child_lock"),
            ("sensor", "finish_time"),
            ("sensor", "start"),
        ):
            row = registry.async_get_or_create(
                platform, DOMAIN, f"{prefix}{cavity.key_prefix}{key}", config_entry=entry
            )
            kept.append(
                registry.async_update_entity(
                    row.entity_id,
                    new_entity_id=f"{platform}.kept_{cavity.key_prefix}{key}",
                    name="My custom label",
                )
            )

    other_entry = MockConfigEntry(domain=DOMAIN)
    other_entry.add_to_hass(hass)
    kept.extend(
        [
            registry.async_get_or_create(
                "button", DOMAIN, f"{prefix}filter_reset", config_entry=other_entry
            ),
            registry.async_get_or_create(
                "button", "another_integration", f"{prefix}start", config_entry=entry
            ),
            *(
                registry.async_get_or_create("button", DOMAIN, unique_id, config_entry=entry)
                for unique_id in ("start", "another_device_start", f"{prefix}subdevice99_start")
            ),
        ]
    )
    for _ in range(2):
        _drop_misidentified_washer_entities(hass, entry, coordinator)
        assert all(registry.async_get(row.entity_id) is None for row in removed)
        assert all(registry.async_get(row.entity_id) == row for row in kept)


@pytest.mark.parametrize("main_type", ["oven", "washer", "airconditioner"])
async def test_siblings_use_their_own_registry_and_stable_prefix(hass, main_type):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    washer = Subdevice("prefixed", "washer-uuid", ())
    oven = Subdevice("prefixed", "oven-uuid", ())
    unidentified = Subdevice("indexed", "2", ("device", "2"))
    views = {
        washer: {"/information/vs/0": {PREFIX + "deviceSubType": "Washer"}},
        oven: {"/information/vs/0": {PREFIX + "deviceSubType": "WallOven"}},
        unidentified: {},
    }
    coordinator = cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            device_type_name=main_type,
            device_key="test-identity",
            subdevices=[washer, oven, unidentified],
            discovery_canonical=views.__getitem__,
        ),
    )
    registry = er.async_get(hass)
    rows = {
        subdevice: registry.async_get_or_create(
            "button",
            DOMAIN,
            f"{DOMAIN}_test-identity_{subdevice.key_prefix}start",
            config_entry=entry,
        )
        for subdevice in (MAIN, washer, oven, unidentified)
    }
    _drop_misidentified_washer_entities(hass, entry, coordinator)
    assert registry.async_get(rows[washer].entity_id) is not None
    assert registry.async_get(rows[oven].entity_id) is None
    for subdevice in (MAIN, unidentified):
        assert (registry.async_get(rows[subdevice].entity_id) is None) is (main_type == "oven")
