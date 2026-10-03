"""Registry cleanup for microwave controls replaced by a light entity."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_microwave_entities_superseded_by_light
from custom_components.localthings.const import CONF_DEVICE_TYPE, DOMAIN

from .conftest import LEGACY_ENTRY_DATA, MOCK_SERIAL


def _entry(hass: HomeAssistant, device_type: str = "microwave") -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**LEGACY_ENTRY_DATA, CONF_DEVICE_TYPE: device_type},
        version=4,
    )
    entry.add_to_hass(hass)
    return entry


def _row(hass: HomeAssistant, entry: MockConfigEntry, domain: str, unique_suffix: str):
    return er.async_get(hass).async_get_or_create(
        domain,
        DOMAIN,
        f"{DOMAIN}_{MOCK_SERIAL}_{unique_suffix}",
        config_entry=entry,
    )


async def test_drops_the_superseded_switch_and_select(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    switch = _row(hass, entry, "switch", "lamp")
    select = _row(hass, entry, "select", "lamp_level")

    _drop_microwave_entities_superseded_by_light(hass, entry)

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get(switch.entity_id) is None
    assert ent_reg.async_get(select.entity_id) is None


async def test_leaves_the_replacement_and_unrelated_rows(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    replacement = _row(hass, entry, "light", "lamp")
    unrelated_switch = _row(hass, entry, "switch", "sound")
    unrelated_select = _row(hass, entry, "select", "cooking_mode")

    _drop_microwave_entities_superseded_by_light(hass, entry)

    ent_reg = er.async_get(hass)
    for row in (replacement, unrelated_switch, unrelated_select):
        assert ent_reg.async_get(row.entity_id) is not None


async def test_does_not_remove_an_oven_lamp_switch(hass: HomeAssistant) -> None:
    entry = _entry(hass, "oven")
    lamp = _row(hass, entry, "switch", "lamp")

    _drop_microwave_entities_superseded_by_light(hass, entry)

    assert er.async_get(hass).async_get(lamp.entity_id) is not None


async def test_matches_subdevice_and_instance_keys(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    switch = _row(hass, entry, "switch", "subdevice_uuid_lamp_2")
    select = _row(hass, entry, "select", "subdevice_uuid_lamp_level_2")

    _drop_microwave_entities_superseded_by_light(hass, entry)

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get(switch.entity_id) is None
    assert ent_reg.async_get(select.entity_id) is None


async def test_is_scoped_to_one_entry_and_idempotent(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    other = _entry(hass)
    _row(hass, entry, "switch", "lamp")
    theirs = _row(hass, other, "switch", "lamp")

    _drop_microwave_entities_superseded_by_light(hass, entry)
    before = set(er.async_get(hass).entities)
    _drop_microwave_entities_superseded_by_light(hass, entry)

    assert set(er.async_get(hass).entities) == before
    assert er.async_get(hass).async_get(theirs.entity_id) is not None


async def test_logs_each_removed_entity(hass: HomeAssistant, caplog) -> None:
    entry = _entry(hass)
    switch = _row(hass, entry, "switch", "lamp")
    select = _row(hass, entry, "select", "lamp_level")

    with caplog.at_level(logging.INFO, logger="custom_components.localthings"):
        _drop_microwave_entities_superseded_by_light(hass, entry)

    assert switch.entity_id in caplog.text
    assert select.entity_id in caplog.text
