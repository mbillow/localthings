"""Configuration controls must correspond to reported oven options."""

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_unsupported_oven_sound_switches
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities.oven import OVEN_MODE
from custom_components.localthings.registry.subdevices import MAIN
from custom_components.localthings.switch import LocalThingsSwitch
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401
    _lower,
)


@pytest.mark.parametrize(
    "rep,included",
    [
        ({PREFIX + "options": ["Sound_On"]}, True),
        ({PREFIX + "options": ["Sound_Off", "EnergySaving_On"]}, True),
        ({PREFIX + "options": ["EnergySaving_On"]}, False),
        ({PREFIX + "options": []}, False),
        ({}, False),
        ({"href": "/mode/vs/0"}, True),
    ],
)
def test_sound_requires_its_own_token_or_a_discovery_stub(rep, included):
    desc = next(desc for desc in OVEN_MODE.entities if desc.key == "sound")
    assert desc.exists_fn is not None
    assert desc.exists_fn(rep, {}) is included


async def test_legacy_capture_exposes_energy_saving_without_phantom_sound(hass):
    coordinator = await _discovered(hass)
    data = flatten(coordinator.bound, coordinator.entity_resources())
    for cavity in (MAIN, _lower(coordinator)):
        bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
        assert not _is_included(bound["sound"], coordinator)
        assert f"{cavity.key_prefix}sound" not in data
        assert _is_included(bound["energy_saving"], coordinator) is (cavity == MAIN)
    assert data["energy_saving"] is True
    assert f"{_lower(coordinator).key_prefix}energy_saving" not in data


async def test_phantom_sound_cleanup_handles_renames_and_preserves_other_entities(hass):
    coordinator = await _discovered(hass)
    registry = er.async_get(hass)
    removed, kept = [], []
    for bound in coordinator.bound:
        if bound.desc.key not in ("sound", "energy_saving"):
            continue
        switch = LocalThingsSwitch(coordinator, bound)
        assert switch.unique_id is not None
        old = registry.async_get_or_create(
            "switch", DOMAIN, switch.unique_id, config_entry=coordinator._entry
        )
        if bound.desc.key == "sound" and bound.subdevice == MAIN:
            removed.append(
                registry.async_update_entity(
                    old.entity_id, new_entity_id=f"switch.renamed_setting_{len(removed)}"
                )
            )
            kept.append(
                registry.async_get_or_create(
                    "sensor", DOMAIN, switch.unique_id, config_entry=coordinator._entry
                )
            )
        else:
            kept.append(old)
    kept.append(
        registry.async_get_or_create(
            "switch", DOMAIN, "UNRELATED_sound", config_entry=coordinator._entry
        )
    )
    assert len(removed) == 1
    for _ in range(2):
        _drop_unsupported_oven_sound_switches(hass, coordinator._entry, coordinator)
        assert all(registry.async_get(item.entity_id) is None for item in removed)
        assert all(registry.async_get(item.entity_id) is not None for item in kept)


@pytest.mark.parametrize("case", ["supported", "unknown", "other_model", "other_entry"])
async def test_sound_cleanup_requires_known_absence_on_owning_legacy_oven(hass, case):
    coordinator = await _discovered(hass)
    bound = next(b for b in coordinator.bound if b.subdevice == MAIN and b.desc.key == "sound")
    if case in ("supported", "unknown"):
        coordinator._observe.apply(
            bound.href,
            {PREFIX + "options": ["Sound_Off"] if case == "supported" else None},
            source="poll",
        )
    elif case == "other_model":
        coordinator._observe.apply(
            "/information/vs/0", {PREFIX + "modelNum": "OTHER_OVEN"}, source="poll"
        )
    entry = coordinator._entry
    if case == "other_entry":
        entry = MockConfigEntry(domain=DOMAIN)
        entry.add_to_hass(hass)
    switch = LocalThingsSwitch(coordinator, bound)
    assert switch.unique_id is not None
    registry = er.async_get(hass)
    old = registry.async_get_or_create("switch", DOMAIN, switch.unique_id, config_entry=entry)

    _drop_unsupported_oven_sound_switches(hass, coordinator._entry, coordinator)

    assert registry.async_get(old.entity_id) is not None
