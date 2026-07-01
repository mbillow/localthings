"""A Capability binds one OCF resource href to the entities it produces."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .entities import SamsungEntityDescription


@dataclass(frozen=True, kw_only=True)
class Capability:
    href: Optional[str] = None
    entities: tuple[SamsungEntityDescription, ...] = ()
    poll_tier: str = 'cold'                  # 'hot' | 'warm' | 'cold'
    rt_filter: Optional[str] = None          # bind only if rt_filter in rep.get('rt', ())
    href_prefix: Optional[str] = None        # pattern caps only: bind only if href starts with this
    strip_prefix_in_key: bool = False         # strip href_prefix segs before building key_override
    match_fn: Optional[Callable[[dict, dict], bool]] = None  # match_fn(rep, resources) -> bool
    # Rare optional hooks — only operational-state-style resources use these.
    active_when: Optional[Callable[[dict], bool]] = None
    on_observation: Optional[Callable[[dict, dict], None]] = None
    project: Optional[Callable[[dict, dict], dict]] = None
