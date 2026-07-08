"""Runtime discovery: device resources -> bound entities.

A device advertises a set of OCF resources keyed by href. For every href
present in the registry, emit the capability's entities bound to that resource.
Unknown hrefs are a coverage gap; each one is passed to the optional `log`
callback (as the raw href, not a formatted message) and otherwise skipped.
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


def instance_suffix(href: str) -> str:
    """'' for the index-0 instance, else '_<n>' from the trailing segment."""
    tail = href.rstrip('/').rsplit('/', 1)[-1]
    if tail.isdigit() and tail != '0':
        return f'_{tail}'
    return ''


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
            for desc in cap.entities:
                out.append(BoundEntity(href=href, capability=cap,
                                       desc=desc, instance=inst))
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
            for desc in cap.entities:
                key_override = '_'.join(segs) + '_' + desc.key
                out.append(BoundEntity(href=href, capability=cap, desc=desc,
                                       instance=inst, key_override=key_override))
            matched = True
            break

        if not matched and log is not None:
            log(href)

    return out
