"""The sweep that drops washer rows from microfiber filters (issue #626).

Until #626 the filter shared the washer registry (#475), which gave it a
child lock, delay, start/stop buttons and cycle progress it has no hardware
behind. Same no-version-bump reasoning as test_superseded_entities.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_washer_entities_from_filters
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator

from .conftest import LEGACY_ENTRY_DATA, MOCK_SERIAL


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=LEGACY_ENTRY_DATA, version=4)
    entry.add_to_hass(hass)
    return entry


def _coordinator(device_type: str) -> LocalThingsCoordinator:
    return cast(
        LocalThingsCoordinator,
        SimpleNamespace(device_type_name=device_type, device_key=MOCK_SERIAL),
    )


def _row(hass: HomeAssistant, entry: MockConfigEntry, domain: str, key: str):
    return er.async_get(hass).async_get_or_create(
        domain, DOMAIN, f"{DOMAIN}_{MOCK_SERIAL}_{key}", config_entry=entry
    )


async def test_drops_the_washer_rows_from_a_filter(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    dropped = [
        _row(hass, entry, "binary_sensor", "child_lock"),
        _row(hass, entry, "button", "start"),
        _row(hass, entry, "number", "delay_start_hours"),
        _row(hass, entry, "sensor", "progress"),
        # Bound on other filter firmware than the two dumps; derived from
        # the registries, so these are covered without being listed.
        _row(hass, entry, "select", "cycle"),
        _row(hass, entry, "button", "diagnosis_start"),
    ]
    kept = [
        _row(hass, entry, "sensor", "filter_status"),
        _row(hass, entry, "sensor", "machine_state"),
        _row(hass, entry, "sensor", "diagnosis_status"),
        # The key matters per platform: no filter sensor is called start.
        _row(hass, entry, "sensor", "start"),
    ]

    _drop_washer_entities_from_filters(hass, entry, _coordinator("microfiber_filter"))

    ent_reg = er.async_get(hass)
    assert all(ent_reg.async_get(r.entity_id) is None for r in dropped)
    assert all(ent_reg.async_get(r.entity_id) is not None for r in kept)


async def test_leaves_a_washer_alone(hass: HomeAssistant) -> None:
    """Every key in the list is real on a washer."""
    entry = _entry(hass)
    rows = [
        _row(hass, entry, "binary_sensor", "child_lock"),
        _row(hass, entry, "button", "start"),
    ]

    _drop_washer_entities_from_filters(hass, entry, _coordinator("washer"))

    assert all(er.async_get(hass).async_get(r.entity_id) is not None for r in rows)


async def test_is_idempotent(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    _row(hass, entry, "button", "stop")
    coordinator = _coordinator("microfiber_filter")

    _drop_washer_entities_from_filters(hass, entry, coordinator)
    before = set(er.async_get(hass).entities)
    _drop_washer_entities_from_filters(hass, entry, coordinator)

    assert set(er.async_get(hass).entities) == before


async def test_runs_on_setup(hass: HomeAssistant) -> None:
    """Wired into async_setup_entry, before the platforms register."""
    from tests.conftest import _load_device

    from .test_identity_migration import UUID_A, _reachable
    from .test_identity_migration import _entry as _identity_entry

    entry = _identity_entry(hass, version=4, key=UUID_A, device_key=UUID_A)
    orphan = er.async_get(hass).async_get_or_create(
        "number", DOMAIN, f"{DOMAIN}_{UUID_A}_delay_start_hours", config_entry=entry
    )

    with _reachable(_load_device("microfiber_amf"), UUID_A):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert er.async_get(hass).async_get(orphan.entity_id) is None
