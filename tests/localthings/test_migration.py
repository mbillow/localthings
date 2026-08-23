"""Config-entry migration and the placeholder-identity repair (issue #236).

The move onto the OCF device UUID (issue #381) has its own suite in
test_identity_migration.py -- it is the one migration step that can't
finish inside async_migrate_entry, so it needs a device to talk to.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_HOST,
    CONF_SERIAL,
    DOMAIN,
)

from .conftest import LEGACY_ENTRY_DATA, MOCK_HOST, MOCK_PORT, MOCK_SERIAL


def _legacy_entry(hass: HomeAssistant, unique_id: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=LEGACY_ENTRY_DATA,
        unique_id=unique_id,
        version=1,
    )
    entry.add_to_hass(hass)
    return entry


async def test_migration_recovers_serial_from_unique_id(
    hass: HomeAssistant, mock_coordinator_session
) -> None:
    """A v1 entry's identity is recoverable without reaching the device: the
    config flow has always keyed the entry's unique_id on the serial its probe
    read."""
    entry = _legacy_entry(hass, f"{DOMAIN}_{MOCK_SERIAL}")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Straight through to the current version: v2 -> v3 is a statistics
    # relabel that no-ops for a family without particulate sensors, v3 -> v4
    # only records the legacy key for the coordinator to re-key from once a
    # poll produces an OCF device id, and v4 -> v5 no-ops for an entry with
    # no dry_level sensor to drop.
    assert entry.version == 5
    assert entry.data[CONF_SERIAL] == MOCK_SERIAL


async def test_migration_collapses_the_host_port_unique_id(hass: HomeAssistant) -> None:
    """A board with no usable serial (issues #83/#189) used to be keyed two
    different ways at once: `host:port` on the config entry, `host` in the
    device and entity registries. Migration collapses the entry onto the
    registry's form, so the two finally name the same thing."""
    from custom_components.localthings import async_migrate_entry

    entry = _legacy_entry(hass, f"{DOMAIN}_{MOCK_HOST}:{MOCK_PORT}")

    assert await async_migrate_entry(hass, entry) is True

    assert entry.data[CONF_SERIAL] == MOCK_HOST
    assert entry.unique_id == f"{DOMAIN}_{MOCK_HOST}"


async def test_migration_resolves_a_placeholder_serial_unique_id(hass: HomeAssistant) -> None:
    """An entry created before the placeholder rules landed was keyed on the
    placeholder itself (issues #83/#189), while the coordinator has been
    resolving those boards to the host ever since. The unique_id records what
    the flow believed then, not what the registry holds -- taking it at face
    value would re-key working devices back onto a string every unit of the
    family reports, which is the collision those issues are about."""
    from custom_components.localthings import async_migrate_entry

    entry = _legacy_entry(hass, f"{DOMAIN}_Nothing(SVC)")
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_HOST)},
    )

    assert await async_migrate_entry(hass, entry) is True

    assert entry.data[CONF_SERIAL] == MOCK_HOST
    assert entry.unique_id == f"{DOMAIN}_{MOCK_HOST}"
    unchanged = dev_reg.async_get(device.id)
    assert unchanged is not None
    assert unchanged.identifiers == {(DOMAIN, MOCK_HOST)}


async def test_migration_resolves_an_all_hex_placeholder_unique_id(hass: HomeAssistant) -> None:
    """The issue #189 flash-unset sentinel, same reasoning."""
    from custom_components.localthings import async_migrate_entry

    entry = _legacy_entry(hass, f"{DOMAIN}_FFFFFFFFFFFFFFF")

    assert await async_migrate_entry(hass, entry) is True

    assert entry.data[CONF_SERIAL] == MOCK_HOST


async def test_migration_keeps_a_survivor_off_a_removed_duplicate_device(
    hass: HomeAssistant,
) -> None:
    """Removing a device takes its entities with it (entity_registry's
    async_device_modified), so an entity that came through the pass above
    re-keyed rather than removed has to move to the surviving device first --
    otherwise the rewrite that exists to preserve an entity_id, name and area
    destroys all three a few lines later.

    Migration is called directly here: what the repair leaves behind is the
    contract, and going through async_setup would let the platform re-adding
    its entities hide a row that had in fact been deleted."""
    from custom_components.localthings import async_migrate_entry

    entry = _legacy_entry(hass, f"{DOMAIN}_{MOCK_SERIAL}")
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    real_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_SERIAL)},
    )
    orphan_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_HOST)},
    )
    # The serial-keyed device exists, but this entity's serial-keyed *key* is
    # free -- e.g. the user deleted the visible duplicate by hand -- so the
    # entity pass rewrites it instead of removing it.
    survivor = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MOCK_HOST}_connection_mode",
        config_entry=entry,
        device_id=orphan_device.id,
        suggested_object_id="kitchen_fridge_connection",
    )

    assert await async_migrate_entry(hass, entry) is True
    await hass.async_block_till_done()

    assert dev_reg.async_get(orphan_device.id) is None
    kept = ent_reg.async_get(survivor.entity_id)
    assert kept is not None
    assert kept.entity_id == "sensor.kitchen_fridge_connection"
    assert kept.unique_id == f"{DOMAIN}_{MOCK_SERIAL}_connection_mode"
    assert kept.device_id == real_device.id


