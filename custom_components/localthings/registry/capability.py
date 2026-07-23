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
    # Rep field holding this instance's device-given name (e.g. an ice
    # maker's "CUBED_ICE"/"ICE_BITES"), normalized and used as the display
    # name prefix in place of the href-derived instance label. Does not
    # affect key_override/unique_id -- only what's shown in the UI.
    name_field: Optional[str] = None
    match_fn: Optional[Callable[[dict, dict], bool]] = None  # match_fn(rep, resources) -> bool
    # Rare optional hook — only operational-state-style resources use this.
    on_observation: Optional[Callable[[dict, dict], None]] = None
    project: Optional[Callable[[dict, dict], dict]] = None
