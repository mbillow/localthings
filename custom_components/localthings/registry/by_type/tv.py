"""Samsung OCF TVs (oic.d.tv, #755): the Smart Monitor's Tizen surface plus
the shared media player (#834)."""

from ..capabilities import tv
from ._base import DeviceRegistry, _build
from .monitor import REGISTRY as MONITOR_REGISTRY
from .monitor import TIZEN_BASE

REGISTRY = DeviceRegistry(
    name="tv",
    capabilities=_build([*TIZEN_BASE, tv.MEDIA_PLAYER, *tv.TV_MEDIA_SURFACE]),
    pattern_capabilities=MONITOR_REGISTRY.pattern_capabilities,
    subscribe_tiers=MONITOR_REGISTRY.subscribe_tiers,
)
