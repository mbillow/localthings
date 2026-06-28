"""A Capability binds one OCF resource type (rt) to the entities it produces."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .entities import SamsungEntityDescription


@dataclass(frozen=True, kw_only=True)
class Capability:
    rt: str
    entities: tuple[SamsungEntityDescription, ...]
    poll_tier: str = 'warm'                  # 'hot' | 'warm' | 'cold'
    observe: bool = True
    # Rare optional hooks — only operational-state-style resources use these.
    active_when: Optional[Callable[[dict], bool]] = None
    on_observation: Optional[Callable[[dict, dict], None]] = None
    project: Optional[Callable[[dict, dict], dict]] = None
