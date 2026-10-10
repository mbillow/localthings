"""Reversible opt-in reduction of duplicate cooktop rows."""

import re

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .registry.adapter import _key

COMPACT = "compact_cooktop"
_MANAGED = "compact_cooktop_disabled"
_DETAIL = re.compile(r"burner_\d+_(?:power_level|hot_surface|pan_size)$")


def supported(coordinator):
    return coordinator is not None and any(
        b.desc.key.startswith("burner_")
        and b.desc.key.endswith("_state")
        and b.desc.extra_state_attributes_fn is not None
        for b in getattr(coordinator, "bound", ())
    )


@callback
def apply(hass, entry, coordinator, compact):
    """Only restore rows disabled by this option; preserve user overrides."""
    options = dict(entry.options)
    if compact == options.get(COMPACT, False):
        return options
    registry = er.async_get(hass)
    managed = set(options.get(_MANAGED, []))
    details = {
        f"{DOMAIN}_{coordinator.device_key}_{_key(b)}"
        for b in coordinator.bound
        if _DETAIL.fullmatch(b.desc.key) or b.desc.key in {"main_timer_current", "main_timer_set"}
    }
    for row in er.async_entries_for_config_entry(registry, entry.entry_id):
        if compact and row.unique_id in details and row.disabled_by is None:
            registry.async_update_entity(
                row.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
            managed.add(row.unique_id)
        elif (
            not compact
            and row.unique_id in managed
            and row.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        ):
            registry.async_update_entity(row.entity_id, disabled_by=None)
    options[COMPACT] = compact
    options[_MANAGED] = sorted(managed) if compact else []
    return options
