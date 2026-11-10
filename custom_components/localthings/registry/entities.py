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
from datetime import datetime
from datetime import time as dt_time
from typing import Any, Literal

WriteFn = Callable[[Any, dict], "tuple[list[str], dict] | None"] | None
# ((kind, value), rep) -> (path_segs, body), for the composite media player.
MediaWriteFn = Callable[[tuple[str, Any], dict], "tuple[list[str], dict] | None"] | None
# (payload, rep, resources) -> a translation key, or None to allow the
# write. resources is the coordinator's full href->rep snapshot, for the same
# cross-resource lookups exists_fn needs (e.g. reading a sibling href's live
# option list).
ValidateFn = Callable[[Any, dict, dict], "str | None"] | None
DisplayFn = Callable[[Any, dict], Any] | None
ButtonPayloadFn = Callable[[datetime], Any] | None
# (picked time, local now) -> the payload write_fn receives, for a control
# whose device field holds a duration rather than a clock reading. The
# platform supplies `now` so the registry stays free of HA's timezone
# handling, the same split ButtonPayloadFn uses.
TimePayloadFn = Callable[[dt_time, datetime], Any] | None
# payload -> whether a write policy applies to that payload.
PayloadPredicate = Callable[[Any], bool] | None


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
    # Recheck exists_fn after setup: add when present, unavailable when
    # absent. For presence that follows live state, not the model. Only the
    # platforms that use entity.async_add_bound_entities add a late entity:
    # sensor, binary_sensor, number, switch, select, button, time and light.
    dynamic_presence: bool = False
    # Remove this entity's registry row when exists_fn excludes it. Only for
    # an exists_fn verified to mean "this model never has it" (#698, #747).
    prune_when_excluded: bool = False
    # (platform, key) rows this entity replaces, removed once it is included
    # on a rep that carries its field (not a stub or {}) and the old key, if
    # still bound, is excluded.
    supersedes: tuple[tuple[str, str], ...] = ()
    # Once included, remove this key's rows on its siblings' platforms. Only
    # where inclusion is a model fact, never live state, and where a rep that
    # can't decide yet excludes this descriptor.
    replaces_siblings: bool = False
    extra_state_attributes_fn: Callable[[dict, dict], dict[str, Any]] | None = None
    # One of cook.PARAM_*: a cook parameter the coordinator holds while an
    # idle oven can be started, instead of writing it (issue #473).
    cook_param: str | None = None
    # False for a write the appliance takes with Remote Control off; the
    # coordinator then leaves the refusal, if any, to the appliance.
    needs_remote_control: bool = True
    # Some boards apply a write but answer 4.00; require a fresh matching GET
    # before accepting it, including a refusal of a value already set (a no-op).
    # Raw writes retain the device's response.
    confirm_on_refusal: bool = False
    # The write carries one `<Group>_<Value>` token of a multi-group
    # x.com.samsung.da.modes list (robot_vacuum). The coordinator merges it
    # by prefix into the cached list, as it does for options, instead of
    # letting the one token stand in for the whole list until the next poll.
    merges_modes: bool = False
    # Wire body -> the body to merge into the cache, for a write whose effect
    # the device reports in fields it never accepts. The wire body is unchanged.
    optimistic_fn: Callable[[dict], dict] | None = None
    # A write that must not be resent once it may have reached the device:
    # a lost reply can follow one that landed, and a relative seek or a skip
    # would then apply twice. A write that never left is still retried.
    resend_unsafe_fn: PayloadPredicate = None
    # Cache the optimistic value only once the device acknowledged the
    # write, so a failed one can't become the next write's baseline.
    cache_after_ack_fn: PayloadPredicate = None
    # A payload that is a command, with no readable state behind it (a
    # skip): treated as write_only, so nothing is cached and polls of its
    # href aren't held back.
    command_fn: PayloadPredicate = None
    # The device can refuse a command inside a 2.04 and say so in the body,
    # {"controlResponse": {"result": false}} (AV boards).
    control_response: bool = False

    def __post_init__(self) -> None:
        # Excluded is the normal state of a dynamic entity, so neither
        # registry removal may act on one.
        if self.dynamic_presence and (
            self.prune_when_excluded or self.supersedes or self.replaces_siblings
        ):
            raise ValueError(f"{self.key}: registry removal can't apply to dynamic_presence")
        if self.prune_when_excluded and self.exists_fn is None:
            raise ValueError(f"{self.key}: prune_when_excluded needs an exists_fn")


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
    validate_fn: ValidateFn = None


