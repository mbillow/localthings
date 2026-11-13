"""Samsung TV registry (oic.d.tv, #755).

The Smart Monitor's Tizen surface (by_type/monitor.py), with the TV's own
capabilities in place of the hrefs it binds there for coverage: the media
player, remote, channel, apps and casting.
"""

from ..capabilities import av, tv
from ._base import DeviceRegistry, _build, replacing, unpolled
from .monitor import REGISTRY as MONITOR_REGISTRY
from .monitor import TIZEN_BASE

REGISTRY = DeviceRegistry(
    name="tv",
    capabilities=_build(
        [
            *replacing([*TIZEN_BASE, *tv.MEDIA_SURFACE_COVERAGE], tv.TV_REPLACES),
            *tv.TV_ONLY,
            *unpolled(tv.TV_COVERAGE, av.MEDIA_RESOURCES),
        ]
    ),
    pattern_capabilities=MONITOR_REGISTRY.pattern_capabilities,
    subscribe_tiers=MONITOR_REGISTRY.subscribe_tiers,
    write_settle_s=6.0,
    # In network standby the TV wakes every few minutes and answers most
    # reads, but not its power switch or remote.
    awake_href=tv.HREF_SWITCH,
)
