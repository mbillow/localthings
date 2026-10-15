"""Present declared oven cavities together without changing their protocol routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .devices import find_entry_device
from .registry.adapter import _key
from .registry.capabilities import cook
from .registry.entities import PLATFORM_OF
from .registry.subdevices import MAIN, Subdevice, canonical_view

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import LocalThingsCoordinator
    from .registry.discovery import BoundEntity


SHARED_KEYS = frozenset(
    {
        "door_open",
        "divider",
        "reservoir_open",
        "cloud_connected",
        "child_lock",
        "energy_saving",
        "power_switch",
        "remote_control",
    }
)

PROBE_KEYS = frozenset({"food_probe_temp", "food_probe_setpoint"})

# Cooking state and fault reports remain attached to their own cavity.
CAVITY_KEYS = PROBE_KEYS | frozenset(
    {
        "start_cooking",
        "stop",
        "oven_mode",
        "oven_setpoint",
        "cook_time_hours",
        "cook_time_minutes",
        "cook_time",
        "machine_state",
        "cycle_active",
        "progress_percentage",
        "operation_time_minutes",
        "finish_time",
        "oven_state",
        "current_temp_c",
        "alarm_code",
        "diagnosis_status",
    }
)


# These resources may be independent on another oven, even when their readings match.
OPTION_KEYS = frozenset(
    {
        "door_open",
        "reservoir_open",
        "child_lock",
        "energy_saving",
        "power_switch",
        "remote_control",
        "lamp",
        "sound",
        "fast_preheat",
        "natural_steam",
        "sync_clock",
        "diagnosis_start",
    }
)


def combined_lower(
    resources: dict[str, dict], subdevices: list[Subdevice], device_type: str | None
) -> Subdevice | None:
    """Use the existing cavity declarations, including shared modeSpec tags (#597)."""
    if device_type not in {"oven", "range"} or len(subdevices) != 1:
        return None
    lower = subdevices[0]
    main_view = canonical_view(MAIN, resources, subdevices)
    lower_view = canonical_view(lower, resources, subdevices)
    if (
        cook.cavity(main_view) != "Lower"
        and cook.cavity(lower_view) == "Lower"
        and all(
            cook.CAVITY_HREF in view and cook.MODE_HREF in view for view in (main_view, lower_view)
        )
    ):
        return lower
    return None


def shared_keys(resources: dict[str, dict], lower: Subdevice | None) -> frozenset[str]:
    """Only collapse copies whose physical sharing is confirmed on NV51K7770S."""
    if lower is None or lower.kind != "indexed" or lower.key != "1":
        return frozenset()
    prefix = "x.com.samsung.da."
    infos = [resources.get(sub.to_actual("/information/vs/0"), {}) for sub in (MAIN, lower)]
    if (
        all(
            str(info.get(prefix + "modelNum", "")).split("|", 1)[0] == "LCD_OV_WALL_16K"
            for info in infos
        )
        and infos[1].get(prefix + "description") == "LCD_OV_WALL_16K_DIV"
    ):
        return SHARED_KEYS
    return frozenset()


def is_shared_duplicate(
    bound: BoundEntity, lower: Subdevice | None, resources: dict[str, dict]
) -> bool:
    """Retain the original source entities, removing only their shared copies."""
    shared = shared_keys(resources, lower)
    if not shared:
        return False
    return (bound.subdevice == MAIN and bound.desc.key == "divider") or (
        bound.subdevice == lower and bound.desc.key in shared - {"divider"}
    )


def divider_state(coordinator: LocalThingsCoordinator) -> str | None:
    lower = coordinator.combined_oven_lower
    if lower is None:
        return None
    return coordinator.resource(lower.to_actual(cook.CONNECTED_HREF)).get(
        "x.com.samsung.da.connected"
    )


def cavity_label(coordinator: LocalThingsCoordinator, subdevice: Subdevice) -> str:
    """A missing divider reading must not assert whole-oven operation."""
    if subdevice == coordinator.combined_oven_lower:
        return "lower"
    return "whole" if divider_state(coordinator) == "Off" else "upper"


@callback
def async_combine_oven_devices(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: LocalThingsCoordinator
) -> None:
    """Move entity rows before removing their old device, preserving their IDs."""
    lower = coordinator.combined_oven_lower
    if lower is None:
        return
    devices, entities = dr.async_get(hass), er.async_get(hass)
    primary = devices.async_get_or_create(config_entry_id=entry.entry_id, **coordinator.device_info)
    previous = find_entry_device(
        hass, entry.entry_id, {(DOMAIN, f"{coordinator.device_key}_{lower.key}")}
    )
    duplicates = {
        (PLATFORM_OF[type(bound.desc)], f"{DOMAIN}_{coordinator.device_key}_{_key(bound)}")
        for bound in coordinator.bound
        if is_shared_duplicate(bound, lower, coordinator.discovery_resources)
    }
    for row in er.async_entries_for_config_entry(entities, entry.entry_id):
        if row.platform != DOMAIN:
            continue
        if (row.domain, row.unique_id) in duplicates:
            entities.async_remove(row.entity_id)
        elif previous is not None and row.device_id == previous.id:
            entities.async_update_entity(row.entity_id, device_id=primary.id)
    # HA retains deleted registry identities for a downgrade. Never remove
    # a device still hosting another integration's entities or ownership.
    if (
        previous is not None
        and previous.id != primary.id
        and previous.config_entries == {entry.entry_id}
        and not er.async_entries_for_device(entities, previous.id, include_disabled_entities=True)
    ):
        devices.async_remove_device(previous.id)
