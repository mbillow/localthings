"""Base DeviceRegistry dataclass and builder."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field

from ..capability import Capability
from ..entities import BinarySensorDesc, SensorDesc


@dataclass(frozen=True)
class DeviceRegistry:
    """Registry of capabilities for a specific device type."""

    name: str
    capabilities: dict[str, list[Capability]]
    pattern_capabilities: list[Capability] = field(default_factory=list)


def _build(caps: list[Capability]) -> dict[str, list[Capability]]:
    """Build a capabilities dict from a list of Capability objects.

    Args:
        caps: List of Capability objects to organize by href.

    Returns:
        A dict mapping href to list of Capability objects sharing that href.

    Raises:
        ValueError: If any capability has href=None (use pattern_capabilities instead),
                   or if multiple caps share an href without all having rt_filter or match_fn.
    """
    out: dict[str, list[Capability]] = {}

    for cap in caps:
        if cap.href is None:
            raise ValueError("Use pattern_capabilities for href=None caps")
        if cap.href_prefix is not None:
            raise ValueError(
                f"href_prefix is only valid for pattern caps (href=None); "
                f"cap with href={cap.href!r} must not set href_prefix"
            )
        out.setdefault(cap.href, []).append(cap)

    # Validate that multi-cap hrefs have proper discrimination
    for href, cs in out.items():
        if len(cs) > 1 and any(c.rt_filter is None and c.match_fn is None for c in cs):
            raise ValueError(
                f"href {href!r} has multiple caps but at least one lacks rt_filter and match_fn"
            )

    return out


def exclude_when(cap: Capability, placeholder: Callable[[dict], bool]) -> Capability:
    """`cap`, binding nothing on a device `placeholder(resources)` recognizes.

    For a sibling slot that answers with constant filler: once none of its
    entities bind, the generic subdevice liveness gate rejects it.
    """
    return dataclasses.replace(
        cap,
        match_fn=lambda rep, resources: (
            not placeholder(resources) and (cap.match_fn is None or cap.match_fn(rep, resources))
        ),
    )


def read_only_when(cap: Capability, unconfirmed: Callable[[dict], bool]) -> list[Capability]:
    """`cap` as a control part and a read part, the controls binding nothing
    on a device `unconfirmed(resources)` recognizes: a family whose writes
    failed or were never tested keeps its readings. Controls come first, as
    they do within each capability (a power switch before its read-only
    twin)."""
    readings = tuple(d for d in cap.entities if isinstance(d, (SensorDesc, BinarySensorDesc)))
    controls = tuple(d for d in cap.entities if not isinstance(d, (SensorDesc, BinarySensorDesc)))
    if not controls:
        return [cap]
    match_fn = cap.match_fn or (lambda rep, resources: True)
    return [
        exclude_when(dataclasses.replace(cap, entities=controls, match_fn=match_fn), unconfirmed),
        dataclasses.replace(cap, entities=readings, match_fn=match_fn),
    ]
