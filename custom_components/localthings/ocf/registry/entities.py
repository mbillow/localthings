"""HA-shaped entity descriptions. The subclass *type* selects the HA platform.

Frozen dataclasses so the future native HA component can consume them as
EntityDescription subclasses unchanged. Read transforms live in value_fn;
presence gating in exists_fn; write logic in write_fn on command platforms.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

WriteFn = Optional[Callable[[Any, dict], "tuple[list[str], dict] | None"]]


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
    exists_fn: Optional[Callable[[dict], bool]] = None


@dataclass(frozen=True, kw_only=True)
class SensorDesc(SamsungEntityDescription):
    device_class: Optional[str] = None
    state_class: Optional[str] = None
    unit: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class BinarySensorDesc(SamsungEntityDescription):
    device_class: Optional[str] = None         # value_fn must return bool


@dataclass(frozen=True, kw_only=True)
class SelectDesc(SamsungEntityDescription):
    options: Any = ()        # tuple[str,...] | Callable[[dict], list[str]]
    options_field: Optional[str] = None  # resource field that contains the live options list
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class SwitchDesc(SamsungEntityDescription):
    device_class: Optional[str] = None
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class ButtonDesc(SamsungEntityDescription):
    payload: str = ''
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class NumberDesc(SamsungEntityDescription):
    device_class: Optional[str] = None
    unit: Optional[str] = None
    native_min: Optional[float] = None
    native_max: Optional[float] = None
    step: Optional[float] = None
    range_field: Optional[str] = None  # resource field containing [min, max] list
    write_fn: WriteFn = None


PLATFORM_OF: dict[type, str] = {
    SensorDesc:       'sensor',
    BinarySensorDesc: 'binary_sensor',
    SelectDesc:       'select',
    SwitchDesc:       'switch',
    ButtonDesc:       'button',
    NumberDesc:       'number',
}
