"""Samsung Tizen TV-platform capabilities (`/sec/tv/*`).

Captured on two M7 Smart Monitors (LS32DM702UKXKR, LS43DM703UKXKR, issue
#815). Their `/sec/tv/*` reps have the same shape as the QE50QN90A TV's in
#755. Power, volume, input and sound mode are bound without entities on the
monitor; the TV's media player owns them (MEDIA_PLAYER below).

The TV section was captured on a 2020 Tizen 5.5 TV (UE50TU7172). Its write
contracts follow the SmartThings TV plugin and the device's own reps; mode
writes carry the mode id, never the localized name.
"""

import json
import re
from collections import Counter
from math import isfinite
from urllib.parse import parse_qs, urlsplit

from ... import tvapps
from ..capability import Capability
from ..entities import (
    ButtonDesc,
    MediaModeSurface,
    MediaPlayerDesc,
    NotifyDesc,
    RemoteDesc,
    SelectDesc,
    SensorDesc,
)
from ..identity import OCF_RESOURCE_VERSION_HREF as HREF_FIRMWARE
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
_MEDIA_OTHER: list[Capability] = [
    # What is on screen: the input or app, and the broadcast channel.
    Capability(href="/sec/tv/contentinfo", poll_tier="never"),
    Capability(href="/sec/tv/channel", poll_tier="never"),
    # The speaker list (internal, or a connected soundbar).
    Capability(href="/sec/tv/mediaOutput", poll_tier="never"),
    # App and casting launchers.
    Capability(href="/sec/tv/launchapp", poll_tier="never"),
    Capability(href="/content/renderer", poll_tier="never"),
]
MEDIA_SURFACE_COVERAGE: list[Capability] = [
    Capability(href=HREF_SWITCH, rt_filter="oic.r.switch.binary", poll_tier="never"),
    Capability(href=HREF_AUDIO, rt_filter="oic.r.audio", poll_tier="never"),
    Capability(href=HREF_INPUT, poll_tier="never"),
    Capability(href=HREF_SOUND, poll_tier="never"),
    *_MEDIA_OTHER,
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
    Capability(href="/sec/tv/category/frametv"),
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
    # Empty on the UN55DU7200 (#895): the Q-Symphony flag carries no state,
    # and the TV reports no power. Bind common.ENERGY_METER if one ever does.
    Capability(href="/sec/tv/qsymphony"),
    Capability(href="/energy/consumption/vs/0"),
    # Utility demand response, unsupported locally like /drlc/vs/0 (#895).
    Capability(href="/energy/demandResponseLoadControl/vs/0"),
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

# The light sensor's own SmartThings device, nested under a per-unit UUID
# (ViewFinity S9, #891): a second copy of /light/sensor/brightness/vs/0 and
# its parent category. Matched by rt, since the prefix differs on every unit.
SENSOR_CHILD = [
    Capability(rt_filter="x.com.samsung.vd.ligntsensor.brightness", poll_tier="never"),
    Capability(rt_filter="x.com.samsung.vd.sensor.parent.category", poll_tier="never"),
]


# ---------------------------------------------------------------------------
# TV (oic.d.tv)
# ---------------------------------------------------------------------------

HREF_CHANNEL = "/sec/tv/channel"
HREF_CONTENTINFO = "/sec/tv/contentinfo"
HREF_THUMBNAIL = "/sec/tv/contentinfo/thumbnail"
HREF_VIDEO_TRACK = "/capability/videoMetaData/main/0"
CHANNEL = Capability(
    href=HREF_CHANNEL,
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="channel",
            # The number when the provider sends no name.
            rep_fn=lambda rep: (
                rep.get("x.com.samsung.tv.channelName") or rep.get("x.com.samsung.tv.channelNumber")
            ),
            extra_state_attributes_fn=lambda rep, resources: {
                "channel_number": rep.get("x.com.samsung.tv.channelNumber"),
                "channel_name": rep.get("x.com.samsung.tv.channelName"),
            },
        ),
    ),
)

