"""Samsung Tizen TV-platform capabilities (`/sec/tv/*`).

Captured on two M7 Smart Monitors (LS32DM702UKXKR, LS43DM703UKXKR, issue
#815). Their `/sec/tv/*` reps have the same shape as the QE50QN90A TV's in
#755. Power, volume, input and sound mode are bound without entities, for
the TV media player to own, as on the soundbar (#606).
"""

from collections import Counter

from ..capability import Capability
from ..entities import SelectDesc, SensorDesc
from . import av

PREFIX = "x.com.samsung.tv."
CURRENT_MODE = PREFIX + "currentMode"
SUPPORTED_MODES = PREFIX + "supportedModes"

HREF_SWITCH = "/sec/tv/switch/binary"
HREF_AUDIO = "/sec/tv/audio"
HREF_INPUT = "/sec/tv/inputsource"
HREF_PICTURE = "/sec/tv/mode/picture"
HREF_SOUND = "/sec/tv/mode/sound"


def mode_options(rep: dict) -> dict[str, str]:
    """`{id: label}` from a mode resource's `supportedModes` rows. Labels are
    the device's own, in its UI language; two inputs both named "PC" (HDMI1
    and USB-C on the LS32DM702) are told apart by their id (from #755)."""
    rows = rep.get(SUPPORTED_MODES)
    if not isinstance(rows, list):
        return {}
    options = {
        row[PREFIX + "id"]: row[PREFIX + "name"]
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get(PREFIX + "id"), str)
        and row[PREFIX + "id"]
        and isinstance(row.get(PREFIX + "name"), str)
        and row[PREFIX + "name"]
    }
    counts = Counter(options.values())
    labels = {
        code: f"{name} ({code})" if counts[name] > 1 else name for code, name in options.items()
    }
    # A device label could itself look like a disambiguated one; fall back
    # to the ids rather than let two options share a label.
    return labels if len(set(labels.values())) == len(labels) else {code: code for code in options}


def _writable(rep: dict) -> bool:
    return "oic.if.a" in (rep.get("if") or ())


def _mode_write(value, rep, href=None):
    if not href or not _writable(rep) or value not in mode_options(rep):
        return None
    return av.path_of(href), {CURRENT_MODE: value}


def _mode_select(href: str, key: str, icon: str) -> SelectDesc:
    return SelectDesc(
        key=key,
        field=CURRENT_MODE,
        icon=icon,
        entity_category="config",
        needs_remote_control=False,
        # The QN90A answers a refused mode write with result=false in a 2.04
        # (#755).
        control_response=True,
        options=lambda resources: list(mode_options(resources.get(href) or {})),
        display_fn=lambda raw, resources: mode_options(resources.get(href) or {}).get(raw),
        exists_fn=lambda rep, resources: _writable(rep) and bool(mode_options(rep)),
        # Mode lists can collapse to an empty row (the LS43DM703's input list
        # on the tuner), so a monitor idle at startup must not lose it for good.
        dynamic_presence=True,
        write_fn=_mode_write,
    )


# The write was confirmed on the QN90A (#755), not yet on a monitor.
PICTURE_MODE = Capability(
    href=HREF_PICTURE,
    poll_tier="warm",
    entities=(_mode_select(HREF_PICTURE, "picture_mode", "mdi:palette"),),
)

# The media player's surface. Unpolled until it exists: no entity reads them.
MEDIA_SURFACE_COVERAGE: list[Capability] = [
    Capability(href=HREF_SWITCH, rt_filter="oic.r.switch.binary", poll_tier="never"),
    Capability(href=HREF_AUDIO, rt_filter="oic.r.audio", poll_tier="never"),
    Capability(href=HREF_INPUT, poll_tier="never"),
    Capability(href=HREF_SOUND, poll_tier="never"),
    # What is on screen: the input or app, and the broadcast channel.
    Capability(href="/sec/tv/contentinfo", poll_tier="never"),
    Capability(href="/sec/tv/channel", poll_tier="never"),
    # The speaker list (internal, or a connected soundbar).
    Capability(href="/sec/tv/mediaOutput", poll_tier="never"),
    # App and casting launchers.
    Capability(href="/sec/tv/launchapp", poll_tier="never"),
    Capability(href="/content/renderer", poll_tier="never"),
]