@dataclass(frozen=True, kw_only=True)
class SwitchDesc(SamsungEntityDescription):
    device_class: str | None = None
    write_fn: WriteFn = None
    validate_fn: ValidateFn = None


@dataclass(frozen=True, kw_only=True)
class LightDesc(SamsungEntityDescription):
    supports_brightness: bool = False
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class ButtonDesc(SamsungEntityDescription):
    payload: Any = ""
    # Optional press-time payload generation. The button platform supplies
    # Home Assistant's current local time so registry modules stay HA-free.
    payload_fn: ButtonPayloadFn = None
    write_fn: WriteFn = None
    # The command field is accepted on POST but never appears in a readable
    # representation. Skip the coordinator's optimistic cache merge for it.
    write_only: bool = False


@dataclass(frozen=True, kw_only=True)
class NumberDesc(SamsungEntityDescription):
    mode: Literal["auto", "box", "slider"] = "slider"
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
    # (rep, resources) -> (min, max, step), or None to fall through to the
    # per-rep overrides above -- for bounds that live on a sibling href.
    bounds_fn: Callable[[dict, dict], tuple[float, float, float] | None] | None = None
    range_field: str | None = None  # resource field containing [min, max] list
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class TimeDesc(SamsungEntityDescription):
    write_fn: WriteFn = None
    payload_fn: TimePayloadFn = None


@dataclass(frozen=True, kw_only=True)
class ClimateDesc(SamsungEntityDescription):
    # Composite entity: binds one primary resource (its href) but the
    # climate platform reads sibling resources from the coordinator
    # snapshot and writes to several of them. write_fn takes a (kind,
    # value) payload and returns the (path_segs, body) for that sub-write.
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class ZoneClimateDesc(SamsungEntityDescription):
    # A heat pump zone as a climate entity: power, mode and temperature each
    # on their own href, named here so one platform class serves every zone.
    power_href: str = ""
    mode_href: str = ""
    temperature_href: str = ""
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


@dataclass(frozen=True)
class MediaModeSurface:
    """A selectable mode (source, sound mode): where it lives and how to read it."""

    href: str
    field: str
    options_field: str
    # Wire value -> label, for values with no readable name (`wifiidle`).
    labels: Mapping[str, str] | None = None


@dataclass(frozen=True, kw_only=True)
class MediaPlayerDesc(SamsungEntityDescription):
    # Binds the generic /capability/mediaPlayback resource; the family names
    # its sibling resources here, so media_player.py needs no per-family code.
    # write_fn maps (kind, value) -> (path_segs, body), like ClimateDesc.
    device_class: str = "speaker"
    power_href: str = ""
    audio_href: str = ""
    source: MediaModeSurface | None = None
    sound_mode: MediaModeSurface | None = None
    track_href: str | None = None
    shuffle_href: str | None = None
    # Advertises next/previous in supportedModes while a stream is active.
    track_control_href: str | None = None
    # resources -> the family's own playback state ("PLAYING", "PAUSED", ...),
    # preferred over the generic feed when it reports one.
    status_fn: Callable[[dict], str | None] | None = None
    # resources -> False when the generic playback and track feeds are stale,
    # e.g. a soundbar keeps its last stream's track on an HDMI input.
    stream_active_fn: Callable[[dict], bool] | None = None
    # False where the device refuses stop while paused, so it isn't offered.
    stop_when_paused: bool = True
    write_fn: MediaWriteFn = None
    validate_fn: ValidateFn = None


@dataclass(frozen=True, kw_only=True)
class VacuumDesc(SamsungEntityDescription):
    # Composite vacuum entity: rep_fn gives the HA activity; vacuum.py reads
    # the rest from the bound rep. write_fn takes a (group, value) payload.
    write_fn: WriteFn = None


PLATFORM_OF: dict[type, str] = {
    SensorDesc: "sensor",
    BinarySensorDesc: "binary_sensor",
    SelectDesc: "select",
    SwitchDesc: "switch",
    LightDesc: "light",
    ButtonDesc: "button",
    NumberDesc: "number",
    TimeDesc: "time",
    ClimateDesc: "climate",
    FanDesc: "fan",
    WaterHeaterDesc: "water_heater",
    ZoneClimateDesc: "climate",
    MediaPlayerDesc: "media_player",
    VacuumDesc: "vacuum",
}
