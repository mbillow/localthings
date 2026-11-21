"""Samsung Smart Monitor registry (x.com.st.d.monitor, issue #815).

A flat Tizen TV board (registry/flat.py) with no /information/vs/0, so it
routes on /oic/d alone.
"""

from ..capabilities import av, common, tv
from ..capability import Capability
from ._base import DeviceRegistry, _build, unpolled

# Shared with by_type/tv.py, which binds its own media surface.
TIZEN_BASE: list[Capability] = [
    *unpolled(av.IGNORED, tv.COVERAGE),
    av.NETWORK,
    tv.PICTURE_MODE,
    tv.AMBIENT_LIGHT,
    # PROBE_HREFS are read on every appliance (issue #301).
    common.FILE_LIST,
    common.FILE_TRANSFER,
]

REGISTRY = DeviceRegistry(
    name="monitor",
    capabilities=_build([*TIZEN_BASE, *tv.MEDIA_SURFACE_COVERAGE]),
    pattern_capabilities=[tv.APPDATA, *tv.SENSOR_CHILD],
    # As on the soundbar: OBSERVE has not been shown to leave the
    # SmartThings app's session alone.
    subscribe_tiers=(),
)
