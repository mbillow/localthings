"""Base DeviceRegistry dataclass and builder."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..capability import Capability


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
            raise ValueError(f"Use pattern_capabilities for href=None caps")
        if cap.href_prefix is not None:
            raise ValueError(
                f"href_prefix is only valid for pattern caps (href=None); "
                f"cap with href={cap.href!r} must not set href_prefix")
        out.setdefault(cap.href, []).append(cap)

    # Validate that multi-cap hrefs have proper discrimination
    for href, cs in out.items():
        if len(cs) > 1 and any(c.rt_filter is None and c.match_fn is None for c in cs):
            raise ValueError(
                f"href {href!r} has multiple caps but at least one lacks rt_filter and match_fn")

    return out
