"""Remove the raw burner mask on upgrade where the decoded burners replace it."""

from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_raw_burner_mask
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device
from tests.test_range_ny9100dw_capabilities import _OPTIONS, DEVICE_TYPES, _resources

from .conftest import ENTRY_DATA, MOCK_DEVICE_KEY
from .test_offline_setup import _reachable, _unreachable

RAW = "cooktop_monitoring"


def _coordinator(resources):
    registry = resolve(resources, device_types=DEVICE_TYPES)
    assert registry is not None
    return cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            device_key=MOCK_DEVICE_KEY,
            discovery_resources=resources,
            discovery_canonical=lambda subdevice: resources,
            bound=discover(resources, registry.capabilities, registry.pattern_capabilities),
        ),
    )


def _row(hass, entry, key, **kwargs):
    return er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{MOCK_DEVICE_KEY}_{key}", config_entry=entry, **kwargs
    )


@pytest.mark.parametrize("disabled_by", [None, er.RegistryEntryDisabler.USER])
async def test_removes_renamed_raw_mask_and_preserves_decoded(hass, mock_entry, disabled_by):
    old = _row(hass, mock_entry, RAW, suggested_object_id="old_mask", disabled_by=disabled_by)
    decoded = _row(hass, mock_entry, "active_burners", suggested_object_id="kitchen_burners")
    unrelated = _row(hass, mock_entry, f"{RAW}_extra")
    coordinator = _coordinator(_resources("1"))

    _drop_raw_burner_mask(hass, mock_entry, coordinator)
    _drop_raw_burner_mask(hass, mock_entry, coordinator)

    registry = er.async_get(hass)
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get(decoded.entity_id) == decoded
    assert registry.async_get(unrelated.entity_id) == unrelated


async def test_removes_raw_mask_on_a_fuel_gas_board(hass, mock_entry):
    old = _row(hass, mock_entry, RAW)
    decoded = _row(hass, mock_entry, "active_burners")
    resources = _load_device("range_nx60t8311ss")
    assert "Fuel_Gas" in resources["/mode/vs/0"]["x.com.samsung.da.options"]

    _drop_raw_burner_mask(hass, mock_entry, _coordinator(resources))

    registry = er.async_get(hass)
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get(decoded.entity_id) == decoded


@pytest.mark.parametrize(
    "name",
    [
        "range_ne63a6111ss",
        "range_ne63a6511",
        "range_ne6516a",
        "range_ne8300d",
        "range_no_info",
        "range_tp1x_da_ks_range_0101x",
    ],
)
async def test_keeps_boards_still_on_the_raw_sensor(hass, mock_entry, name):
    old = _row(hass, mock_entry, RAW)

    _drop_raw_burner_mask(hass, mock_entry, _coordinator(_load_device(name)))

    assert er.async_get(hass).async_get(old.entity_id) == old


async def test_keeps_ny_board_reporting_fuel_electric(hass, mock_entry):
    old = _row(hass, mock_entry, RAW)
    resources = _resources("1", [*_OPTIONS, "Fuel_Electric"])

    _drop_raw_burner_mask(hass, mock_entry, _coordinator(resources))

    assert er.async_get(hass).async_get(old.entity_id) == old


async def test_keeps_board_reporting_burner_list(hass, mock_entry):
    old = _row(hass, mock_entry, RAW)
    resources = _resources("1")
    resources["/cooktop/status/vs/0"] = {"burnerList": [{"burnerNumber": 0, "powerLevel": "3"}]}

    _drop_raw_burner_mask(hass, mock_entry, _coordinator(resources))

    assert er.async_get(hass).async_get(old.entity_id) == old


@pytest.mark.parametrize("href", ["/mode/vs/0", "/cooktopmonitoring/vs/0"])
async def test_waits_while_evidence_is_a_stub(hass, mock_entry, href):
    """A stub passes `_mask_decoded`'s optimistic gate on an electric board too."""
    old = _row(hass, mock_entry, RAW)
    resources = _resources("1")
    resources[href] = {"href": href}

    _drop_raw_burner_mask(hass, mock_entry, _coordinator(resources))

    assert er.async_get(hass).async_get(old.entity_id) == old


async def test_keeps_row_owned_by_another_entry(hass, mock_entry):
    other = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=4)
    other.add_to_hass(hass)
    old = _row(hass, other, RAW)

    _drop_raw_burner_mask(hass, mock_entry, _coordinator(_resources("1")))

    assert er.async_get(hass).async_get(old.entity_id) == old


@pytest.mark.parametrize("offline", [False, True])
async def test_setup_removes_raw_mask_without_recreating_it(hass, mock_entry, offline):
    # A full capture routes without an /oic/d identity, which `_resources` needs.
    resources = _load_device("range_nx60t8311ss")
    resources["/mode/vs/0"]["x.com.samsung.da.options"] = list(_OPTIONS)
    if offline:
        with _reachable(resources):
            assert await hass.config_entries.async_setup(mock_entry.entry_id)
            await hass.async_block_till_done()
            assert await hass.config_entries.async_unload(mock_entry.entry_id)
            await hass.async_block_till_done()

    old = _row(hass, mock_entry, RAW, suggested_object_id="old_mask")
    decoded = _row(hass, mock_entry, "active_burners", suggested_object_id="kitchen_burners")
    with _unreachable() if offline else _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get(decoded.entity_id) is not None
    assert hass.states.get(decoded.entity_id) is not None
    assert registry.async_get_entity_id("sensor", DOMAIN, old.unique_id) is None