# What is on right now: live broadcast title or the current app's name,
# with schedule times when they exist.
CONTENT_INFO = Capability(
    href=HREF_CONTENTINFO,
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="now_showing",
            field="x.com.samsung.tv.title",
            extra_state_attributes_fn=lambda rep, resources: {
                "input": rep.get("x.com.samsung.tv.inputname"),
                "subtitle": rep.get("x.com.samsung.tv.subtitle"),
                "channel_number": rep.get("x.com.samsung.tv.number"),
                "start_time": rep.get("x.com.samsung.tv.starttime"),
                "end_time": rep.get("x.com.samsung.tv.endtime"),
                # Empty on plain DTV.
                **(
                    {"thumbnail_path": rep["x.com.samsung.tv.thumbnailpath"]}
                    if rep.get("x.com.samsung.tv.thumbnailpath")
                    else {}
                ),
            },
        ),
    ),
)

# Nothing reports a pending update, so no update entity. The build string
# becomes the device's firmware version at discovery (identity.resolve_firmware).
FIRMWARE_COVERAGE = Capability(
    href=HREF_FIRMWARE,
    rt_filter="x.com.st.r.custom.ocfResourceVersion",
)

# Remote keys, as the SmartThings TV plugin sends them: a tap posts keystatus
# "pressAndRelease"; holds post "press"/"release" pairs. /sec/tv/uicontrol
# refuses keys; /sec/tv/remotecontrol takes them but never answers a GET.
# So the entity is anchored on uicontrol, which answers, and writes go to
# remotecontrol (anchored there, the remote would never be discovered).

_REMOTE_PATH = ["sec", "tv", "remotecontrol"]


KEY_TAP = "pressAndRelease"
KEY_PRESS = "press"
KEY_RELEASE = "release"


def _remote_write(p, rep, href=None):
    """A KEY_* name taps the key; (KEY_*, status) presses or releases it."""
    key, status = p if isinstance(p, tuple) else (p, KEY_TAP)
    return _REMOTE_PATH, {
        "x.com.samsung.tv.keyvalue": key,
        "x.com.samsung.tv.keystatus": status,
    }


# showmenu opens a menu.json page with empty targetMenuParam, otherwise sets
# its value; targetMenuName is required but unused. GET never answers.
# pictureOff "on" needs a key press to undo ("off" does nothing).
# sleeptimer takes minutes; "0" disables it.
HREF_SHOW_MENU = "/sec/tv/showmenu"
HREF_NATIVE_APP = "/sec/tv/launchnativeapp"
SHOW_MENU_PATH = ["sec", "tv", "showmenu"]
SLEEP_TIMER_ID = "sleeptimer"


def show_menu_body(menu_id: str, param: str = "", name: str | None = None) -> dict:
    """Open a settings page (param empty) or set a setting's value."""
    return {
        "x.com.samsung.tv.targetMenuId": menu_id,
        "x.com.samsung.tv.targetMenuName": name or menu_id,
        "x.com.samsung.tv.targetMenuParam": param,
    }


def sleep_timer_body(minutes: int) -> dict:
    """The sleep timer, as the SmartThings app sets it; 0 turns it off."""
    return show_menu_body(SLEEP_TIMER_ID, str(minutes), "Sleep Timer")


def _screen_off_write(p, rep, href=None):
    return SHOW_MENU_PATH, show_menu_body("pictureOff", "on")


# Advertised but not answering a GET while a QE85QN95A is on, so each costs
# discovery a read timeout; no entity reads them (has_href uses /oic/res).
UNREAD_HREFS = frozenset({"/sec/tv/remotecontrol", HREF_NATIVE_APP, "/autoreconnect/ap"})

# Accepts KEY_* names; remote.py adds friendly aliases. The notify entity and
# the screen-off button ride the same anchor: their resources
# (HREF_NATIVE_APP, HREF_SHOW_MENU) never answer a GET.
REMOTE = Capability(
    href="/sec/tv/uicontrol",
    poll_tier="cold",
    entities=(
        RemoteDesc(key="remote", write_fn=_remote_write),
        NotifyDesc(key="notification", requires_href=HREF_NATIVE_APP),
        ButtonDesc(
            key="screen_off",
            icon="mdi:monitor-off",
            write_fn=_screen_off_write,
            requires_href=HREF_SHOW_MENU,
            write_only=True,
        ),
    ),
)

