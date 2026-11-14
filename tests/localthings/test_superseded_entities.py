"""cleanup.RETIRED_KEYS: sensor rows a platform move has superseded.

The dryer's dryLevel and dryTime became selects in #439. unique_ids are
scoped by (integration, platform), so that move cannot rewrite the old rows
-- they can only be dropped, and until they are, every upgraded dryer keeps
a permanently-unavailable `sensor.<name>_dry_level` carrying the user's
rename, area and any automation reference.

Deliberately not a numbered migration (reviewed on PR #407): the sweep is
idempotent, so a version gate would buy only running it once, at the cost of
a bump that fails the *whole entry* to load on a downgrade rather than
losing two entities.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.cleanup import async_reconcile_entities
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator

from .conftest import LEGACY_ENTRY_DATA, MOCK_SERIAL


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=LEGACY_ENTRY_DATA, version=4)
    entry.add_to_hass(hass)
    return entry


def _coordinator() -> LocalThingsCoordinator:
    """Retired keys need no discovery: nothing binds them on these platforms."""
    return cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            bound=[],
            discovery_resources={},
            device_key=MOCK_SERIAL,
            device_type_name="dryer",
            subdevices=[],
        ),
    )


def _row(hass: HomeAssistant, entry: MockConfigEntry, domain: str, unique_suffix: str):
    return er.async_get(hass).async_get_or_create(
        domain,
        DOMAIN,
        f"{DOMAIN}_{MOCK_SERIAL}_{unique_suffix}",
        config_entry=entry,
    )


async def test_drops_both_superseded_sensors(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    level = _row(hass, entry, "sensor", "dry_level")
    time = _row(hass, entry, "sensor", "dry_time")

    async_reconcile_entities(hass, entry, _coordinator())

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get(level.entity_id) is None
    assert ent_reg.async_get(time.entity_id) is None


async def test_matches_a_subdevice_instanced_key(hass: HomeAssistant) -> None:
    """Same tail-matching contract as the particulate-statistics relabel: a
    key can carry a subdevice prefix and a trailing `_<n>` instance, and
    matching the tail is what keeps this working for a renamed entity, whose
    entity_id no longer follows from its key."""
    entry = _entry(hass)
    instanced = _row(hass, entry, "sensor", "subdevice_uuid_dry_level_2")

    async_reconcile_entities(hass, entry, _coordinator())

    assert er.async_get(hass).async_get(instanced.entity_id) is None


async def test_leaves_the_replacement_and_unrelated_rows_alone(hass: HomeAssistant) -> None:
    """The domain check is what makes the tail safe: the select that
    supersedes these carries the identical key."""
    entry = _entry(hass)
    replacement = _row(hass, entry, "select", "dry_level")
    unrelated = _row(hass, entry, "sensor", "dryer_type")
    # A key merely *ending* in something similar must not be swept.
    not_a_tail = _row(hass, entry, "sensor", "dry_level_remaining")

    async_reconcile_entities(hass, entry, _coordinator())

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get(replacement.entity_id) is not None
    assert ent_reg.async_get(unrelated.entity_id) is not None
    assert ent_reg.async_get(not_a_tail.entity_id) is not None


async def test_leaves_another_entrys_rows_alone(hass: HomeAssistant) -> None:
    """Scoped to the entry being set up, not the whole registry -- two
    dryers are two entries, and each sweeps its own on its own setup."""
    entry = _entry(hass)
    other = MockConfigEntry(domain=DOMAIN, data=LEGACY_ENTRY_DATA, version=4)
    other.add_to_hass(hass)
    theirs = er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_OTHER-SERIAL_dry_level", config_entry=other
    )

    async_reconcile_entities(hass, entry, _coordinator())

    assert er.async_get(hass).async_get(theirs.entity_id) is not None


async def test_is_idempotent(hass: HomeAssistant) -> None:
    """The property the whole no-version-bump argument rests on: running it
    on every setup has to be free once there is nothing left to drop."""
    entry = _entry(hass)
    _row(hass, entry, "sensor", "dry_level")
    keep = _row(hass, entry, "select", "dry_level")

    async_reconcile_entities(hass, entry, _coordinator())
    before = set(er.async_get(hass).entities)
    async_reconcile_entities(hass, entry, _coordinator())

    assert set(er.async_get(hass).entities) == before
    assert er.async_get(hass).async_get(keep.entity_id) is not None


async def test_logs_the_removal_without_guessing_the_replacement_id(
    hass: HomeAssistant, caplog
) -> None:
    """INFO, not debug: the removal is irreversible and takes the user's
    rename and area with it. It names the row it removed but NOT a
    `select.<object_id>` derived from it -- a renamed sensor's object id is
    not the one the new select gets, so that would be a guess."""
    entry = _entry(hass)
    renamed = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MOCK_SERIAL}_dry_level",
        config_entry=entry,
        suggested_object_id="basement_dryer_dryness",
    )
    assert renamed.entity_id == "sensor.basement_dryer_dryness"

    with caplog.at_level(logging.INFO, logger="custom_components.localthings"):
        async_reconcile_entities(hass, entry, _coordinator())

    assert "sensor.basement_dryer_dryness" in caplog.text
    assert "select.basement_dryer_dryness" not in caplog.text


async def test_runs_on_setup(hass: HomeAssistant, fridge_resources) -> None:
    """Wired into async_setup_entry, and before the platforms register, so
    an upgraded dryer never has the dead row and its replacement alive at
    the same time."""
    # Reusing the identity suite's harness rather than rebuilding it: this
    # is the only test here that needs a device to answer a poll, and that
    # setup (a UUID-keyed v4 entry plus a patched session) is exactly what
    # it already builds. Imported locally to keep that coupling visible.
    from .test_identity_migration import UUID_A, _reachable
    from .test_identity_migration import _entry as _identity_entry

    entry = _identity_entry(hass, version=4, key=UUID_A, device_key=UUID_A)
    orphan = er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{UUID_A}_dry_level", config_entry=entry
    )

    with _reachable(fridge_resources, UUID_A):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert er.async_get(hass).async_get(orphan.entity_id) is None
