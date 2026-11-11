"""Samsung Smart Monitor registry (x.com.st.d.monitor, issue #815).

A flat Tizen TV board (registry/flat.py) with no /information/vs/0, so it
routes on /oic/d alone.
"""

from ..capabilities import av, common, tv
from ._base import DeviceRegistry, _build, unpolled

SMART_MONITOR = DeviceRegistry(
    name="smart_monitor",
    capabilities=_build(
        [
            *unpolled(av.IGNORED, tv.COVERAGE),
            av.NETWORK,
            *tv.MEDIA_SURFACE_COVERAGE,
            tv.PICTURE_MODE,
            tv.AMBIENT_LIGHT,
            # PROBE_HREFS are read on every appliance (issue #301).
            common.FILE_LIST,
            common.FILE_TRANSFER,
        ]
    ),
    pattern_capabilities=[tv.APPDATA],
    # As on the soundbar: OBSERVE has not been shown to leave the
    # SmartThings app's session alone.
    subscribe_tiers=(),
    write_settle_s=6.0,
)
