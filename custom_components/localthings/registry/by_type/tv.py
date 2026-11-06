"""Samsung TV settings; the media player and remote remain separate (#388)."""

from ..capabilities import common, tv
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="tv",
    capabilities=_build(
        [tv.INPUT, tv.PICTURE_MODE, tv.SOUND_MODE, common.FILE_LIST, common.FILE_TRANSFER]
    ),
    # Keep discovery read-only with respect to subscription state, as for
    # the soundbar. TV OBSERVE coexistence has not been validated.
    subscribe_tiers=(),
    write_settle_s=6.0,
)