async def test_migration_rekeys_an_ip_keyed_device_and_entity(
    hass: HomeAssistant, mock_coordinator_session
) -> None:
    """The registry entries the old placeholder identity minted are rewritten
    in place, so an orphan keeps its entity_id, name, area and every
    automation that referenced it -- rather than being replaced by a
    serial-keyed duplicate with a `_2` suffix while it sits permanently
    unavailable (issue #236)."""
    entry = _legacy_entry(hass, f"{DOMAIN}_{MOCK_SERIAL}")
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    orphan_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_HOST)},
        name=f"Samsung Appliance ({MOCK_HOST})",
    )
    orphan_entity = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MOCK_HOST}_connection_mode",
        config_entry=entry,
        device_id=orphan_device.id,
    )

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Same registry rows, now keyed on the real identity.
    rekeyed_device = dev_reg.async_get(orphan_device.id)
    assert rekeyed_device is not None
    assert rekeyed_device.identifiers == {(DOMAIN, MOCK_SERIAL)}
    rekeyed = ent_reg.async_get(orphan_entity.entity_id)
    assert rekeyed is not None
    assert rekeyed.unique_id == f"{DOMAIN}_{MOCK_SERIAL}_connection_mode"
    # And nothing is left keyed on the IP.
    assert dev_reg.async_get_device(identifiers={(DOMAIN, MOCK_HOST)}) is None


async def test_migration_removes_an_orphan_that_is_already_duplicated(
    hass: HomeAssistant, mock_coordinator_session
) -> None:
    """Where the serial-keyed entry already exists, the IP-keyed one is the
    dead duplicate the race left behind -- it has been unavailable since the
    restart that created it and nothing will ever update it, so it goes
    rather than being rewritten onto a key that is taken."""
    entry = _legacy_entry(hass, f"{DOMAIN}_{MOCK_SERIAL}")
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    real_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_SERIAL)},
    )
    real_entity = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MOCK_SERIAL}_connection_mode",
        config_entry=entry,
        device_id=real_device.id,
    )
    orphan_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_HOST)},
    )
    orphan_entity = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MOCK_HOST}_connection_mode",
        config_entry=entry,
        device_id=orphan_device.id,
    )
    assert orphan_entity.entity_id != real_entity.entity_id

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert ent_reg.async_get(orphan_entity.entity_id) is None
    assert dev_reg.async_get(orphan_device.id) is None
    # The working pair is untouched.
    assert ent_reg.async_get(real_entity.entity_id) is not None
    assert dev_reg.async_get(real_device.id) is not None


async def test_migration_leaves_a_host_identity_device_alone(hass: HomeAssistant) -> None:
    """A board whose serial resolves *to* the host was never keyed on a
    placeholder -- its host-keyed device is the real one, and re-keying or
    removing it would orphan a working device to fix a problem it doesn't
    have."""
    from custom_components.localthings import async_migrate_entry

    entry = _legacy_entry(hass, f"{DOMAIN}_{MOCK_HOST}")
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MOCK_HOST)},
    )

    assert await async_migrate_entry(hass, entry) is True

    assert entry.data[CONF_SERIAL] == MOCK_HOST
    unchanged = dev_reg.async_get(device.id)
    assert unchanged is not None
    assert unchanged.identifiers == {(DOMAIN, MOCK_HOST)}


async def test_migration_rejects_a_future_entry_version(hass: HomeAssistant) -> None:
    """A downgrade must fail the entry rather than silently mangling data
    written by a newer release."""
    from custom_components.localthings import async_migrate_entry

    entry = MockConfigEntry(domain=DOMAIN, data=LEGACY_ENTRY_DATA, version=6)
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is False


async def test_migration_without_a_unique_id_falls_back_to_host(hass: HomeAssistant) -> None:
    """Nothing to recover the identity from means the host, which is exactly
    what the coordinator used to seed -- so the registry keys such an entry
    already holds stay valid."""
    from custom_components.localthings import async_migrate_entry

    entry = MockConfigEntry(domain=DOMAIN, data=LEGACY_ENTRY_DATA, version=1)
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.data[CONF_SERIAL] == entry.data[CONF_HOST]
    assert entry.unique_id == f"{DOMAIN}_{MOCK_HOST}"


async def test_migration_drops_the_superseded_dry_level_sensor(hass: HomeAssistant) -> None:
    """dryer.py's dry_level moved from a sensor to a select; unique_ids are
    scoped by (integration, platform), so the old sensor row can only be
    dropped, not carried across -- same tail-matching contract as the
    particulate-statistics relabel: a plain match, a subdevice-instanced
    match, a select-domain row with the same tail (untouched, wrong
    domain), and an unrelated sensor (untouched, wrong tail)."""
    from custom_components.localthings import async_migrate_entry

    entry = MockConfigEntry(domain=DOMAIN, data=LEGACY_ENTRY_DATA, version=4)
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)

    orphan = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{MOCK_SERIAL}_dry_level", config_entry=entry
    )
    orphan_instanced = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MOCK_SERIAL}_subdevice_uuid_dry_level_2",
        config_entry=entry,
    )
    survivor_select = ent_reg.async_get_or_create(
        "select", DOMAIN, f"{DOMAIN}_{MOCK_SERIAL}_dry_level", config_entry=entry
    )
    unrelated_sensor = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{MOCK_SERIAL}_dry_time", config_entry=entry
    )

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 5
    assert ent_reg.async_get(orphan.entity_id) is None
    assert ent_reg.async_get(orphan_instanced.entity_id) is None
    assert ent_reg.async_get(survivor_select.entity_id) is not None
    assert ent_reg.async_get(unrelated_sensor.entity_id) is not None