# A one-way "enter Ambient Mode" command: it works from live TV and from Art
# Mode, can't power the TV on, and its rep holds no state (The Frame 2021,
# #868). KEY_EXIT on the remote leaves Ambient Mode.
HREF_AMBIENT = "/sec/tv/switch/ambient"
AMBIENT_MODE = Capability(
    href=HREF_AMBIENT,
    poll_tier="never",
    entities=(
        ButtonDesc(
            key="ambient_mode",
            icon="mdi:image-frame",
            write_fn=lambda p, rep, href=None: (
                ["sec", "tv", "switch", "ambient"],
                {"x.com.samsung.tv.requestor": "user"},
            ),
            write_only=True,
        ),
    ),
)

# Notifications: launchnativeapp hands the TV's notification service app an
# "ocf_noti" bundle holding a JSON payload. A notification needs a registered
# service and a non-empty title and message; Home Assistant registers its own
# service, so turning SmartThings' notifications off on the TV doesn't
# silence it.
NATIVE_APP_PATH = ["sec", "tv", "launchnativeapp"]
_NOTIFY_APP = "com.samsung.tv.notification-serviceapp"
NOTIFY_SERVICE_ID = "io.home-assistant.localthings"
NOTIFY_DEFAULT_TITLE = "Home Assistant"


def _notify_bundle(payload: dict) -> dict:
    return {
        "x.com.samsung.tv.targetAppId": _NOTIFY_APP,
        "x.com.samsung.tv.bundleParam": [
            {
                "x.com.samsung.tv.bundle.key": "ocf_noti",
                "x.com.samsung.tv.bundle.value": json.dumps(payload, separators=(",", ":")),
            }
        ],
    }


def notify_register_body() -> dict:
    """Register Home Assistant's notification service, on by default."""
    return _notify_bundle(
        {
            "x.com.samsung.data": {
                "x.com.samsung.tv.message_type": "action",
                "x.com.samsung.tv.action": {
                    "action_type": "service_register",
                    "service_info": {
                        "service_name": NOTIFY_DEFAULT_TITLE,
                        "service_id": NOTIFY_SERVICE_ID,
                        "service_default_option": True,
                        "service_show_popup": True,
                        "service_popup_name": NOTIFY_DEFAULT_TITLE,
                        "service_account_option": False,
                    },
                },
            }
        }
    )


def notify_body(seq: int, title: str, message: str) -> dict:
    """A pop-up notification with an OK button; seq makes it a new one."""
    return _notify_bundle(
        {
            "x.org.iotivity.ns.messageid": seq,
            "x.org.iotivity.ns.providerid": "home-assistant",
            "x.org.iotivity.ns.type": 1,
            "x.com.samsung.data": {
                "x.com.samsung.tv.message_type": "notification",
                "x.com.samsung.tv.notification": {
                    "service_info": {"service_id": NOTIFY_SERVICE_ID},
                    "message_id": f"ha_{seq}",
                    "layout_type": "basic",
                    "type": "app",
                    "title": title,
                    "message": message,
                    "hide_timeout": "10",
                    "delete_timeout": "100",
                    "buttons": [{"text_sid": "COM_SID_OK"}],
                },
            },
        }
    )


# Search on the TV, as the plugin's searchOnWeb: YouTube takes the query
# alone (and opens on the results); the other sites also carry the plugin's
# own search URL.
HREF_SEARCH = "/sec/tv/searchonweb"
SEARCH_SITES: dict[str, str | None] = {
    "youtube": None,
    "bing": "https://www.bing.com/search?q=",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search=",
    "naver": "https://search.naver.com/search.naver?query=",
    "daum": "http://search.daum.net/search?nil_suggest=btn&w=tot&DA=SBC&q=",
}


def search_body(site: str, query: str) -> dict:
    body = {"x.com.samsung.tv.searchSite": site, "x.com.samsung.tv.searchQuery": query}
    if (url := SEARCH_SITES[site]) is not None:
        body["x.com.samsung.tv.searchUrl"] = url
    return body


# The renderer routes image/* to Gallery, YouTube ids to its app, and video/*
# (including HLS) to the player; other types produce an empty notice.
# Its prompt uses sender userName/iconUrl and requires the TV's own remote.
# URL formats and latency: docs/investigations/tv-casting-latency.md.
HREF_RENDERER = "/content/renderer"
GALLERY_APP_ID = "com.samsung.tv.gallery"


