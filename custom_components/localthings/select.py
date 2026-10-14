"""Select platform for Local Things."""

from __future__ import annotations

import re
from typing import cast

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .catalog import translated_states
from .catalog import translation_state as _translation_state
from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included
from .registry.entities import SelectDesc


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LocalThingsSelect(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, SelectDesc) and _is_included(b, coordinator)
    )


_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _display(value, translation_key: str | None, fallback_fn=None):
    """Turn a raw device option/state value into what's shown in the UI.

    `translation_key` is the entity's already-resolved key (it can itself
    be a callable -- see entities.py -- so callers pass the resolved
    value, not the raw descriptor field).

    An entity with a translation_key looks its state up in the shipped
    translation catalog, whose state keys are lowercase, and the device
    still expects that same raw casing back on write (mapped back via
    _raw_options()). Everything else has no catalog lookup, so there's no
    reason to destroy the device's own casing: only two cosmetic fixups
    apply, title-casing a fully lowercase token ("voice") and spacing a
    PascalCase one ("ExtraHigh" -> "Extra High"); an already-friendly value
    ("AI Wash") matches neither and passes through unchanged.
    """
    if not isinstance(value, str):
        return value
    known = translated_states("select", translation_key) if translation_key else frozenset()
    if translated := _translation_state(value, known):
        return translated
    if fallback_fn is not None and (fallback := fallback_fn(value)) is not None:
        return fallback
    if translation_key and not known:
        # No state table for this key: either the entity isn't translated at
        # all, or its name is translated but its options deliberately aren't
        # (an unrecognized course table, say). Nothing named this value, so
        # the raw device value is the best choice -- the cosmetic reshaping
        # below would only mangle an opaque code, turning a course '0E' into
        # '0 E'. Reached only when the fallback *declined* the value, not
        # merely when none was supplied: cycle_select always supplies one now
        # (it labels cloud programs) and returns None for everything else.
        return value
    if value.islower():
        return value.replace("_", " ").title()
    return _CAMEL_BOUNDARY_RE.sub(" ", value)


class LocalThingsSelect(LocalThingsEntity, SelectEntity):
    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        desc = cast(SelectDesc, bound.desc)
        if not desc.options_field and not callable(desc.options):
            self._attr_options = [self._display_option(o) for o in desc.options]

    def _display_option(self, value):
        """Normalize both current state and options through one path."""
        display_fn = cast(SelectDesc, self._bound.desc).display_fn
        fallback_fn = (
            (lambda raw: display_fn(raw, self._resources)) if display_fn is not None else None
        )
        return _display(value, self.translation_key, fallback_fn)

    def _raw_options(self) -> list[str]:
        desc = cast(SelectDesc, self._bound.desc)
        if callable(desc.options):
            # Per-device option list computed from the full resource
            # snapshot -- e.g. a course list decoded from a sibling
            # resource. No static fallback: when unpopulated, the callable
            # returns [] and exists_fn suppresses the entity entirely. Uses
            # this subdevice's canonical view (issue #177), not the raw
            # snapshot -- see LocalThingsEntity._resources.
            return list(desc.options(self._resources) or [])
        if desc.options_field:
            rep = self.coordinator.last_resources.get(self._bound.href) or {}
            return list(rep.get(desc.options_field) or [])
        return list(desc.options)

    @property
    def options(self) -> list[str]:
        desc = cast(SelectDesc, self._bound.desc)
        if desc.options_field or callable(desc.options):
            return [self._display_option(o) for o in self._raw_options()]
        return self._attr_options

    @property
    def current_option(self):
        raw = (self.coordinator.data or {}).get(self._state_key)
        return self._display_option(raw)

    async def async_select_option(self, option: str) -> None:
        raw = next(
            (o for o in self._raw_options() if self._display_option(o) == option),
            option,
        )
        await self.coordinator.async_send_command(self._bound, raw)
