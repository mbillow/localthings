"""OCF TVs reuse the Tizen settings profile with the shared media player."""

from dataclasses import replace

from ..capabilities import tv
from ..capability import Capability
from ._base import _build
from .monitor import REGISTRY as MONITOR_REGISTRY

REGISTRY = replace(
    MONITOR_REGISTRY,
    name="tv",
    capabilities={
        **MONITOR_REGISTRY.capabilities,
        **_build(
            [
                tv.MEDIA_PLAYER,
                Capability(href=tv.HREF_AUDIO, rt_filter="oic.r.audio", poll_tier="hot"),
                Capability(href=tv.HREF_INPUT, poll_tier="warm"),
                Capability(href=tv.HREF_SOUND, poll_tier="warm"),
            ]
        ),
    },
)
