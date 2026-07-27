"""Base entity for Local Things."""
from __future__ import annotations

import re

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.const import EntityCategory

from .registry.adapter import _key
from .registry.discovery import BoundEntity, _snake_to_title

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator


def _is_included(bound: BoundEntity, coordinator: 'LocalThingsCoordinator') -> bool:
    """Return False if the entity should not be registered for this device.

    Explicit exists_fn takes priority. Otherwise, if the entity has a field,
    require that field to be present in the resource rep so that optional
    fields on shared resources don't create phantom entities.

    An empty rep ({}) means /device/0 returned a stub for this resource —
    the resource exists on the device but data hasn't been fetched yet.
    In that case we include the entity so it can be populated by sub-polls.
    """
    rep = coordinator.last_resources.get(bound.href)
    if rep is None:
        return False
    if bound.desc.exists_fn is not None:
        return bound.desc.exists_fn(rep, coordinator.last_resources)
    if bound.desc.field:
        if not rep:  # stub — resource known to exist, data not yet fetched
            return True
        return bound.desc.field in rep
    return True  # rep_fn or no-field entities (ButtonDesc) are always included


def _derive_name(state_key: str) -> str:
    """Turn a snake_case state key into a title-cased label.

    Strips a trailing instance number of 0 (singleton), promotes any other
    instance number with a space: "door_cooler_open1" → "Door Cooler Open 1".

    Entity names themselves come from the translation catalog; this only
    builds the {instance_name} placeholder those translations interpolate,
    for a device that named its own compartments/ice makers.
    """
    name = re.sub(r'(\d+)$', lambda m: f' {m.group()}' if int(m.group()) > 0 else '', state_key)
    return _snake_to_title(name).strip()


def _instance_display_name(bound: BoundEntity, state_key: str) -> str:
    """Return the stable vendor/href instance label used in a name placeholder."""
    if bound.instance_name:
        return bound.instance_name
    source = bound.key_override or state_key
    suffix = f"_{bound.desc.key}"
    if source.endswith(suffix):
        source = source[:-len(suffix)]
    elif bound.instance and source.endswith(bound.instance):
        source = source[:-len(bound.instance)] + bound.instance.replace("_", " ")
    return _derive_name(source)


class LocalThingsEntity(CoordinatorEntity[LocalThingsCoordinator]):
    """Base class for all Local Things entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LocalThingsCoordinator, bound: BoundEntity) -> None:
        super().__init__(coordinator)
        self._bound = bound
        self._state_key = _key(bound)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.device_serial}_{self._state_key}"
        if bound.desc.translation_placeholders is not None:
            self._attr_translation_placeholders = dict(
                bound.desc.translation_placeholders
            )
        elif bound.desc.use_instance_name:
            self._attr_translation_placeholders = {
                "instance_name": _instance_display_name(bound, self._state_key)
            }

        # _attr_name is deliberately left unset: Home Assistant gives an
        # explicitly-set name precedence over the translation catalog, so
        # setting it here would make every entity untranslatable. Every
        # descriptor resolves to a catalog entry (see translation_key below);
        # a platform that wants the bare device name instead sets
        # _attr_name = None itself, as fan.py does for the hood's main entity.
        self._attr_icon = bound.desc.icon
        raw_cat = bound.desc.entity_category
        self._attr_entity_category = EntityCategory(raw_cat) if raw_cat else None
        self._attr_entity_registry_enabled_default = bound.desc.enabled_default

    @property
    def translation_key(self) -> str | None:
        """The descriptor's catalog key, defaulting to its own `key`.

        Overrides Entity.translation_key (a property upstream, not a plain
        attribute) so a callable descriptor -- e.g. laundry.cycle_select's
        table-id-gated resolver -- is re-evaluated against live coordinator
        data on every access, not resolved once at construction time.

        Discovery runs on the first /device/0 poll, which the entity
        registry already documents can hand a sibling resource an empty
        stub rep before it's actually been fetched (see _is_included's
        docstring) -- a static one-time resolution here would risk baking
        in a permanent None (no translation) for the entity's whole
        lifetime if that stub hadn't populated yet, even once the real
        value arrives on a later poll.
        """
        tk = self._bound.desc.translation_key
        if callable(tk):
            return tk(self.coordinator.last_resources)
        return tk if tk is not None else self._bound.desc.key

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info
