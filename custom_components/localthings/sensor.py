"""Sensor platform for Local Things."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import cast

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_FINISH_TIME_HYSTERESIS_MINUTES,
    DEFAULT_FINISH_TIME_HYSTERESIS_MINUTES,
    DOMAIN,
)
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included
from .observe import MODE_OBSERVE, MODE_POLL
from .registry.entities import SensorDesc


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        LocalThingsSensor(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, SensorDesc) and _is_included(b, coordinator)
    ]
    entities.append(LocalThingsConnectionModeSensor(coordinator))
    async_add_entities(entities)


class LocalThingsSensor(LocalThingsEntity, SensorEntity):
    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        desc = cast(SensorDesc, bound.desc)
        self._attr_native_unit_of_measurement = desc.unit
        self._attr_device_class = (
            SensorDeviceClass(desc.device_class) if desc.device_class else None
        )
        self._attr_state_class = SensorStateClass(desc.state_class) if desc.state_class else None
        # Always set for static options; callable options are resolved from the
        # live canonical resource snapshot in the property below.
        self._attr_options = (
            None if callable(desc.options) else list(desc.options) if desc.options else None
        )
        self._hysteresis_value = None
        self._sticky_value = None
        self._sticky_until: float | None = None
        self._sticky_spent = False

    @property
    def native_unit_of_measurement(self):
        desc = cast(SensorDesc, self._bound.desc)
        if desc.unit_fn is not None:
            return desc.unit_fn(self.coordinator.resource(self._bound.href))
        return self._attr_native_unit_of_measurement

    @property
    def options(self):
        """Declared options, plus whatever this device is actually reporting.

        HA raises for an enum state outside `options`, which would turn any
        device value we don't have a translation for into a broken entity --
        the opposite of this registry's rule that an unrecognized value
        renders raw. Admitting the live value keeps it displayable; it just
        shows untranslated (PR #341 review).
        """
        desc = cast(SensorDesc, self._bound.desc)
        declared = (
            list(desc.options(self._resources) or [])
            if callable(desc.options)
            else self._attr_options
        )
        if declared is None:
            return None
        value = self.native_value
        if not isinstance(value, str) or value in declared:
            return declared
        return [*declared, value]

    @property
    def native_value(self):
        raw = (self.coordinator.data or {}).get(self._state_key)
        desc = cast(SensorDesc, self._bound.desc)
        if desc.sticky_fn is not None:
            raw = self._apply_sticky(raw, desc)
        if not desc.hysteresis:
            return raw
        return self._apply_hysteresis(raw)

    def _apply_sticky(self, raw, desc: SensorDesc):
        """Freeze this entity at a value for up to `desc.sticky_seconds`
        after `desc.sticky_fn` next stops matching this href's live rep
        (issue #345 -- see operational.py's `_just_finished` for the
        motivating case). Entity-instance state only, exactly like
        `_hysteresis_value` above -- never written back to the coordinator
        cache, so write_fn, diagnostics, and the observe-mode sweep
        comparison keep seeing real device data throughout.

        `sticky_fn`/`sticky_bypass_fn` read this href's live rep rather
        than the already-computed `raw`, so they can key on fields rep_fn
        has collapsed away -- but they never compute a value. `raw` and
        the frozen `sticky_value` are the only things returned here, so a
        held entity and a free-running one agree on what "live" means; a
        hook that broke that rule caused issue #358.

        At most one window per `sticky_bypass_fn` cycle: arming marks the
        hold spent, and only the bypass clears that. So a `sticky_fn` that
        keeps matching (firmware leaving the field stuck -- the quirk
        `_completion_minutes` works around) can't extend the window, and
        one flapping in and out can't restart it either, before or after
        expiry. Expiry alone doesn't re-open the door: without something
        the calibre of "a new cycle is actually running" in between, a
        second Finish is the same Finish, and re-arming on it would strobe
        the entity between held and live once per window -- exactly the
        repeated announcements #345 and #358 are about.

        `sticky_bypass_fn` drops the hold and returns `raw`, for when "not
        sticky right now" is ambiguous between "went idle, honor the hold"
        and "genuinely moved on to new data". It is both the early release
        and the only re-arm, so it should demand positive evidence of that
        move; when unsure, letting the window run out is the cheaper
        mistake.
        """
        assert desc.sticky_fn is not None  # native_value only calls this when set
        rep = self.coordinator.resource(self._bound.href)
        now = time.monotonic()

        if desc.sticky_fn(rep):
            if not self._sticky_spent:
                self._sticky_value = (
                    desc.sticky_value_fn(rep) if desc.sticky_value_fn is not None else raw
                )
                self._sticky_until = now + desc.sticky_seconds
                self._sticky_spent = True
        elif desc.sticky_bypass_fn is not None and desc.sticky_bypass_fn(rep):
            self._sticky_until = None
            self._sticky_spent = False
            return raw

        holding = self._sticky_until is not None and now < self._sticky_until
        return self._sticky_value if holding else raw

    def _apply_hysteresis(self, raw):
        """Hold the last value this entity actually reported until a new one
        differs by at least the configured threshold, regardless of how long
        that difference has been building up (this is a deadband, not a
        time-based debounce).

        Values like finish_time are `now() + remaining`, recomputed from
        scratch every poll -- both wall-clock drift between the device's own
        remaining-time updates and the device revising its own estimate mid-
        cycle produce a stream of small, real changes that are individually
        meaningless but each trigger a recorder/logbook entry. A cycle
        ending (raw is None) or starting (cache empty) always passes through
        immediately -- only in-between jitter while a value already exists
        on both sides gets held back.
        """
        assert self.coordinator.config_entry is not None
        threshold_min = self.coordinator.config_entry.options.get(
            CONF_FINISH_TIME_HYSTERESIS_MINUTES, DEFAULT_FINISH_TIME_HYSTERESIS_MINUTES
        )
        if (
            threshold_min
            and raw is not None
            and self._hysteresis_value is not None
            and abs(raw - self._hysteresis_value) < timedelta(minutes=threshold_min)
        ):
            return self._hysteresis_value
        self._hysteresis_value = raw
        return raw


class LocalThingsConnectionModeSensor(CoordinatorEntity[LocalThingsCoordinator], SensorEntity):
    """Diagnostic sensor exposing whether this device is currently
    receiving push notifications (observe mode) or being polled only.
    Disabled by default — it's for troubleshooting, not everyday use."""

    _attr_has_entity_name = True
    _attr_translation_key = "connection_mode"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [MODE_OBSERVE, MODE_POLL]  # noqa: RUF012 -- HA `_attr_*` convention

    def __init__(self, coordinator: LocalThingsCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.device_key}_connection_mode"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> str:
        return self.coordinator.observe_mode
