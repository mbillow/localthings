"""Runtime discovery: device resources -> bound entities.

A device advertises a set of OCF resources keyed by href. For every href
present in the registry, emit the capability's entities bound to that resource.
Unknown hrefs are a coverage gap; each one is passed to the optional `log`
callback (as the raw href, not a formatted message) and otherwise skipped.

An href with a registered capability whose rt_filter/match_fn declines for
this particular device (e.g. a filter capability sitting on hardware that
doesn't have that filter) is *not* a gap — a maintainer already looked at
that href and decided how to handle it. Only hrefs absent from the registry
entirely are reported.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from .capability import Capability
from .entities import SamsungEntityDescription


@dataclass
class BoundEntity:
    href: str
    capability: Capability
    desc: SamsungEntityDescription
    instance: str = ''
    key_override: Optional[str] = None
    instance_name: Optional[str] = None


def _snake_to_title(s: str) -> str:
    """'CUBED_ICE'/'cubed_ice' -> 'Cubed Ice'. Shared with entity.py's
    _derive_name, which applies the same transform to a state key."""
    return s.replace('_', ' ').title()


def _instance_name(cap: Capability, rep: dict) -> Optional[str]:
    """Normalize `cap.name_field`'s raw value ("CUBED_ICE" -> "Cubed Ice")
    for use as a display-name prefix, or None if the cap doesn't declare
    one or the device didn't report it."""
    if not cap.name_field:
        return None
    raw = rep.get(cap.name_field)
    if not isinstance(raw, str) or not raw:
        return None
    return _snake_to_title(raw)


def instance_suffix(href: str) -> str:
    """'' for the index-0 instance, else '_<n>' from the trailing segment."""
    tail = href.rstrip('/').rsplit('/', 1)[-1]
    if tail.isdigit() and tail != '0':
        return f'_{tail}'
    return ''


def _bind(cap: Capability, href: str, inst: str, inst_name: Optional[str],
          key_prefix: Optional[str] = None) -> list[BoundEntity]:
    """Build one BoundEntity per entity on `cap`, sharing the instance/
    key-prefix/instance-name computed once by the caller."""
    return [
        BoundEntity(href=href, capability=cap, desc=desc, instance=inst,
                    key_override=f'{key_prefix}_{desc.key}' if key_prefix else None,
                    instance_name=inst_name)
        for desc in cap.entities
    ]


def discover(
    resources: dict[str, dict],
    registry: dict[str, list[Capability]],
    pattern_caps: Iterable[Capability] = (),
    log: Optional[Callable[[str], None]] = None,
) -> list[BoundEntity]:
    out: list[BoundEntity] = []

    for href, rep in resources.items():
        if not isinstance(rep, dict):
            continue
        rts = rep.get('rt') or ()
        caps = registry.get(href) or []
        matched = False

        for cap in caps:
            if cap.rt_filter is not None and cap.rt_filter not in rts:
                continue
            if cap.match_fn is not None and not cap.match_fn(rep, resources):
                continue
            inst = instance_suffix(href)
            out.extend(_bind(cap, href, inst, _instance_name(cap, rep)))
            matched = True

        if matched:
            continue

        # Pattern cap fallback — first matching pattern wins
        for cap in pattern_caps:
            if cap.href_prefix and not href.startswith(cap.href_prefix):
                continue
            if cap.rt_filter is not None and cap.rt_filter not in rts:
                continue
            if cap.match_fn is not None and not cap.match_fn(rep, resources):
                continue
            inst = instance_suffix(href)
            # Auto-derive key prefix from href segments (skip digits and 'vs')
            src = href[len(cap.href_prefix):] if (cap.strip_prefix_in_key and cap.href_prefix) else href
            segs = [s for s in src.strip('/').split('/') if s and not s.isdigit() and s != 'vs']
            out.extend(_bind(cap, href, inst, _instance_name(cap, rep), '_'.join(segs)))
            matched = True
            break

        if not matched and not caps and log is not None:
            log(href)

    return out
