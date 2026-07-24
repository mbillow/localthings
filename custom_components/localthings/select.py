"""Select platform for Local Things."""
from __future__ import annotations

import re
from typing import Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .registry.entities import SelectDesc

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included


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


_CAMEL_BOUNDARY_RE = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')


def _display(value, translation_key: Optional[str]):
    """Turn a raw device option/state value into what's shown in the UI.

    `translation_key` is the entity's already-resolved key (SelectDesc.
    translation_key can itself be a callable -- see entities.py -- so
    callers pass the resolved value, e.g. self._attr_translation_key, not
    the raw descriptor field).

    An entity with a translation_key looks its state up in strings.json,
    and hassfest requires those keys to be lowercase -- so those values
    must be lowercased exactly to match, and the device still expects
    that same raw casing back on write (callers map the displayed value
    back to raw via _raw_options()).

    Everything else has no strings.json lookup, so there's no reason to
    destroy the device's own casing. Only two cosmetic fixups apply: a
    fully lowercase device-native token (e.g. "voice") is title-cased,
    and a PascalCase token (e.g. "ExtraHigh") gets a space inserted at
    the case boundary ("Extra High"). A value that's already
    human-friendly (e.g. "AI Wash") matches neither pattern and passes
    through unchanged.
    """
    if not isinstance(value, str):
        return value
    if translation_key:
        return value.lower()
    if value.islower():
        return value.replace('_', ' ').title()
    return _CAMEL_BOUNDARY_RE.sub(' ', value)


class LocalThingsSelect(LocalThingsEntity, SelectEntity):

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        desc: SelectDesc = bound.desc
        if not desc.options_field and not callable(desc.options):
            self._attr_options = [_display(o, self._attr_translation_key) for o in desc.options]

    def _raw_options(self) -> list[str]:
        desc: SelectDesc = self._bound.desc
        if callable(desc.options):
            # Per-device option list computed from the full resource
            # snapshot (not just this entity's own href) -- e.g. a course
            # list decoded from a sibling resource. There is no static
            # fallback: when that resource isn't populated the callable
            # returns [] and the entity's exists_fn suppresses it entirely.
            return list(desc.options(self.coordinator.last_resources) or [])
        if desc.options_field:
            rep = self.coordinator.last_resources.get(self._bound.href) or {}
            return list(rep.get(desc.options_field) or [])
        return list(desc.options)

    @property
    def options(self) -> list[str]:
        desc: SelectDesc = self._bound.desc
        if desc.options_field or callable(desc.options):
            return [_display(o, self._attr_translation_key) for o in self._raw_options()]
        return self._attr_options

    @property
    def current_option(self):
        raw = (self.coordinator.data or {}).get(self._state_key)
        return _display(raw, self._attr_translation_key)

    async def async_select_option(self, option: str) -> None:
        raw = next(
            (o for o in self._raw_options() if _display(o, self._attr_translation_key) == option),
            option,
        )
        await self.coordinator.async_send_command(self._bound, raw)
