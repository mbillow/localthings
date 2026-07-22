"""HA-shaped entity descriptions. The subclass *type* selects the HA platform.

Frozen dataclasses so the future native HA component can consume them as
EntityDescription subclasses unchanged. Read transforms live in value_fn;
presence gating in exists_fn; write logic in write_fn on command platforms;
pre-write rejection (surfaced to the user, not just logged) in validate_fn
where a description declares one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

WriteFn = Optional[Callable[[Any, dict], "tuple[list[str], dict] | None"]]
# (payload, rep, resources) -> a human-readable rejection message, or None to
# allow the write. resources is the coordinator's full href->rep snapshot, for
# the same cross-resource lookups exists_fn needs (e.g. reading a sibling
# href's live option list).
ValidateFn = Optional[Callable[[Any, dict, dict], "str | None"]]


def _identity(v: Any) -> Any:
    return v


@dataclass(frozen=True, kw_only=True)
class SamsungEntityDescription:
    key: str
    field: str = ''
    name: Optional[str] = None
    translation_key: Optional[str] = None
    icon: Optional[str] = None
    entity_category: Optional[str] = None      # 'diagnostic' | 'config' | None
    enabled_default: bool = True
    value_fn: Callable[[Any], Any] = _identity
    rep_fn: Optional[Callable[[dict], Any]] = None   # replaces field+value_fn; receives full rep
    # (rep, resources): rep is this entity's own href's representation;
    # resources is the coordinator's full href->rep snapshot, for gating
    # presence on a sibling resource (e.g. laundry.cycle_options's source).
    exists_fn: Optional[Callable[[dict, dict], bool]] = None


@dataclass(frozen=True, kw_only=True)
class SensorDesc(SamsungEntityDescription):
    device_class: Optional[str] = None
    state_class: Optional[str] = None
    unit: Optional[str] = None
    unit_fn: Optional[Callable[[dict], str]] = None  # overrides `unit` from the live rep, when set
    options: Optional[tuple] = None  # required by HA when device_class == 'enum'


@dataclass(frozen=True, kw_only=True)
class BinarySensorDesc(SamsungEntityDescription):
    device_class: Optional[str] = None         # value_fn must return bool


@dataclass(frozen=True, kw_only=True)
class SelectDesc(SamsungEntityDescription):
    options: Any = ()        # tuple[str,...] | Callable[[dict[str, dict]], list[str]]
    # callable form receives the coordinator's full href->rep resource
    # snapshot (not just this entity's own href) and returns raw device
    # option values; see select.py's LocalThingsSelect._raw_options().
    options_field: Optional[str] = None  # resource field that contains the live options list
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class SwitchDesc(SamsungEntityDescription):
    device_class: Optional[str] = None
    write_fn: WriteFn = None
    validate_fn: ValidateFn = None


@dataclass(frozen=True, kw_only=True)
class ButtonDesc(SamsungEntityDescription):
    payload: str = ''
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class NumberDesc(SamsungEntityDescription):
    device_class: Optional[str] = None
    unit: Optional[str] = None
    unit_fn: Optional[Callable[[dict], str]] = None  # overrides `unit` from the live rep, when set
    native_min: Optional[float] = None
    native_max: Optional[float] = None
    step: Optional[float] = None
    range_field: Optional[str] = None  # resource field containing [min, max] list
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class TimeDesc(SamsungEntityDescription):
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class ClimateDesc(SamsungEntityDescription):
    # A composite entity: it binds one *primary* resource (its href) but the
    # climate platform reads sibling resources (power, temperature, wind) from
    # the coordinator snapshot and writes to several of them. write_fn takes a
    # (kind, value) payload from the platform and returns the (path_segs, body)
    # for that one sub-write, so a single desc drives multi-resource writes.
    write_fn: WriteFn = None


PLATFORM_OF: dict[type, str] = {
    SensorDesc:       'sensor',
    BinarySensorDesc: 'binary_sensor',
    SelectDesc:       'select',
    SwitchDesc:       'switch',
    ButtonDesc:       'button',
    NumberDesc:       'number',
    TimeDesc:         'time',
    ClimateDesc:      'climate',
}
