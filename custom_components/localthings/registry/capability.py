"""A Capability binds one OCF resource href to the entities it produces."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .entities import SamsungEntityDescription


@dataclass(frozen=True, kw_only=True)
class Capability:
    href: str | None = None
    entities: tuple[SamsungEntityDescription, ...] = ()
    # 'hot'/'warm' get their own sub-poll GETs between summary polls; 'cold'
    # rides the /device/0 batch. 'probe' is for an href no batch carries
    # (issue #301's /file/transfer/vs/0 is absent from all 83 batch fixtures
    # on record), so discovery can never see it to tier it -- the coordinator
    # reads registry.PROBE_HREFS directly instead.
    poll_tier: str = "cold"  # 'hot' | 'warm' | 'cold' | 'probe'
    rt_filter: str | None = None  # bind only if rt_filter in rep.get('rt', ())
    href_prefix: str | None = None  # pattern caps only: bind only if href starts with this
    strip_prefix_in_key: bool = False  # strip href_prefix segs before building key_override
    # Rep field holding this instance's device-given name (e.g. an ice
    # maker's "CUBED_ICE"/"ICE_BITES"), normalized and used as the display
    # name prefix in place of the href-derived instance label. Does not
    # affect key_override/unique_id -- only what's shown in the UI.
    name_field: str | None = None
    match_fn: Callable[[dict, dict], bool] | None = None  # match_fn(rep, resources) -> bool
    # Rare optional hook — only operational-state-style resources use this.
    on_observation: Callable[[dict, dict], None] | None = None
    project: Callable[[dict, dict], dict] | None = None
    # For a subdevice whose identity resource reports another model (#676's
    # hood slot reports the cooktop's board): its resources -> its own
    # model, or None when it doesn't apply.
    model_fn: Callable[[dict], str | None] | None = None
