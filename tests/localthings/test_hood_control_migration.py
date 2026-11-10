"""Hood controls remove only the legacy rows they actually replace."""

from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.cleanup import async_reconcile_entities
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

from .conftest import ENTRY_DATA, MOCK_DEVICE_KEY
from .test_offline_setup import _reachable, _unreachable

MOVED = [
    ("binary_sensor", "switch", "periodic_air_sensing"),
    ("sensor", "select", "automatic_ventilation_state"),
]


def _coordinator(resources):
    registry = resolve(resources)
    return cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            device_key=MOCK_DEVICE_KEY,
            device_type_name="range_hood",
            subdevices=[],
            discovery_resources=resources,
            discovery_canonical=lambda subdevice: resources,
            bound=discover(resources, registry.capabilities, registry.pattern_capabilities)
            if registry
            else [],
        ),
    )


def _row(hass, entry, domain, key, **kwargs):
    return er.async_get(hass).async_get_or_create(
        domain, DOMAIN, f"{DOMAIN}_{MOCK_DEVICE_KEY}_{key}", config_entry=entry, **kwargs
    )


@pytest.mark.parametrize("old_domain,new_domain,key", MOVED)
@pytest.mark.parametrize("disabled_by", [None, er.RegistryEntryDisabler.USER])
async def test_removes_renamed_read_only_control_idempotently(
    hass, mock_entry, old_domain, new_domain, key, disabled_by
):
    old = _row(
        hass,
        mock_entry,
        old_domain,
        key,
        suggested_object_id="renamed_hood_control",
        disabled_by=disabled_by,
    )
    replacement = _row(hass, mock_entry, new_domain, key)
    sibling = _row(hass, mock_entry, "sensor", "air_sensing_state")
    coordinator = _coordinator(_load_device("range_hood"))
    async_reconcile_entities(hass, mock_entry, coordinator)
    async_reconcile_entities(hass, mock_entry, coordinator)
    registry = er.async_get(hass)
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get(replacement.entity_id) == replacement
    assert registry.async_get(sibling.entity_id) == sibling


@pytest.mark.parametrize(
    "resources",
    [
        {},
        {"/airlevelcheck/vs/0": {}},
        {"/airlevelcheck/vs/0": {"href": "/airlevelcheck/vs/0"}},
        _load_device("air_purifier"),
    ],
)
async def test_keeps_rows_without_discovered_hood_replacement(hass, mock_entry, resources):
    rows = [_row(hass, mock_entry, old_domain, key) for old_domain, _, key in MOVED]
    async_reconcile_entities(hass, mock_entry, _coordinator(resources))
    assert all(er.async_get(hass).async_get(row.entity_id) == row for row in rows)


async def test_preserves_other_entry(hass, mock_entry):
    other = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=4)
    other.add_to_hass(hass)
    old = _row(hass, other, "sensor", "automatic_ventilation_state")
    async_reconcile_entities(hass, mock_entry, _coordinator(_load_device("range_hood")))
    assert er.async_get(hass).async_get(old.entity_id) == old


@pytest.mark.parametrize("offline", [False, True])
async def test_setup_migrates_hood_controls(hass, mock_entry, offline):
    resources = _load_device("range_hood")
    if offline:
        with _reachable(resources):
            assert await hass.config_entries.async_setup(mock_entry.entry_id)
            await hass.async_block_till_done()
            assert await hass.config_entries.async_unload(mock_entry.entry_id)
            await hass.async_block_till_done()
    old_rows = [
        (new_domain, _row(hass, mock_entry, old_domain, key))
        for old_domain, new_domain, key in MOVED
    ]
    with _unreachable() if offline else _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
    registry = er.async_get(hass)
    for new_domain, old in old_rows:
        assert registry.async_get(old.entity_id) is None
        assert registry.async_get_entity_id(new_domain, DOMAIN, old.unique_id) is not None


@pytest.mark.parametrize("missing", ["empty_mode", "missing_activation"])
async def test_removes_old_platform_even_when_replacement_is_excluded(
    hass, mock_entry, missing, caplog
):
    resources = _load_device("range_hood")
    rep = resources["/airlevelcheck/vs/0"]
    if missing == "empty_mode":
        rep["x.com.samsung.da.autoExeState"] = ""
    else:
        rep.pop("x.com.samsung.da.periodicSensingActivationState", None)
    rows = [_row(hass, mock_entry, domain, key) for domain, _, key in MOVED]
    with caplog.at_level("INFO", logger="custom_components.localthings.cleanup"):
        removed = async_reconcile_entities(hass, mock_entry, _coordinator(resources))
    assert set(removed) == {row.entity_id for row in rows}
    for row in rows:
        assert er.async_get(hass).async_get(row.entity_id) is None
        assert f"Removing {row.entity_id}: no longer provided on this platform;" in caplog.text
