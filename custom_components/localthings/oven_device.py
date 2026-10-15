"""Present the confirmed Flex Duo cavity pair as one physical oven."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .devices import find_entry_device
from .registry.adapter import _key
from .registry.entities import PLATFORM_OF
from .registry.subdevices import MAIN, Subdevice

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

# Cooking state and fault reports remain attached to their own cavity.
CAVITY_KEYS = frozenset(
    {
        "start_cooking",
        "stop",
        "oven_mode",
        "oven_setpoint",
        "cook_time_hours",
        "cook_time_minutes",
        "machine_state",
        "cycle_active",
        "progress_percentage",
        "operation_time_minutes",
        "finish_time",
        "oven_state",
        "current_temp_c",
        "alarm_code",
        "diagnosis_status",
        "food_probe_temp",
        "food_probe_setpoint",
    }
)


def combined_lower(resources: dict[str, dict], subdevices: list[Subdevice]) -> Subdevice | None:
    """Only combine the measured removable-divider pair, not two separate ovens."""
    prefix = "x.com.samsung.da."
    if (
        str(resources.get("/information/vs/0", {}).get(prefix + "modelNum", "")).split("|", 1)[0]
        != "LCD_OV_WALL_16K"
    ):
        return None
    return next(
        (
            sub
            for sub in subdevices
            if sub.kind == "indexed"
            and sub.key == "1"
            and str(
                resources.get(sub.to_actual("/information/vs/0"), {}).get(prefix + "modelNum", "")
            ).split("|", 1)[0]
            == "LCD_OV_WALL_16K"
            and resources.get(sub.to_actual("/information/vs/0"), {}).get(prefix + "description")
            == "LCD_OV_WALL_16K_DIV"
        ),
        None,
    )


def is_shared_duplicate(bound: BoundEntity, lower: Subdevice | None) -> bool:
    """Retain the original source entities, removing only their shared copies."""
    if lower is None:
        return False
    return (bound.subdevice == MAIN and bound.desc.key == "divider") or (
        bound.subdevice == lower and bound.desc.key in SHARED_KEYS - {"divider"}
    )


def cavity_label(coordinator: LocalThingsCoordinator, subdevice: Subdevice) -> str:
    """A missing divider reading must not assert whole-oven operation."""
    if subdevice == coordinator.combined_oven_lower:
        return "lower"
    connected = coordinator.resource("/connected/vs/1").get("x.com.samsung.da.connected")
    return "whole" if connected == "Off" else "upper"


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
        if is_shared_duplicate(bound, lower)
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
