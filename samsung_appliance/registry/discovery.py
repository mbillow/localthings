"""Runtime discovery: device resources -> bound entities.

A device advertises a set of OCF resources keyed by href. For every href
present in the registry, emit the capability's entities bound to that resource.
Unknown hrefs are a coverage gap, logged at debug and skipped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

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


def discover(resources: dict[str, dict],
             registry: dict[str, 'Capability'],
             log: Optional[Callable[[str], None]] = None) -> list[BoundEntity]:
    out: list[BoundEntity] = []
    for href, rep in resources.items():
        if not isinstance(rep, dict):
            continue
        cap = registry.get(href)
        if cap is None:
            if log is not None:
                log(f"unknown resource {href}")
            continue
        inst = instance_suffix(href)
        for desc in cap.entities:
            out.append(BoundEntity(href=href, capability=cap,
                                   desc=desc, instance=inst))
    return out
