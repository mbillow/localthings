"""Select platform for Local Things."""
from __future__ import annotations

import re
from typing import Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .registry.entities import SelectDesc

from .catalog import translated_states
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


def _translation_state(value: str, known: frozenset[str]) -> str | None:
    """Return the catalog state `value` normalizes to, else None.

    Samsung reports options in whatever casing the resource uses
    ('Rinse_Hold', 'SpTtypeBeerDrinks', '1b'); Home Assistant looks state
    translations up by a lowercase key. Only values the catalog actually
    knows are normalized -- an unrecognized (or future) vendor value keeps
    its own readable form rather than becoming an untranslatable slug.
    """
    direct = value.lower().replace(' ', '_')
    if direct in known:
        return direct
    snake = _CAMEL_BOUNDARY_RE.sub('_', value).lower().replace(' ', '_')
    return snake if snake in known else None


def _display(value, translation_key: Optional[str], fallback_fn=None):
    """Turn a raw device option/state value into what's shown in the UI.

    `translation_key` is the entity's already-resolved key (SelectDesc.
    translation_key can itself be a callable -- see entities.py -- so
    callers pass the resolved value, e.g. self.translation_key, not
    the raw descriptor field).

    An entity with a translation_key looks its state up in the shipped
    translation catalog, whose state keys are lowercase -- so those values
    must be lowercased exactly to match, and the device still expects
    that same raw casing back on write (callers map the displayed value
    back to raw via _raw_options()).

    Everything else has no catalog lookup, so there's no reason to
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
        known = translated_states('select', translation_key)
        if not known:
            # No state table for this key: either the entity isn't translated
            # at all, or its name is translated but its options deliberately
            # aren't (an unrecognized course table, say). Give an explicit
            # device-specific fallback the opportunity to make an opaque value
            # readable; otherwise the raw device value remains the best choice.
            if fallback_fn is None:
                return value
        elif translated := _translation_state(value, known):
            return translated
    if fallback_fn is not None:
        fallback = fallback_fn(value)
        if fallback is not None:
            return fallback
    if value.islower():
        return value.replace('_', ' ').title()
    return _CAMEL_BOUNDARY_RE.sub(' ', value)


class LocalThingsSelect(LocalThingsEntity, SelectEntity):

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        desc: SelectDesc = bound.desc
        if not desc.options_field and not callable(desc.options):
            self._attr_options = [self._display_option(o) for o in desc.options]

    def _display_option(self, value):
        """Normalize both current state and options through one path."""
        display_fn = self._bound.desc.display_fn
        fallback_fn = (
            (lambda raw: display_fn(raw, self._resources))
            if display_fn is not None else None
        )
        return _display(value, self.translation_key, fallback_fn)

    def _raw_options(self) -> list[str]:
        desc: SelectDesc = self._bound.desc
        if callable(desc.options):
            # Per-device option list computed from the full resource
            # snapshot (not just this entity's own href) -- e.g. a course
            # list decoded from a sibling resource. There is no static
            # fallback: when that resource isn't populated the callable
            # returns [] and the entity's exists_fn suppresses it entirely.
            # This entity's own subdevice's canonical view (issue #177), not
            # the raw actual-href snapshot -- see LocalThingsEntity._resources.
            return list(desc.options(self._resources) or [])
        if desc.options_field:
            rep = self.coordinator.last_resources.get(self._bound.href) or {}
            return list(rep.get(desc.options_field) or [])
        return list(desc.options)

    @property
    def options(self) -> list[str]:
        desc: SelectDesc = self._bound.desc
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
