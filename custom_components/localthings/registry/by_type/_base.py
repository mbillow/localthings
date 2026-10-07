"""Base DeviceRegistry dataclass and builder."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from ..capability import Capability


@dataclass(frozen=True)
class DeviceRegistry:
    """Registry of capabilities for a specific device type."""

    name: str
    capabilities: dict[str, list[Capability]]
    pattern_capabilities: list[Capability] = field(default_factory=list)
    # No /device/0 Collection: read one href at a time (registry/flat.py),
    # and found by a ClientHello probe when the port moves, as these boards
    # advertise nothing in plaintext.
    flat: bool = False
    # Poll tiers that may carry an OBSERVE subscription; () when
    # subscriptions cost other clients their session (the AV boards).
    subscribe_tiers: tuple[str, ...] = ("hot", "warm")
    # Write-settle window in seconds; None keeps the coordinator's default,
    # sized for boards that settle well after the ACK (washer course).
    write_settle_s: float | None = None


def unpolled(*groups: Iterable[Capability]) -> list[Capability]:
    """Coverage-only capabilities a flat board reads once, at discovery:
    re-reading them every sweep would cost a GET each for nothing."""
    return [
        replace(cap, poll_tier="never") if cap.poll_tier == "cold" else cap
        for group in groups
        for cap in group
    ]


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