def gallery_body(kind: str, url: str) -> dict:
    """Launch Gallery on an image or video URL directly, with no prompt.

    It is what "Watch now" on the renderer's prompt does."""
    return {
        "x.com.samsung.tv.targetAppId": GALLERY_APP_ID,
        "x.com.samsung.tv.bundleParam": [
            {
                "x.com.samsung.tv.bundle.key": f"smart_thing_uri_{kind}0",
                "x.com.samsung.tv.bundle.value": url,
            }
        ],
    }


_RENDERER_SENDER = {
    "deviceName": "Home Assistant",
    "userName": "Home Assistant",
    "userId": "home-assistant",
}
_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_YOUTUBE_PATH_PREFIXES = ("/shorts/", "/embed/", "/live/", "/v/")


def youtube_id(value: str) -> str | None:
    """The video id in a YouTube link (watch, youtu.be, shorts, embed,
    live, v) or a bare 11-character id; None for anything else."""
    value = value.strip()
    if _YOUTUBE_ID.fullmatch(value):
        return value
    parts = urlsplit(value if "://" in value else f"https://{value}")
    host = (parts.hostname or "").lower()
    candidate = None
    if host == "youtu.be" or host.endswith(".youtu.be"):
        candidate = parts.path.lstrip("/").split("/")[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if parts.path.rstrip("/") == "/watch":
            candidate = (parse_qs(parts.query).get("v") or [""])[0]
        else:
            for prefix in _YOUTUBE_PATH_PREFIXES:
                if parts.path.startswith(prefix):
                    candidate = parts.path[len(prefix) :].split("/")[0]
    return candidate if candidate and _YOUTUBE_ID.fullmatch(candidate) else None


def _renderer_body(host: str, path: str, mimetype: str) -> dict:
    return {
        "x.com.samsung.action": "play",
        "x.com.samsung.contents": [
            {
                "x.com.samsung.host": host,
                "x.com.samsung.path": path,
                "x.com.samsung.mimetype": mimetype,
            }
        ],
        "x.com.samsung.provider.user": dict(_RENDERER_SENDER),
    }


def with_sender(body: dict, title: str | None = None, icon_url: str | None = None) -> dict:
    """The renderer body with the prompt's title and icon replaced."""
    sender = dict(body["x.com.samsung.provider.user"])
    if title:
        sender["userName"] = title
    if icon_url:
        sender["iconUrl"] = icon_url
    return {**body, "x.com.samsung.provider.user": sender}


def youtube_body(video_id: str) -> dict:
    return _renderer_body("https://www.youtube.com", f"/{video_id}", "video/youtube")


HREF_LAUNCH_APP = "/sec/tv/launchapp"
LAUNCH_APP_PATH = ["sec", "tv", "launchapp"]
YOUTUBE_APP_ID = "111299001912"


def youtube_launch_body(video_id: str) -> dict:
    """Start a YouTube video directly, with no prompt.

    It is what "Watch now" on the renderer's prompt does for a YouTube item."""
    return {
        "x.com.samsung.tv.targetAppId": YOUTUBE_APP_ID,
        "x.com.samsung.tv.targetAppParam": f"#play?v={video_id}",
    }


def _file_body(url: str, mimetype: str) -> dict | None:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return _renderer_body(f"{parts.scheme}://{parts.netloc}", path, mimetype)


def image_body(url: str) -> dict | None:
    """The renderer body for an http(s) image URL; None for anything else.

    The renderer only advertises image/jpeg; the type just routes the item
    to Gallery, which reads the file itself."""
    return _file_body(url, "image/jpeg")


def video_body(url: str, mimetype: str = "video/mp4") -> dict | None:
    """The renderer body for an http(s) video file URL; None for anything else."""
    return _file_body(url, mimetype if mimetype.startswith("video/") else "video/mp4")


# Launch a built-in screen (supportedNativeApps: home, guide, ...) or an app
# the TV has been seen running, by name; the current option is the app in
# front. Contracts and measurements in tvapps.py.
OPEN_APP = Capability(
    href=tvapps.LAUNCH_HREF,
    poll_tier="cold",
    entities=(
        SelectDesc(
            key="open_app",
            affects_playback=True,
            rep_fn=tvapps.current_name,
            options=lambda resources: tvapps.app_names(resources.get(tvapps.LAUNCH_HREF) or {}),
            write_fn=lambda p, rep, href=None: tvapps.launch_write(p, rep),
        ),
    ),
)

# Which app is casting to the TV. Read-only; session control is the cast protocol's.
CAST_SESSION = Capability(
    href="/sec/cast/remote/session",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="cast_session",
            field="sessionStatus",
            entity_category="diagnostic",
            extra_state_attributes_fn=lambda rep, resources: {
                "sender_app": rep.get("senderAppId") or None,
                "receiver_app": rep.get("receiverAppId") or None,
            },
        ),
    ),
)

