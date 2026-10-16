"""Remove the verified legacy cooktop's duplicate countdown on upgrade."""

from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_duplicate_cooktop_timer
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

from .conftest import ENTRY_DATA, MOCK_DEVICE_KEY
from .test_offline_setup import _reachable, _unreachable

NAME = "cooktop_tp6x_ct_16k_8888"


def _coordinator(resources):
    registry = resolve(resources)
    bound = (
        discover(resources, registry.capabilities, registry.pattern_capabilities)
        if registry
        else []
    )
    return cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            device_key=MOCK_DEVICE_KEY,
            discovery_resources=resources,
            discovery_canonical=lambda subdevice: resources,
            bound=bound,
        ),
    )


def _row(hass, entry, key, **kwargs):
    return er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{MOCK_DEVICE_KEY}_{key}", config_entry=entry, **kwargs
    )


@pytest.mark.parametrize("disabled_by", [None, er.RegistryEntryDisabler.USER])
async def test_removes_renamed_duplicate_and_preserves_remaining(hass, mock_entry, disabled_by):
    old = _row(
        hass,
        mock_entry,
        "main_timer_current",
        suggested_object_id="old_countdown",
        disabled_by=disabled_by,
    )
    remaining = _row(hass, mock_entry, "main_timer_set", suggested_object_id="kitchen_countdown")
    status = _row(hass, mock_entry, "main_timer_state")
    unrelated = _row(hass, mock_entry, "main_timer_current_extra")
    coordinator = _coordinator(_load_device(NAME))

    _drop_duplicate_cooktop_timer(hass, mock_entry, coordinator)
    _drop_duplicate_cooktop_timer(hass, mock_entry, coordinator)

    registry = er.async_get(hass)
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get(remaining.entity_id) == remaining
    assert registry.async_get(status.entity_id) == status
    assert registry.async_get(unrelated.entity_id) == unrelated


@pytest.mark.parametrize("name", ["cooktop", "cooktop_nv9300k", "cooktop_nv8000t"])
async def test_keeps_other_cooktop_families(hass, mock_entry, name):
    old = _row(hass, mock_entry, "main_timer_current")

    _drop_duplicate_cooktop_timer(hass, mock_entry, _coordinator(_load_device(name)))

    assert er.async_get(hass).async_get(old.entity_id) == old


async def test_waits_for_replacement_evidence(hass, mock_entry):
    old = _row(hass, mock_entry, "main_timer_current")
    resources = _load_device(NAME)
    options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
    options[:] = [option for option in options if not option.startswith("MainTimerSet_")]

    _drop_duplicate_cooktop_timer(hass, mock_entry, _coordinator(resources))
    _drop_duplicate_cooktop_timer(hass, mock_entry, _coordinator({}))

    assert er.async_get(hass).async_get(old.entity_id) == old


async def test_keeps_row_owned_by_another_entry(hass, mock_entry):
    other = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=4)
    other.add_to_hass(hass)
    old = _row(hass, other, "main_timer_current")

    _drop_duplicate_cooktop_timer(hass, mock_entry, _coordinator(_load_device(NAME)))

    assert er.async_get(hass).async_get(old.entity_id) == old


@pytest.mark.parametrize("offline", [False, True])
async def test_setup_removes_duplicate_without_recreating_it(hass, mock_entry, offline):
    resources = _load_device(NAME)
    if offline:
        with _reachable(resources):
            assert await hass.config_entries.async_setup(mock_entry.entry_id)
            await hass.async_block_till_done()
            assert await hass.config_entries.async_unload(mock_entry.entry_id)
            await hass.async_block_till_done()

    old = _row(hass, mock_entry, "main_timer_current", suggested_object_id="old_countdown")
    remaining = _row(hass, mock_entry, "main_timer_set", suggested_object_id="kitchen_countdown")
    with _unreachable() if offline else _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get(remaining.entity_id) is not None
    assert hass.states.get(remaining.entity_id) is not None
    assert registry.async_get_entity_id("sensor", DOMAIN, old.unique_id) is None
