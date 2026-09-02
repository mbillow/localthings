"""HA-shaped entity descriptions. The subclass *type* selects the HA platform.

Frozen dataclasses so the future native HA component can consume them as
EntityDescription subclasses unchanged. Read transforms live in value_fn;
presence gating in exists_fn; write logic in write_fn on command platforms;
pre-write rejection (surfaced to the user, not just logged) in validate_fn
where a description declares one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

WriteFn = Callable[[Any, dict], "tuple[list[str], dict] | None"] | None
# (payload, rep, resources) -> a translation key, or None to allow the
# write. resources is the coordinator's full href->rep snapshot, for the same
# cross-resource lookups exists_fn needs (e.g. reading a sibling href's live
# option list).
ValidateFn = Callable[[Any, dict, dict], "str | None"] | None
DisplayFn = Callable[[Any, dict], Any] | None


def _identity(v: Any) -> Any:
    return v


@dataclass(frozen=True, kw_only=True)
class SamsungEntityDescription:
    key: str
    field: str = ""
    # Defaults to `key`: entity names and states live in translations/, never
    # here, so a descriptor only sets this to share one catalog entry across
    # several descriptors, or to point at a differently-named one.
    translation_key: Any = None  # str | Callable[[dict[str, dict]], Optional[str]]
    # callable form receives the full href->rep snapshot and returns the key
    # to use -- for a descriptor shared across board generations whose
    # state-code meaning isn't consistent between them; see
    # laundry.cycle_select's table-id-gated resolver.
    translation_placeholders: Mapping[str, str] | None = None
    # Dynamic resources such as fridge compartments and ice makers use a
    # device-provided or href-derived instance label inside a translated name.
    use_instance_name: bool = False
    icon: str | None = None
    entity_category: str | None = None  # 'diagnostic' | 'config' | None
    enabled_default: bool = True
    value_fn: Callable[[Any], Any] = _identity
    rep_fn: Callable[[dict], Any] | None = None  # replaces field+value_fn; receives full rep
    # (rep, resources): rep is this entity's own href's representation;
    # resources is the coordinator's full href->rep snapshot, for gating
    # presence on a sibling resource (e.g. laundry.cycle_options's source).
    exists_fn: Callable[[dict, dict], bool] | None = None


@dataclass(frozen=True, kw_only=True)
class SensorDesc(SamsungEntityDescription):
    device_class: str | None = None
    state_class: str | None = None
    unit: str | None = None
    unit_fn: Callable[[dict], str] | None = None  # overrides `unit` from the live rep, when set
    # Required by HA when device_class == 'enum'. The callable form receives
    # the coordinator's canonical href->rep snapshot and returns final sensor
    # state options; its input shape matches SelectDesc.options.
    options: Any = None  # tuple[str, ...] | Callable[[dict[str, dict]], list[str]]
    # Opt-in: gate this value behind CONF_FINISH_TIME_HYSTERESIS_MINUTES
    # (see sensor.py). Only for values expected to jitter between
    # device-side revisions -- not a general-purpose flag.
    hysteresis: bool = False
    # Opt-in, entity-instance-only hold -- see sensor.py's _apply_sticky
    # for the full contract (arm/value/bypass semantics, one window per
    # bypass, why this never touches the coordinator cache).
    # sticky_fn arms it; sticky_value_fn picks what to freeze at that
    # moment (defaults to rep_fn's own result); sticky_bypass_fn drops the
    # hold and lets rep_fn's own live result through; sticky_seconds
    # bounds how long it can hold. There is deliberately no hook for
    # computing a live value differently from rep_fn -- see issue #358.
    sticky_fn: Callable[[dict], bool] | None = None
    sticky_value_fn: Callable[[dict], Any] | None = None
    sticky_bypass_fn: Callable[[dict], bool] | None = None
    sticky_seconds: float = 300.0


@dataclass(frozen=True, kw_only=True)
class BinarySensorDesc(SamsungEntityDescription):
    device_class: str | None = None  # value_fn must return bool


@dataclass(frozen=True, kw_only=True)
class SelectDesc(SamsungEntityDescription):
    options: Any = ()  # tuple[str,...] | Callable[[dict[str, dict]], list[str]]
    # callable form receives the coordinator's full href->rep resource
    # snapshot (not just this entity's own href) and returns raw device
    # option values; see select.py's LocalThingsSelect._raw_options().
    options_field: str | None = None  # resource field that contains the live options list
    # Optional device-specific fallback for values absent from the translation
    # catalog. Receives (raw_value, canonical_resources); select.py applies it
    # identically to the current state and every option.
    display_fn: DisplayFn = None
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class SwitchDesc(SamsungEntityDescription):
    device_class: str | None = None
    write_fn: WriteFn = None
    validate_fn: ValidateFn = None


@dataclass(frozen=True, kw_only=True)
class ButtonDesc(SamsungEntityDescription):
    payload: str = ""
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class NumberDesc(SamsungEntityDescription):
    device_class: str | None = None
    unit: str | None = None
    unit_fn: Callable[[dict], str] | None = None  # overrides `unit` from the live rep, when set
    native_min: float | None = None
    native_max: float | None = None
    step: float | None = None
    # Override native_min/max/step from the live rep, when set -- same
    # "static default, live override" shape as unit_fn, for resources whose
    # bounds depend on a per-device value (e.g. Celsius vs. Fahrenheit).
    native_min_fn: Callable[[dict], float] | None = None
    native_max_fn: Callable[[dict], float] | None = None
    step_fn: Callable[[dict], float] | None = None
    range_field: str | None = None  # resource field containing [min, max] list
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class TimeDesc(SamsungEntityDescription):
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class ClimateDesc(SamsungEntityDescription):
    # Composite entity: binds one primary resource (its href) but the
    # climate platform reads sibling resources from the coordinator
    # snapshot and writes to several of them. write_fn takes a (kind,
    # value) payload and returns the (path_segs, body) for that sub-write.
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class FanDesc(SamsungEntityDescription):
    # Composite fan entity: reads power from /power/0 and speed/support data
    # from its bound href. Payloads are (kind, value), like ClimateDesc.
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class WaterHeaterDesc(SamsungEntityDescription):
    # Composite water_heater entity, same (kind, value) -> (path_segs,
    # body) write_fn shape as ClimateDesc/FanDesc.
    write_fn: WriteFn = None


PLATFORM_OF: dict[type, str] = {
    SensorDesc: "sensor",
    BinarySensorDesc: "binary_sensor",
    SelectDesc: "select",
    SwitchDesc: "switch",
    ButtonDesc: "button",
    NumberDesc: "number",
    TimeDesc: "time",
    ClimateDesc: "climate",
    FanDesc: "fan",
    WaterHeaterDesc: "water_heater",
}