CAST_ITEM = Capability(
    href="/sec/cast/remote/item/status",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="cast_item",
            field="itemStatus",
            entity_category="diagnostic",
            extra_state_attributes_fn=lambda rep, resources: {
                "position": rep.get("position"),
                "duration": rep.get("duration"),
            },
        ),
    ),
)

# Current content's artwork (base64 PNG), the media player's fallback picture.
THUMBNAIL = Capability(
    href=HREF_THUMBNAIL,
    rt_filter="x.com.samsung.tv.contentinfo.thumbnail",
    poll_tier="cold",
)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


# A channel number as the TV takes it: '5', or a subchannel such as '5-1'.
CHANNEL_NUMBER = re.compile(r"\d+(?:[-.]\d+)?")

# Playback commands and the SmartThings media resource that takes each.
_PLAYBACK_COMMANDS = (
    ("playback", av.HREF_MEDIA_PLAYBACK),
    ("seek", av.HREF_MEDIA_PLAYBACK),
    ("next_track", av.HREF_TRACK_CONTROL),
    ("previous_track", av.HREF_TRACK_CONTROL),
    ("shuffle", av.HREF_SHUFFLE),
)


def _supported_commands(resources: dict) -> frozenset[str]:
    """Commands whose resource the TV reports writable. Never power_on: the
    OCF endpoint is gone in standby, so turn-on is Wake-on-LAN, plus a
    switch write while the TV wakes in network standby (av.py)."""
    commands = set()
    power = resources.get(HREF_SWITCH) or {}
    audio = resources.get(HREF_AUDIO) or {}
    if _writable(power) and isinstance(power.get("value"), bool):
        commands.add("power_off")
    if _writable(audio):
        if _number(audio.get("volume")):
            commands.add("volume")
        if isinstance(audio.get("mute"), bool):
            commands.add("mute")
    for command, href in (("source", HREF_INPUT), ("sound_mode", HREF_SOUND)):
        rep = resources.get(href) or {}
        if _writable(rep) and mode_options(rep):
            commands.add(command)
    for command, href in (
        *_PLAYBACK_COMMANDS,
        ("app", tvapps.LAUNCH_HREF),
        ("channel", HREF_CHANNEL),
    ):
        if _writable(resources.get(href) or {}):
            commands.add(command)
    return frozenset(commands)


def _validate_media(payload, rep, resources) -> str | None:
    kind, value = payload
    command = ("power_on" if value else "power_off") if kind == "power" else kind
    if command not in _supported_commands(resources):
        return "media_option_not_supported"
    if kind in ("source", "sound_mode"):
        href = HREF_INPUT if kind == "source" else HREF_SOUND
        if not isinstance(value, str) or value not in mode_options(resources.get(href) or {}):
            return "media_option_not_supported"
    if kind == "volume" and (not _number(value) or not 0 <= value <= 100):
        return "media_option_not_supported"
    if kind in ("power", "mute", "shuffle") and not isinstance(value, bool):
        return "media_option_not_supported"
    # media_player.py names the bad value first; this keeps a direct write honest.
    if kind == "app" and not (isinstance(value, str) and value.strip()):
        return "media_option_not_supported"
    if kind == "channel" and not (isinstance(value, str) and CHANNEL_NUMBER.fullmatch(value)):
        return "media_option_not_supported"
    return None


