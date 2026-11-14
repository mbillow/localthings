"""A sub-unit kept registered past the first live discovery (#850) keeps
its capability attributes.

A silent or short-answering sub-unit stays registered and unavailable, and
Home Assistant still publishes its capabilities, which HomeKit Bridge bakes
into an accessory once. Only hrefs the run still holds as unconfirmed stand
in from the snapshot; one let go of stays gone.
"""

from unittest.mock import patch

from custom_components.localthings.climate import LocalThingsClimate
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.entities import ClimateDesc
from custom_components.localthings.registry.subdevices import MAIN

from .test_offline_setup import _first_live_cycle, _later_cycles, _two_unit_ac_snapshot


def _capabilities(hass, coordinator) -> dict[str, tuple]:
    """Per subdevice key, what a snapshotting consumer would keep."""
    out = {}
    for bound in coordinator.bound:
        if isinstance(bound.desc, ClimateDesc):
            entity = LocalThingsClimate(coordinator, bound)
            entity.hass = hass
            out[bound.subdevice.key] = (
                entity.hvac_modes,
                entity.fan_modes,
                entity.swing_modes,
                entity.min_temp,
                entity.max_temp,
            )
    return out


def _values(hass, coordinator, key) -> tuple:
    for bound in coordinator.bound:
        if isinstance(bound.desc, ClimateDesc) and bound.subdevice.key == key:
            entity = LocalThingsClimate(coordinator, bound)
            entity.hass = hass
            return entity.current_temperature, entity.target_temperature, entity.fan_mode
    raise AssertionError(key)


async def _reference(hass, mock_entry, hass_storage, full, materialized):
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    assert await coordinator.async_rehydrate()
    await _first_live_cycle(hass, coordinator, full, materialized)
    expected = _capabilities(hass, coordinator)
    _two_unit_ac_snapshot(hass_storage, mock_entry)
    return expected


async def test_a_silent_sub_unit_keeps_its_capabilities(hass, mock_entry, hass_storage) -> None:
    full, _, materialized = _two_unit_ac_snapshot(hass_storage, mock_entry)
    (sub,) = materialized
    expected = await _reference(hass, mock_entry, hass_storage, full, materialized)
    assert all(modes[0] and modes[1] for modes in expected.values())

    main_only = {href: rep for href, rep in full.items() if not sub.owns(href)}
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    assert await coordinator.async_rehydrate()
    await _first_live_cycle(hass, coordinator, main_only, [])
    await _later_cycles(hass, coordinator, main_only, 2)

    assert _capabilities(hass, coordinator) == expected
    # The silent sub-unit isn't in coordinator.subdevices, but its held hrefs
    # still belong to it: MAIN's whole-view scans mustn't see them.
    assert not any(sub.owns(href) for href in coordinator.capability_canonical(MAIN))
    assert _values(hass, coordinator, sub.key) == (None, None, None)


async def test_a_short_sub_unit_keeps_what_it_missed(hass, mock_entry, hass_storage) -> None:
    full, short, materialized = _two_unit_ac_snapshot(hass_storage, mock_entry)
    (sub,) = materialized
    expected = await _reference(hass, mock_entry, hass_storage, full, materialized)

    coordinator = LocalThingsCoordinator(hass, mock_entry)
    assert await coordinator.async_rehydrate()
    await _first_live_cycle(hass, coordinator, short, materialized)
    await _later_cycles(hass, coordinator, short, 2)

    assert _capabilities(hass, coordinator)[sub.key] == expected[sub.key]


async def test_an_href_let_go_of_stops_standing_in(hass, mock_entry, hass_storage) -> None:
    """Once the owner has answered long enough without an href, the
    snapshot lets it go -- and so does the capability view."""
    full, short, materialized = _two_unit_ac_snapshot(hass_storage, mock_entry)
    (sub,) = materialized
    expected = await _reference(hass, mock_entry, hass_storage, full, materialized)

    coordinator = LocalThingsCoordinator(hass, mock_entry)
    assert await coordinator.async_rehydrate()
    await _first_live_cycle(hass, coordinator, short, materialized)
    with patch.object(LocalThingsCoordinator, "_PRUNE_AFTER_CYCLES", 3):
        await _later_cycles(hass, coordinator, short, 4)

    assert not coordinator._unconfirmed
    _, fan_modes, *_ = _capabilities(hass, coordinator)[sub.key]
    assert expected[sub.key][1] and not fan_modes
