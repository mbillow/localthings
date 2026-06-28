"""Runtime discovery: device resources -> bound entities.

A device advertises a set of OCF resources, each carrying one or more `rt`
values. For every rt present in the registry, emit the capability's entities
bound to that resource href. Unknown rt is a coverage gap, logged and skipped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .capability import Capability
from .entities import SamsungEntityDescription


@dataclass
class BoundEntity:
    href: str
    capability: Capability
    desc: SamsungEntityDescription
    instance: str = ''


def instance_suffix(href: str) -> str:
    """'' for the index-0 instance, else '_<n>' from the trailing segment."""
    tail = href.rstrip('/').rsplit('/', 1)[-1]
    if tail.isdigit() and tail != '0':
        return f'_{tail}'
    return ''


def discover(resources: dict[str, dict],
             registry: dict[str, Capability],
             log: Optional[Callable[[str], None]] = None) -> list[BoundEntity]:
    out: list[BoundEntity] = []
    for href, rep in resources.items():
        if not isinstance(rep, dict):
            continue
        rts = rep.get('rt') or []
        for rt in rts:
            cap = registry.get(rt)
            if cap is None:
                continue
            inst = instance_suffix(href)
            for desc in cap.entities:
                out.append(BoundEntity(href=href, capability=cap,
                                       desc=desc, instance=inst))
        # Coverage logging: a resource none of whose rts are known.
        if log is not None and not any(rt in registry for rt in rts):
            for rt in rts:
                log(f"unknown capability {rt} at {href}")
    return out
