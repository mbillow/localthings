"""Older releases gave ARTIK051_PRAC air conditioners an air purify switch
that every write failed on (#747). Setup removes it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_unsupported_sound_switches
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.capabilities import airconditioner
from custom_components.localthings.registry.discovery import discover

from .conftest import LEGACY_ENTRY_DATA, MOCK_SERIAL

HREF = "/option/airpurify/vs/0"


def _coordinator(model: str) -> LocalThingsCoordinator:
    resources = {
        HREF: {"x.com.samsung.da.modes": "Off", "x.com.samsung.da.supportedModes": ["On", "Off"]},
        "/information/vs/0": {"x.com.samsung.da.modelNum": model},
    }
    return cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            bound=discover(resources, {HREF: [airconditioner.AIR_PURIFY]}),
            discovery_resources=resources,
            discovery_canonical=lambda subdevice: resources,
            device_key=MOCK_SERIAL,
            instance_names={},
        ),
    )


def _row(hass: HomeAssistant) -> tuple[MockConfigEntry, str]:
    entry = MockConfigEntry(domain=DOMAIN, data=LEGACY_ENTRY_DATA, version=4)
    entry.add_to_hass(hass)
    entity_id = (
        er.async_get(hass)
        .async_get_or_create(
            "switch", DOMAIN, f"{DOMAIN}_{MOCK_SERIAL}_air_purify", config_entry=entry
        )
        .entity_id
    )
    return entry, entity_id


async def test_the_artik051_prac_switch_is_removed(hass: HomeAssistant) -> None:
    entry, entity_id = _row(hass)

    _drop_unsupported_sound_switches(hass, entry, _coordinator("ARTIK051_PRAC_20K|10217841|6001"))

    assert er.async_get(hass).async_get(entity_id) is None


async def test_other_boards_keep_it(hass: HomeAssistant) -> None:
    entry, entity_id = _row(hass)

    _drop_unsupported_sound_switches(hass, entry, _coordinator("ARTIK051_KRAC_18K|10217841|6001"))

    assert er.async_get(hass).async_get(entity_id) is not None
