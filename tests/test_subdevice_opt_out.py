"""Switching a discovered subdevice off in the options flow (#670, #681)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _async_options_updated
from custom_components.localthings.config_flow import _subdevice_order
from custom_components.localthings.const import (
    CONF_BYPASS_REMOTE_CONTROL,
    CONF_DISABLED_SUBDEVICES,
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from tests.test_subdevice_discovery import ENTRY_DATA, _discover

FIXTURE = "airconditioner_artik051_dongle_fac_18k"


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
    return enable_custom_integrations


def _coordinator(hass: HomeAssistant, options=None) -> LocalThingsCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options=options or {},
        unique_id="localthings_SUBDEVICE-TEST",
    )
    entry.add_to_hass(hass)
    return LocalThingsCoordinator(hass, entry)


async def test_a_disabled_subdevice_binds_nothing_and_leaves_no_gap(hass: HomeAssistant):
    coordinator = _coordinator(hass, {CONF_DISABLED_SUBDEVICES: {"1": "Bedroom"}})
    await _discover(coordinator, FIXTURE)

    assert coordinator.subdevices == []
    assert [sub.key for sub in coordinator.disabled_subdevices] == ["1"]
    assert all(b.subdevice.key != "1" for b in coordinator.bound)
    assert not any(href.endswith("/1") for href in coordinator._unbound_hrefs)
    assert not coordinator.resource("/mode/vs/1")


async def test_without_the_option_every_live_subdevice_is_kept(hass: HomeAssistant):
    coordinator = _coordinator(hass)
    await _discover(coordinator, FIXTURE)

    assert [sub.key for sub in coordinator.subdevices] == ["1"]
    assert coordinator.disabled_subdevices == []


async def _loaded(hass: HomeAssistant, options=None) -> LocalThingsCoordinator:
    coordinator = _coordinator(hass, options)
    await _discover(coordinator, FIXTURE)
    hass.data.setdefault(DOMAIN, {})[coordinator._entry.entry_id] = coordinator
    return coordinator


async def _open_subdevices(hass: HomeAssistant, entry_id: str):
    result = await hass.config_entries.options.async_init(entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "subdevices"}
    )


def _offered(result) -> list[tuple[str, str]]:
    schema = result["data_schema"]
    assert schema is not None
    selector = schema.schema["enabled_subdevices"]
    return [(o["value"], o["label"]) for o in selector.config["options"]]


async def test_the_menu_offers_subdevices_only_when_there_are_some(hass: HomeAssistant):
    coordinator = await _loaded(hass)
    result = await hass.config_entries.options.async_init(coordinator._entry.entry_id)
    assert "subdevices" in cast(Iterable[str], result["menu_options"])

    coordinator.subdevices = []
    result = await hass.config_entries.options.async_init(coordinator._entry.entry_id)
    assert "subdevices" not in cast(Iterable[str], result["menu_options"])


async def test_unticking_removes_the_device_and_saves_its_name(hass: HomeAssistant):
    coordinator = await _loaded(hass, {CONF_BYPASS_REMOTE_CONTROL: True})
    entry = coordinator._entry
    registry = dr.async_get(hass)
    sub_device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={coordinator.subdevice_identifier("1")},
        name="Bedroom",
    )

    result = await _open_subdevices(hass, entry.entry_id)
    assert _offered(result) == [("1", "Bedroom")]
    schema = result["data_schema"]
    assert schema is not None
    assert schema({})["enabled_subdevices"] == ["1"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"enabled_subdevices": []}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_DISABLED_SUBDEVICES] == {"1": "Bedroom"}
    assert entry.options[CONF_BYPASS_REMOTE_CONTROL] is True
    assert registry.async_get(sub_device.id) is None


async def test_a_disabled_subdevice_keeps_its_name_and_can_be_ticked_again(
    hass: HomeAssistant,
):
    coordinator = await _loaded(hass, {CONF_DISABLED_SUBDEVICES: {"1": "Bedroom"}})
    entry = coordinator._entry

    result = await _open_subdevices(hass, entry.entry_id)
    assert _offered(result) == [("1", "Bedroom")]
    schema = result["data_schema"]
    assert schema is not None
    assert schema({})["enabled_subdevices"] == []

    await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"enabled_subdevices": ["1"]}
    )

    assert entry.options[CONF_DISABLED_SUBDEVICES] == {}


async def test_a_key_not_found_this_time_is_still_offered(hass: HomeAssistant):
    coordinator = await _loaded(hass, {CONF_DISABLED_SUBDEVICES: {"7": "Garage"}})
    entry = coordinator._entry

    result = await _open_subdevices(hass, entry.entry_id)
    assert ("7", "Garage") in _offered(result)

    await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"enabled_subdevices": ["1"]}
    )

    assert entry.options[CONF_DISABLED_SUBDEVICES] == {"7": "Garage"}


async def test_saving_settings_keeps_the_subdevice_choice(hass: HomeAssistant):
    coordinator = await _loaded(hass, {CONF_DISABLED_SUBDEVICES: {"1": ""}})
    entry = coordinator._entry

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_BYPASS_REMOTE_CONTROL: True}
    )

    assert entry.options[CONF_DISABLED_SUBDEVICES] == {"1": ""}


async def test_the_listener_reloads_only_when_the_choice_changes(hass: HomeAssistant):
    coordinator = await _loaded(hass)
    entry = coordinator._entry

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await _async_options_updated(hass, entry)
        reload.assert_not_called()

        hass.config_entries.async_update_entry(
            entry, options={CONF_DISABLED_SUBDEVICES: {"1": "Bedroom"}}
        )
        await _async_options_updated(hass, entry)
        reload.assert_called_once_with(entry.entry_id)


def test_indexed_subdevices_sort_numerically():
    assert sorted(["10", "2", "1", "abc-uuid"], key=_subdevice_order) == [
        "1",
        "2",
        "10",
        "abc-uuid",
    ]
