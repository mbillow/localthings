"""Base entity for Local Things."""

from __future__ import annotations

import re

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .oven_labels import CAVITY_KEYS, main_cavity_divider
from .registry.adapter import _key
from .registry.batch import is_stub_rep
from .registry.discovery import BoundEntity, _snake_to_title
from .registry.subdevices import MAIN


def _is_included(bound: BoundEntity, coordinator: LocalThingsCoordinator) -> bool:
    """Return False if the entity should not be registered for this device.

    Explicit exists_fn takes priority. Otherwise, if the entity has a
    field, require that field to be present in the resource rep so that
    optional fields on shared resources don't create phantom entities.

    A stub rep (is_stub_rep) is included anyway so it can be populated by
    sub-polls. A genuinely empty {} rep is included too by this default
    gate: whether empty means "not populated yet" or "permanently
    unsupported" needs per-field domain knowledge this generic gate
    doesn't have (e.g. /alarms/vs/0's {} is fridge.py's documented normal
    no-alarm state, not an absence signal). Only a capability whose author
    has verified a field is genuinely never populated opts into stricter
    gating with its own exists_fn (see common.ENERGY_METER, issue #127).

    `bound.href` is already the actual href (issue #177); `exists_fn` gets
    `bound`'s own subdevice's canonical view instead of the raw snapshot,
    same rule as everywhere else a whole-resources-dict scan happens --
    this is a free function, so it can't use self._resources.

    Reads `discovery_resources`, not `last_resources`: on an offline load
    (issue #295) the live cache is still empty, and judging existence
    against it would filter every rehydrated entity away.
    """
    rep = coordinator.discovery_resources.get(bound.href)
    if rep is None:
        return False
    if bound.desc.exists_fn is not None:
        return bound.desc.exists_fn(rep, coordinator.discovery_canonical(bound.subdevice))
    if bound.desc.field:
        if not rep or is_stub_rep(rep):
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
        self._state_key = _key(bound)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.device_key}_{self._state_key}"
        self._oven_divider_href = None
        if (
            bound.subdevice == MAIN
            and bound.desc.key in CAVITY_KEYS
            and getattr(coordinator, "device_type_name", None) in {"oven", "range"}
        ):
            self._oven_divider_href = main_cavity_divider(
                coordinator._cook_view(coordinator.discovery_resources), coordinator.subdevices
            )
        if bound.desc.translation_placeholders is not None:
            self._attr_translation_placeholders = dict(bound.desc.translation_placeholders)
        elif bound.desc.use_instance_name:
            self._attr_translation_placeholders = {
                "instance_name": _localized_instance_name(
                    _instance_display_name(bound, self._state_key), coordinator.instance_names
                )
            }
        if self._oven_divider_href is not None:
            self._update_cavity_name()

        # _attr_name is deliberately left unset: HA gives an explicitly-set
        # name precedence over the translation catalog, so setting it here
        # would make every entity untranslatable. A platform that wants the
        # bare device name sets _attr_name = None itself (see fan.py).
        self._attr_icon = bound.desc.icon
        raw_cat = bound.desc.entity_category
        self._attr_entity_category = EntityCategory(raw_cat) if raw_cat else None
        self._attr_entity_registry_enabled_default = bound.desc.enabled_default

    def _update_cavity_name(self) -> bool:
        if self._oven_divider_href is None:
            return False
        # Only an explicit divider removal establishes whole-oven operation.
        state = self.coordinator.resource(self._oven_divider_href).get("x.com.samsung.da.connected")
        label = "whole" if state == "Off" else "upper"
        name = self.coordinator.instance_names.get(
            f"oven_cavity_{label}", "Oven" if label == "whole" else "Upper"
        )
        if self.translation_placeholders.get("cavity") == name:
            return False
        self._attr_translation_placeholders = {**self.translation_placeholders, "cavity": name}
        self.__dict__.pop("name", None)
        return True

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
            key = tk(self._resources or self.coordinator.discovery_canonical(self._bound.subdevice))
        else:
            key = tk if tk is not None else self._bound.desc.key
        return (
            f"oven_cavity_{key}" if self._oven_divider_href is not None and key is not None else key
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        # The frontend translates state from the registry's copy of the key,
        # which HA never refreshes on its own (issue #531).
        entry = self.registry_entry
        if (
            self._oven_divider_href is not None
            and self._update_cavity_name()
            and entry is not None
            and isinstance(name := self.name, str)
        ):
            entry = self.registry_entry = er.async_get(self.hass).async_update_entity(
                entry.entity_id, original_name=name
            )
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
    def available(self) -> bool:
        if not super().available:
            return False
        available_fn = self._bound.desc.available_fn
        return available_fn is None or available_fn(
            self.coordinator.resource(self._bound.href), self._resources
        )

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info_for(self._bound.subdevice)