# The field is named lux; the two monitors read 118 and 602 at different
# times of day.
AMBIENT_LIGHT = Capability(
    href="/light/sensor/brightness/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="illuminance",
            field="brightness.lux",
            device_class="illuminance",
            state_class="measurement",
            unit="lx",
        ),
    ),
)

COVERAGE: list[Capability] = [
    # The same input list as /sec/tv/inputsource, and its OCF twin.
    Capability(href="/sec/tv/inputsourcelist"),
    Capability(href="/sec/tv/mediaInput"),
    # Sound recognition and camera motion. /sensor/state/vs/0 reports both
    # off on these monitors, so their readings are not yet known to be live.
    Capability(href="/sound/detection/vs/0"),
    Capability(href="/sensor/state/vs/0"),
    Capability(href="/capability/motion/vs/0"),
    Capability(href="/sec/tv/camera/context"),
    Capability(href="/sec/tv/camera/status"),
    # Static model, category and feature flags.
    Capability(href="/sec/tv/deviceinfo"),
    Capability(href="/sec/tv/category/monitor"),
    Capability(href="/sec/tv/category/tv"),
    Capability(href="/sec/tv/support/features"),
    Capability(href="/sec/tv/onsupport"),
    Capability(href="/sec/tv/tapview"),
    Capability(href="/sec/tv/virtualgamepad/info"),
    Capability(href="/capability/custom.ocfResourceVersion/main/0"),
    # Remote, keyboard, menu and search plumbing for the SmartThings app.
    Capability(href="/sec/tv/virtualremote"),
    Capability(href="/sec/tv/ime"),
    Capability(href="/sec/tv/uicontrol"),
    Capability(href="/sec/tv/optioncontrol"),
    Capability(href="/sec/tv/showmenu"),
    Capability(href="/sec/tv/searchall"),
    Capability(href="/sec/tv/searchonweb"),
    Capability(href="/sec/tv/imagecapture"),
    Capability(href="/sec/tv/display/orientation"),
    Capability(href="/irblasterRemoteControl"),
    Capability(href="/irblasterRemoteDevice"),
    # Ambient mode, lighting automations and the built-in SmartThings hub.
    Capability(href="/sec/tv/ambient/info"),
    Capability(href="/sec/tv/automation/light/control"),
    Capability(href="/sec/tv/light/control"),
    Capability(href="/sec/tv/iotHub"),
    Capability(href="/childdevices/lifecycle"),
    # The current app's thumbnail, a base64 PNG.
    Capability(href="/sec/tv/contentinfo/thumbnail"),
    # Cloud, provider and update plumbing.
    Capability(href="/sec/tv/msoinfo"),
    Capability(href="/sec/tv/thingstatus"),
    Capability(href="/sec/tv/connectioncontrol/info"),
    Capability(href="/sec/tv/tvonwithmobile/enable"),
    Capability(href="/sec/tv/plugin/first_launch"),
    Capability(href="/sec/tv/networkinfo"),
    Capability(href="/calmconnection/command"),
    Capability(href="/capability/bixby/main/0"),
    Capability(href="/capability/bixbyCommand/main/0"),
    Capability(href="/capability/sec.diagnosticsInformation/main/0"),
    Capability(href="/capability/sec.wifiConfiguration/main/0"),
    # Find-node location reporting.
    Capability(href="/capability/samsungim.findNodeGeolocation/main/0"),
    Capability(href="/capability/samsungim.fixedFindNode/main/0"),
    # Log capture and upload.
    Capability(href="/sec/tv/logTrigger"),
    Capability(href="/sec/tv/logcapture"),
    Capability(href="/devicelog/command"),
    Capability(href="/devicelog/connectioninfo"),
    Capability(href="/devicelog/dump"),
]

# TV-to-phone message mailboxes. ssoservice's holds the Samsung account list
# and remote-server's a remote token (#815), so registry/flat.py never reads
# them; bound for captures that carry them.
APPDATA_PREFIX = "/sec/tv/appdata/"
APPDATA = Capability(href_prefix=APPDATA_PREFIX, poll_tier="never")
