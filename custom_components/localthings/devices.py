"""Device-registry lookups and parent links, across the HA releases this
integration supports.

HA 2026.9 deprecated `DeviceInfo`'s `via_device` for `via_device_id` and
`device_registry.async_get_device` for `async_get_device_by_identifier`, both
breaking in 2027.8; neither replacement exists on hacs.json's 2025.1 floor.
The lookups here are written to need no replacement at all, and the one call
that does feature-detects at runtime -- same pattern as `__init__.py`'s
PARTICULATE_UNIT and `new_unit_class`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

_HAS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


def device_info_fields(info: DeviceInfo) -> dict[str, Any]:
    """Read only set fields from either DeviceInfo representation."""
    if as_dict := getattr(info, "as_dict", None):
        return as_dict()
    return cast("dict[str, Any]", info)


def find_entry_device(
    hass: HomeAssistant, entry_id: str, identifiers: Iterable[tuple[str, str]]
) -> dr.DeviceEntry | None:
    """The `entry_id` device row carrying any of `identifiers`.

    Scans the entry's own rows rather than calling `async_get_device`, which
    searches every entry and is deprecated for exactly that reason -- an
    identifier is only unique within one config entry. Every caller here means
    its own entry's row, so this is both the compatible spelling and the
    correct one; entries hold a handful of rows, so the scan is free.
    """
    wanted = set(identifiers)
    return next(
        (
            row
            for row in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)
            if row.identifiers & wanted
        ),
        None,
    )


def via_device_fields(
    hass: HomeAssistant, entry_id: str, parent: tuple[str, str]
) -> dict[str, Any]:
    """Return the parent link to include in a DeviceInfo constructor."""
    if not _HAS_VIA_DEVICE_ID:
        return {"via_device": parent}
    # Core drops the entity if via_device_id names an unregistered device.
    if (device := find_entry_device(hass, entry_id, (parent,))) is not None:
        return {"via_device_id": device.id}
    return {}
