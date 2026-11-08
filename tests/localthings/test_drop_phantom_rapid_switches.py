"""Older releases gave fridges whose /refrigeration/vs/0 reports one rapid
field a switch for the other too, always off (#698). Setup removes it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_unsupported_sound_switches
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.capabilities import fridge
from custom_components.localthings.registry.discovery import discover

from .conftest import LEGACY_ENTRY_DATA, MOCK_SERIAL

HREF = "/refrigeration/vs/0"


def _coordinator(rep: dict) -> LocalThingsCoordinator:
    resources = {HREF: rep}
    return cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            bound=discover(resources, {HREF: [fridge.REFRIGERATION]}),
            discovery_resources=resources,
            discovery_canonical=lambda subdevice: resources,
            device_key=MOCK_SERIAL,
            instance_names={},
        ),
    )


def _rows(hass: HomeAssistant) -> tuple[MockConfigEntry, dict[str, str]]:
    entry = MockConfigEntry(domain=DOMAIN, data=LEGACY_ENTRY_DATA, version=4)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    rows = {
        key: registry.async_get_or_create(
            "switch", DOMAIN, f"{DOMAIN}_{MOCK_SERIAL}_{key}", config_entry=entry
        ).entity_id
        for key in ("rapid_fridge", "rapid_freezing")
    }
    return entry, rows


async def test_the_switch_for_an_unreported_field_is_removed(hass: HomeAssistant) -> None:
    entry, rows = _rows(hass)

    _drop_unsupported_sound_switches(
        hass, entry, _coordinator({"x.com.samsung.da.rapidFreezing": "Off"})
    )

    registry = er.async_get(hass)
    assert registry.async_get(rows["rapid_fridge"]) is None
    assert registry.async_get(rows["rapid_freezing"]) is not None


async def test_a_stub_keeps_both(hass: HomeAssistant) -> None:
    entry, rows = _rows(hass)

    _drop_unsupported_sound_switches(hass, entry, _coordinator({"href": HREF}))

    registry = er.async_get(hass)
    assert all(registry.async_get(entity_id) is not None for entity_id in rows.values())
