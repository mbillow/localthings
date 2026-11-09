"""Only Boolean buzzers replace their legacy select on setup."""

from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_boolean_buzzer_selects
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

from .conftest import ENTRY_DATA, MOCK_DEVICE_KEY
from .test_offline_setup import _reachable, _unreachable


def _resources(vocabulary=("On", "Off")):
    resources = _load_device("washer")
    resources["/buzzersound/vs/0"] = {
        "supportedBuzzerSound": list(vocabulary),
        "setBuzzerSound": vocabulary[0],
    }
    return resources


def _coordinator(resources):
    registry = resolve(resources)
    return cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            device_key=MOCK_DEVICE_KEY,
            discovery_resources=resources,
            discovery_canonical=lambda subdevice: resources,
            bound=discover(resources, registry.capabilities, registry.pattern_capabilities)
            if registry
            else [],
        ),
    )


def _row(hass, entry, domain="select", key="buzzer_sound", **kwargs):
    return er.async_get(hass).async_get_or_create(
        domain, DOMAIN, f"{DOMAIN}_{MOCK_DEVICE_KEY}_{key}", config_entry=entry, **kwargs
    )


@pytest.mark.parametrize("disabled_by", [None, er.RegistryEntryDisabler.USER])
async def test_removes_renamed_boolean_select_idempotently(hass, mock_entry, disabled_by, caplog):
    old = _row(hass, mock_entry, suggested_object_id="renamed_buzzer", disabled_by=disabled_by)
    replacement = _row(hass, mock_entry, "switch")
    unrelated = _row(hass, mock_entry, key="finish_sound")
    coordinator = _coordinator(_resources())
    _drop_boolean_buzzer_selects(hass, mock_entry, coordinator)
    _drop_boolean_buzzer_selects(hass, mock_entry, coordinator)
    registry = er.async_get(hass)
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get(replacement.entity_id) == replacement
    assert registry.async_get(unrelated.entity_id) == unrelated


@pytest.mark.parametrize(
    "resources",
    [
        _resources(("Volume_Off", "Volume_High")),
        {},
        {"/buzzersound/vs/0": {}},
        {"/buzzersound/vs/0": {"href": "/buzzersound/vs/0"}},
    ],
)
async def test_keeps_select_without_boolean_replacement(hass, mock_entry, resources):
    old = _row(hass, mock_entry)
    _drop_boolean_buzzer_selects(hass, mock_entry, _coordinator(resources))
    assert er.async_get(hass).async_get(old.entity_id) == old


async def test_preserves_other_entry(hass, mock_entry):
    other = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=4)
    other.add_to_hass(hass)
    old = _row(hass, other)
    _drop_boolean_buzzer_selects(hass, mock_entry, _coordinator(_resources()))
    assert er.async_get(hass).async_get(old.entity_id) == old


@pytest.mark.parametrize("offline", [False, True])
async def test_setup_migrates_boolean_buzzer(hass, mock_entry, offline):
    resources = _resources()
    if offline:
        with _reachable(resources):
            assert await hass.config_entries.async_setup(mock_entry.entry_id)
            await hass.async_block_till_done()
            assert await hass.config_entries.async_unload(mock_entry.entry_id)
            await hass.async_block_till_done()
    old = _row(hass, mock_entry, suggested_object_id="renamed_buzzer")
    with _unreachable() if offline else _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get_entity_id("switch", DOMAIN, old.unique_id) is not None