def _media_write(payload, rep, href=None, resources=None):
    """(kind, value) pairs from the media_player platform -> (path, body).
    Source and sound mode arrive as mode ids (MediaModeSurface.options_fn)."""
    resources = resources or {}
    if _validate_media(payload, rep, resources) is not None:
        return None
    kind, value = payload
    if kind == "power":
        return av.path_of(HREF_SWITCH), {"value": value}
    if kind == "volume":
        return av.path_of(HREF_AUDIO), {"volume": round(value)}
    if kind == "mute":
        return av.path_of(HREF_AUDIO), {"mute": value}
    if kind in ("source", "sound_mode"):
        mode_href = HREF_INPUT if kind == "source" else HREF_SOUND
        return av.path_of(mode_href), {CURRENT_MODE: value}
    if kind == "app":
        # An app id, or the name of one the TV has been seen running.
        return tvapps.launch_write(value, resources.get(tvapps.LAUNCH_HREF) or {})
    if kind == "channel":
        # Measured: the TV tunes and answers controlResponse {result: true};
        # the name follows on the next read.
        return av.path_of(HREF_CHANNEL), {PREFIX + "channelNumber": value}
    return av.media_write(kind, value)


def _mode_surface(href: str) -> MediaModeSurface:
    return MediaModeSurface(
        href=href, field=CURRENT_MODE, options_field=SUPPORTED_MODES, options_fn=mode_options
    )


# Bound to the power switch, which every TV state answers: SmartThings'
# media capabilities come and go with the TV's own media service (4.04 and
# absent from /oic/res for a while after power-on).
MEDIA_PLAYER = Capability(
    href=HREF_SWITCH,
    rt_filter="oic.r.switch.binary",
    poll_tier="hot",
    entities=(
        MediaPlayerDesc(
            key="media_player",
            rep_fn=lambda rep: "on" if rep.get("value") else "off",
            device_class="tv",
            power_href=HREF_SWITCH,
            audio_href=HREF_AUDIO,
            source=_mode_surface(HREF_INPUT),
            sound_mode=_mode_surface(HREF_SOUND),
            playback_href=av.HREF_MEDIA_PLAYBACK,
            track_href=HREF_VIDEO_TRACK,
            fallback_track_href=av.HREF_TRACK_DATA,
            shuffle_href=av.HREF_SHUFFLE,
            track_control_href=av.HREF_TRACK_CONTROL,
            channel_href=HREF_CHANNEL,
            content_info_href=HREF_CONTENTINFO,
            thumbnail_href=HREF_THUMBNAIL,
            play_media=True,
            wake_on_lan=True,
            resend_unsafe_fn=av.resend_unsafe,
            cache_after_ack_fn=av.cache_after_ack,
            command_fn=av.is_skip,
            supported_commands_fn=_supported_commands,
            needs_remote_control=False,
            control_response=True,
            # A refusal passes only when a fresh GET already shows the
            # requested fields: an already-current sound mode is the refusal
            # seen (QN90A, #834). A relative command (seek, skip) can't match,
            # so its refusal still fails.
            confirm_on_refusal=True,
            write_fn=_media_write,
            validate_fn=_validate_media,
        ),
    ),
)

# The TV's own capabilities on hrefs the monitor binds for coverage
# (by_type/tv.py swaps them in with _base.replacing).
TV_REPLACES: list[Capability] = [
    MEDIA_PLAYER,
    # The media player's feeds, polled because it reads them.
    Capability(href=HREF_AUDIO, rt_filter="oic.r.audio", poll_tier="hot"),
    Capability(href=HREF_INPUT, poll_tier="warm"),
    Capability(href=HREF_SOUND, poll_tier="warm"),
    CHANNEL,
    CONTENT_INFO,
    THUMBNAIL,
    FIRMWARE_COVERAGE,
    REMOTE,
    OPEN_APP,
]

# Hrefs only a TV has served so far.
TV_ONLY: list[Capability] = [
    Capability(href=av.HREF_MEDIA_PLAYBACK, rt_filter="x.com.st.mediaplayer", poll_tier="hot"),
    Capability(href=HREF_VIDEO_TRACK, poll_tier="hot"),
    CAST_SESSION,
    CAST_ITEM,
    AMBIENT_MODE,
]

TV_COVERAGE: list[Capability] = [
    # Relative volume; /sec/tv/audio has the absolute 0..100.
    Capability(href="/capability/audioVolume/main/0"),
    # Cast control channel (the session/item state is CAST_SESSION/CAST_ITEM).
    Capability(href="/sec/cast/remote/control"),
    Capability(href="/content/continuity/renderer"),
    # Network self-repair state; answered only in standby so far.
    Capability(href="/autoreconnect/ap"),
]
