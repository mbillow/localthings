"""Samsung Smart Monitor registry (x.com.st.d.monitor, issue #815).

A flat Tizen TV board (registry/flat.py) with no /information/vs/0, so it
routes on /oic/d alone.
"""

from ..capabilities import av, common, tv
from ..capability import Capability
from ._base import DeviceRegistry, _build, replacing, unpolled

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

# A Smart Monitor is a tuner-less Samsung TV: it serves the same writable
# Tizen media surface (power, volume/mute, input source, sound mode, app
# launch and the content renderer), so it binds the same media player the
# TV does, swapping tv.TV_REPLACES in over the hrefs TIZEN_BASE only
# covered before (issue #936). Writes were confirmed live on an M70A
# (LS32AM702UUXEN): /sec/tv/audio and /sec/tv/mode/sound both answered a
# 2.04 with controlResponse result=true and held on re-read -- the
# confirmation the TV header noted was still missing on a monitor. Channel
# and broadcast content stay href-only coverage for the tuner the monitor
# lacks, exactly as on the TV.
REGISTRY = DeviceRegistry(
    name="monitor",
    capabilities=_build(
        [
            *replacing([*TIZEN_BASE, *tv.MEDIA_SURFACE_COVERAGE], tv.TV_REPLACES),
            *tv.TV_ONLY,
            *unpolled(tv.TV_COVERAGE, av.MEDIA_RESOURCES),
        ]
    ),
    pattern_capabilities=[tv.APPDATA, *tv.SENSOR_CHILD],
    # As on the soundbar: OBSERVE has not been shown to leave the
    # SmartThings app's session alone.
    subscribe_tiers=(),
    # The media service comes and goes with the panel; writes settle for six
    # seconds and the power switch answers in standby, as on the TV.
    write_settle_s=6.0,
    awake_href=tv.HREF_SWITCH,
)
