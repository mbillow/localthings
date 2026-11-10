"""Remove entity registry rows that the current code no longer creates.

Runs on every setup, before the platforms register, rather than as a numbered
migration: every rule is idempotent and judged from the current code, so a
user who skips releases gets the same result as one who installed each, and a
downgrade costs only the removed rows instead of failing the whole entry
(`entry.version > 4` in async_migrate_entry).

A row is removed only for a code change (a platform move, a superseded or
retired key) or a model fact its descriptor vouches for (`prune_when_excluded`,
`replaces_siblings`). A `dynamic_presence` entity is never removed for being
absent. A `prune_when_excluded` one is removed whenever its exists_fn excludes
it, including on a confirmed-empty {} rep, as the switches it replaced were.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from collections.abc import Iterator
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.start import async_at_started

from .const import DOMAIN, PLATFORMS
from .registry.adapter import _key
from .registry.batch import is_stub_rep
from .registry.by_type import KEY_PLATFORMS, WASHER_ONLY_ENTITIES, registry_for, resolve
from .registry.discovery import BoundEntity
from .registry.entities import PLATFORM_OF
from .registry.subdevices import MAIN

if TYPE_CHECKING:
    from .coordinator import LocalThingsCoordinator

_LOGGER = logging.getLogger(__name__)

# (platform, key) pairs no registry binds on that platform any more. Matched
# on the unique_id's tail, which can carry a subdevice prefix and a `_<n>`
# instance; tests/test_entity_cleanup.py checks no descriptor could collide.
RETIRED_KEYS: tuple[tuple[str, str], ...] = (
    # The dryer's dryLevel and dryTime became selects (#439).
    ("sensor", "dry_level"),
    ("sensor", "dry_time"),
)

REMOVED_ISSUE = "removed_entities"
# entry_id -> removed entity_ids not yet checked for automation use. Outside
# the entry, so a reload before Home Assistant has started doesn't lose them:
# its own pass finds nothing left to remove.
_PENDING = f"{DOMAIN}_removed_pending"


def _retired_patterns() -> dict[str, re.Pattern[str]]:
    keys: dict[str, list[str]] = defaultdict(list)
    for platform, key in RETIRED_KEYS:
        keys[platform].append(re.escape(key))
    return {
        platform: re.compile(rf"_(?:{'|'.join(names)})(?:_\d+)?$")
        for platform, names in keys.items()
    }


_RETIRED = _retired_patterns()


def _platform(bound: BoundEntity) -> str:
    return PLATFORM_OF[type(bound.desc)]


def _populated(rep: dict | None) -> bool:
    return bool(rep) and not is_stub_rep(rep)


def _decides(bound: BoundEntity, rep: dict | None) -> bool:
    """Whether this rep is complete enough for the descriptor's choice to stand."""
    return rep is not None and _populated(rep) and (not bound.desc.field or bound.desc.field in rep)


def _bound_removals(coordinator: LocalThingsCoordinator) -> Iterator[tuple[str, str, str]]:
    """(platform, unique_id, reason) for rows the bound descriptors rule out."""
    # entity.py imports this module's caller, so this one can't import it at the top.
    from .entity import _is_included

    prefix = f"{DOMAIN}_{coordinator.device_key}_"
    by_uid: dict[str, list[BoundEntity]] = defaultdict(list)
    for bound in coordinator.bound:
        by_uid[prefix + _key(bound)].append(bound)
    resources = coordinator.discovery_resources

    for uid, siblings in by_uid.items():
        platforms = {_platform(b) for b in siblings}
        # A platform no registry binds this key on any more. Judged from the
        # code alone, so a resource missing from this discovery can't count
        # as a move; a key only expand_fn produces has no static entry.
        known = set(platforms)
        for bound in siblings:
            if bound.desc.key not in KEY_PLATFORMS:
                known = set(PLATFORMS)
                break
            known |= KEY_PLATFORMS[bound.desc.key]
            # A prefixed binding shares its unique_id with any descriptor
            # keyed on the whole prefixed key.
            known |= KEY_PLATFORMS.get(bound.key_override or "", frozenset())
        for platform in PLATFORMS:
            if platform not in known:
                yield platform, uid, "no longer provided on this platform"

        included = [b for b in siblings if _is_included(b, coordinator)]
        kept = {_platform(b) for b in included}
        # Opt-in and one-way: only a descriptor whose inclusion is a model
        # fact replaces its siblings, and never while presence can still
        # change or a rep is a stub, empty, or missing the winner's field.
        winners = [b for b in included if b.desc.replaces_siblings]
        if (
            winners
            and not any(b.desc.dynamic_presence for b in siblings)
            and all(_decides(b, resources.get(b.href)) for b in winners)
            and all(_populated(resources.get(b.href)) for b in siblings)
        ):
            replaced_by = ", ".join(sorted({_platform(b) for b in winners}))
            for platform in platforms - kept:
                yield platform, uid, f"replaced by its {replaced_by} entity"

        for bound in siblings:
            if bound.desc.prune_when_excluded and _platform(bound) not in kept:
                yield _platform(bound), uid, "not reported by this device"

    for bound in coordinator.bound:
        if not bound.desc.supersedes or not _is_included(bound, coordinator):
            continue
        for platform, key in bound.desc.supersedes:
            uid = f"{prefix}{bound.subdevice.key_prefix}{key}{bound.instance}"
            still_bound = {_platform(b) for b in by_uid.get(uid, ())}
            if platform not in still_bound:
                yield platform, uid, f"replaced by {_key(bound)}"


