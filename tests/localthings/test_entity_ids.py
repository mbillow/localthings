"""What HA actually names the entities this integration registers.

Every other naming test checks an input to that -- a translation key, a
device name, a unique_id. This one checks the output: it sets an entry up
for real and reads the entity_ids out of the registry, which is the only
place the two halves (device name + translated entity name) are combined.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import MOCK_DEVICE_KEY, MOCK_MODEL


async def _entity_ids(hass: HomeAssistant, entry) -> list[str]:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    return sorted(e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id))


async def test_entity_ids_read_as_device_type_plus_entity(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    ids = await _entity_ids(hass, mock_entry)

    assert "sensor.samsung_refrigerator_energy" in ids
    assert "binary_sensor.samsung_refrigerator_door_freezer_open" in ids


async def test_no_entity_id_carries_the_device_key(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """The device key is a 36-character UUID since issue #381, and every
    entity's unique_id is minted from it -- but HA builds entity_ids from
    the device and entity names, so it stays internal. An entity that
    registers with no name is the one way it leaks: HA appends a literal
    'None' to the device name, and falls back to `<platform>_<unique_id>`
    outright for an entity with no device. test_translations stops that at
    the source by requiring a catalog entry per descriptor; this is the
    same check at the far end of the pipeline.
    """
    ids = await _entity_ids(hass, mock_entry)

    assert ids
    key_fragment = MOCK_DEVICE_KEY.split("-")[0]
    assert not [entity_id for entity_id in ids if key_fragment in entity_id]
    assert not [entity_id for entity_id in ids if "none" in entity_id.split(".")[1].split("_")]


async def test_the_board_model_stays_off_the_entity_id(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """modelNum is a board string ('ARTIK051_DONGLE_REF'), so it belongs on
    the device's `model` field, not slugified into 46 entity_ids."""
    ids = await _entity_ids(hass, mock_entry)

    slug = MOCK_MODEL.lower().replace("-", "_")
    assert not [entity_id for entity_id in ids if slug in entity_id]
