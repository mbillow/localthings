"""Base entity for Local Things."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .registration import bound_id

# Tests import it from here, where it lived before registration.py.
from .registration import is_included as _is_included  # noqa: F401
from .registry.adapter import _key
from .registry.discovery import BoundEntity, _snake_to_title


def async_add_bound_entities(
    entry: ConfigEntry,
    coordinator: LocalThingsCoordinator,
    async_add_entities: AddEntitiesCallback,
    select: Callable[[BoundEntity], bool],
    factory: Callable[[BoundEntity], Entity],
    extra: Iterable[Entity] = (),
) -> None:
    """Add every selected bound the coordinator says is registrable, now and
    on each update after, so an entity a later discovery or live evidence
    brings in is added in place instead of by reloading the entry (#807).

    Each is added once, so one that drops out and comes back reuses the same
    entity, with its ID and customizations; LocalThingsEntity.available
    covers the gaps.
    """
    added: dict[tuple[str, str, str], Entity] = {}
    # Selected bounds not added yet that live data can still bring in, and
    # the registration generation they were collected at: nothing else can
    # become registrable until the generation moves.
    pending: list[BoundEntity] = []
    seen: list[int | None] = [None]

    def _rebind(current: dict[tuple[str, str, str], BoundEntity]) -> None:
        """Replace an entity whose id a later discovery bound differently
        (another href or descriptor after a firmware update), keeping its
        registry row and entity_id; the reload this replaces rebuilt it."""
        for ident, entity in list(added.items()):
            bound = current.get(ident)
            if bound is None or getattr(entity, "_bound", bound) == bound:
                continue
            replacement = factory(bound)
            added[ident] = replacement

            async def _swap(old: Entity = entity, new: Entity = replacement) -> None:
                await old.async_remove()
                async_add_entities([new])

            entry.async_create_background_task(
                coordinator.hass, _swap(), f"localthings rebind {ident}"
            )

    def _new() -> list[Entity]:
        if seen[0] != coordinator.registration.generation:
            seen[0] = coordinator.registration.generation
            current = {bound_id(b): b for b in coordinator.bound if select(b)}
            _rebind(current)
            candidates = [b for ident, b in current.items() if ident not in added]
            pending[:] = [b for b in candidates if b.desc.dynamic_presence]
        else:
            candidates = list(pending)
        entities = []
        for bound in candidates:
            ident = bound_id(bound)
            if ident in added or not coordinator.registrable(bound):
                continue
            entity = factory(bound)
            added[ident] = entity
            if bound in pending:
                pending.remove(bound)
            entities.append(entity)
        return entities

    async_add_entities([*_new(), *extra])

    @callback
    def _add_new() -> None:
        if entities := _new():
            async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))


def _derive_name(state_key: str) -> str:
    """Turn a snake_case state key into a title-cased label.

    Strips a trailing instance number of 0 (singleton), promotes any other
    instance number with a space: "door_cooler_open1" → "Door Cooler Open 1".

    Entity names themselves come from the translation catalog; this only
    builds the {instance_name} placeholder those translations interpolate,
    for a device that named its own compartments/ice makers.
    """
    name = re.sub(r"(\d+)$", lambda m: f" {m.group()}" if int(m.group()) > 0 else "", state_key)
    return _snake_to_title(name).strip()


def _instance_display_name(bound: BoundEntity, state_key: str) -> str:
    """Return the stable vendor/href instance label used in a name placeholder."""
    if bound.instance_name:
        return bound.instance_name
    source = bound.key_override or state_key
    suffix = f"_{bound.desc.key}"
    if source.endswith(suffix):
        source = source[: -len(suffix)]
    elif bound.instance and source.endswith(bound.instance):
        source = source[: -len(bound.instance)] + bound.instance.replace("_", " ")
    return _derive_name(source)


# hassfest rejects an unknown top-level translation section, and `selector`
# is the one that holds free-standing string tables.
_INSTANCE_NAMES_PREFIX = f"component.{DOMAIN}.selector.instance_name.options."


async def async_load_instance_names(hass: HomeAssistant) -> dict[str, str]:
    """The {instance_name} labels in HA's language, keyed by the slug of
    the name Python derives for them (issue #533)."""
    strings = await async_get_translations(hass, hass.config.language, "selector", {DOMAIN})
    return {
        key.removeprefix(_INSTANCE_NAMES_PREFIX): value
        for key, value in strings.items()
        if key.startswith(_INSTANCE_NAMES_PREFIX)
    }


def _localized_instance_name(name: str, localized: dict[str, str]) -> str:
    """An unlisted name -- a numbered instance or an ice maker name no
    fixture has shown yet -- keeps its derived English form."""
    return localized.get(name.lower().replace(" ", "_"), name)


class LocalThingsEntity(CoordinatorEntity[LocalThingsCoordinator]):
    """Base class for all Local Things entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LocalThingsCoordinator, bound: BoundEntity) -> None:
        super().__init__(coordinator)
        self._bound = bound
        self._bound_id = bound_id(bound)
        self._state_key = _key(bound)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.device_key}_{self._state_key}"
        if bound.desc.translation_placeholders is not None:
            self._attr_translation_placeholders = dict(bound.desc.translation_placeholders)
        elif bound.desc.use_instance_name:
            self._attr_translation_placeholders = {
                "instance_name": _localized_instance_name(
                    _instance_display_name(bound, self._state_key), coordinator.instance_names
                )
            }

        # _attr_name is deliberately left unset: HA gives an explicitly-set
        # name precedence over the translation catalog, so setting it here
        # would make every entity untranslatable. A platform that wants the
        # bare device name sets _attr_name = None itself (see fan.py).
        self._attr_icon = bound.desc.icon
        raw_cat = bound.desc.entity_category
        self._attr_entity_category = EntityCategory(raw_cat) if raw_cat else None
        self._attr_entity_registry_enabled_default = bound.desc.enabled_default

    @property
    def translation_key(self) -> str | None:
        """The descriptor's catalog key, defaulting to its own `key`.

        Overrides Entity.translation_key so a callable descriptor (e.g.
        laundry.cycle_select's table-id-gated resolver) is re-evaluated
        against live coordinator data on every access, not resolved once
        at construction time -- a static resolution would risk baking in
        a permanent None if the first poll handed a sibling an empty stub
        rep (see _is_included's docstring) before it populated.

        Falls back to the discovery snapshot while the live cache is empty:
        an offline load (issue #295) registers entities before any poll, and
        HA keeps the key it sees then until the entity is next added
        (issue #531).
        """
        tk = self._bound.desc.translation_key
        if callable(tk):
            return tk(
                self._resources or self.coordinator.discovery_canonical(self._bound.subdevice)
            )
        return tk if tk is not None else self._bound.desc.key

    @callback
    def _handle_coordinator_update(self) -> None:
        # The frontend translates state from the registry's copy of the key,
        # which HA never refreshes on its own (issue #531).
        entry = self.registry_entry
        if (
            entry is not None
            and callable(self._bound.desc.translation_key)
            and (tk := self.translation_key) != entry.translation_key
        ):
            self.registry_entry = er.async_get(self.hass).async_update_entity(
                entry.entity_id, translation_key=tk
            )
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.provides(self._bound, self._bound_id)

    @property
    def _resources(self) -> dict:
        """This entity's own subdevice's canonical resources view (issue
        #177) -- see coordinator.canonical_resources. Any platform property
        needing the whole resources dict, not one href via
        `coordinator.resource(href)`, must read through this instead of
        `coordinator.last_resources`, or a sibling subdevice's own hrefs
        could leak into this entity's view. For MAIN this is exactly
        `coordinator.last_resources`."""
        return self.coordinator.canonical_resources(self._bound.subdevice)

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info_for(self._bound.subdevice)