def _washer_only_keys(coordinator: LocalThingsCoordinator) -> set[tuple[str, str]]:
    """Washer-only rows earlier routing left on filters, ovens and microwaves (#475, #681)."""
    main_registry = registry_for(coordinator.device_type_name)
    obsolete = set()
    for subdevice in (MAIN, *coordinator.subdevices):
        registry = main_registry
        if subdevice != MAIN:
            registry = resolve(coordinator.discovery_canonical(subdevice)) or main_registry
        obsolete.update(
            (platform, f"{subdevice.key_prefix}{key}")
            for platform, key in WASHER_ONLY_ENTITIES.get(registry.name, ())
        )
    return obsolete


def _stale_rows(
    ent_reg: er.EntityRegistry, entry: ConfigEntry, coordinator: LocalThingsCoordinator
) -> dict[str, str]:
    """entity_id -> reason for every row this entry should drop."""
    stale: dict[str, str] = {}
    rows = [
        row
        for row in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        if row.platform == DOMAIN
    ]
    by_id = {(row.domain, row.unique_id): row for row in rows}

    for platform, uid, reason in _bound_removals(coordinator):
        if (row := by_id.get((platform, uid))) is not None:
            stale.setdefault(row.entity_id, reason)

    prefix = f"{DOMAIN}_{coordinator.device_key}_"
    washer_only = _washer_only_keys(coordinator)
    for row in rows:
        pattern = _RETIRED.get(row.domain)
        if pattern is not None and pattern.search(row.unique_id):
            stale.setdefault(row.entity_id, "no longer provided on this platform")
        elif (
            row.unique_id.startswith(prefix)
            and (row.domain, row.unique_id.removeprefix(prefix)) in washer_only
        ):
            stale.setdefault(row.entity_id, "left by an earlier washer misidentification")
    return stale


@callback
def async_reconcile_entities(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: LocalThingsCoordinator
) -> list[str]:
    """Remove this entry's stale registry rows and return their entity_ids."""
    ent_reg = er.async_get(hass)
    stale = _stale_rows(ent_reg, entry, coordinator)
    for entity_id, reason in stale.items():
        # INFO: irreversible, and it takes the user's rename and area with it.
        _LOGGER.info(
            "Removing %s: %s; update any automation or dashboard that referenced it",
            entity_id,
            reason,
        )
        ent_reg.async_remove(entity_id)
    return list(stale)


def _usage(hass: HomeAssistant, entity_id: str) -> list[str]:
    from homeassistant.components.automation import automations_with_entity
    from homeassistant.components.script import scripts_with_entity

    ent_reg = er.async_get(hass)
    links = []
    for kind, users in (
        ("automation", automations_with_entity(hass, entity_id)),
        ("script", scripts_with_entity(hass, entity_id)),
    ):
        for user in users:
            state = hass.states.get(user)
            name = state.name if state is not None else user
            row = ent_reg.async_get(user)
            links.append(
                f"[{name}](/config/{kind}/edit/{row.unique_id})" if row is not None else name
            )
    return links


@callback
def async_notify_removed(hass: HomeAssistant, entry: ConfigEntry, removed: list[str]) -> None:
    """Raise a Repair listing the automations and scripts that used a removed entity.

    Checked once Home Assistant has started, when automations and scripts
    have loaded. Persistent, because the removal won't recur to recreate it;
    keyed on the removed set, so a later removal is a new issue rather than
    one the user already dismissed.
    """
    pending: list[str] = hass.data.setdefault(_PENDING, {}).setdefault(entry.entry_id, [])
    pending.extend(entity_id for entity_id in removed if entity_id not in pending)
    if not pending:
        return

    @callback
    def _check(hass: HomeAssistant) -> None:
        removed = hass.data[_PENDING].pop(entry.entry_id, [])
        lines = [
            f"- `{entity_id}`: {', '.join(users)}"
            for entity_id in removed
            if (users := _usage(hass, entity_id))
        ]
        if not lines:
            return
        digest = hashlib.sha1("\n".join(sorted(removed)).encode()).hexdigest()[:12]
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{REMOVED_ISSUE}_{entry.entry_id}_{digest}",
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=REMOVED_ISSUE,
            translation_placeholders={"device_name": entry.title, "entities": "\n".join(lines)},
        )

    entry.async_on_unload(async_at_started(hass, _check))


@callback
def async_delete_removed_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop this entry's removal Repairs when the entry itself is deleted."""
    hass.data.get(_PENDING, {}).pop(entry.entry_id, None)
    prefix = f"{REMOVED_ISSUE}_{entry.entry_id}_"
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(prefix):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
