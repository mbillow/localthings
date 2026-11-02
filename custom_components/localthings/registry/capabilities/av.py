"""Shared pieces for Samsung AV boards.

Resources with the same href on every AV board seen so far: onboarding,
provisioning, network and the /capability/* media resources.
"""

from ..capability import Capability
from ..entities import SensorDesc

# Coverage and poll tier only; the media_player entity reads these reps.
MEDIA_TRACK_DATA = Capability(href="/capability/audioTrackData/main/0", poll_tier="hot")

MEDIA_SHUFFLE = Capability(href="/capability/mediaPlaybackShuffle/main/0")


def first_mode(rep: dict):
    modes = rep.get("modes")
    return (modes[0] if modes else None) if isinstance(modes, (list, tuple)) else modes


def media_write(kind: str, value) -> tuple[list[str], dict] | None:
    """Commands with identical wire formats on TV and soundbar boards."""
    if kind == "playback":
        # playpause resumes BT/AirPlay where a bare play may do nothing.
        return ["capability", "mediaPlayback", "main", "0"], {"modes": [value]}
    if kind == "shuffle":
        return ["capability", "mediaPlaybackShuffle", "main", "0"], {
            "mode": "enabled" if value else "disabled"
        }
    if kind in ("next_track", "previous_track"):
        # These are supported by the plugin even when absent from supportedModes.
        return ["capability", "mediaTrackControl", "main", "0"], {
            "modes": ["next" if kind == "next_track" else "previous"]
        }
    if kind == "seek":
        # Seek uses a delta in seconds, as the plugin's ±15 s buttons send.
        return ["capability", "mediaPlayback", "main", "0"], {
            "modes": ["seek"],
            "position": int(value),
        }
    return None


def _connection_kind(rep: dict):
    # tnn is "ethernet" on a wired unit and the SSID on WiFi (registry/flat.py
    # keeps only "wifi"); never expose the SSID.
    tnn = rep.get("tnn")
    if not tnn:
        return None
    return "ethernet" if str(tnn).lower() == "ethernet" else "wifi"


NETWORK = Capability(
    href="/WiFiConfResURI",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="network_connection",
            rep_fn=_connection_kind,
            device_class="enum",
            options=("ethernet", "wifi"),
            entity_category="diagnostic",
        ),
    ),
)

# SmartThings' media capabilities: playback, track data, shuffle, repeat and
# next/previous. No entity of their own yet.
MEDIA_COVERAGE: list[Capability] = [
    Capability(href=f"/capability/{name}/main/0")
    for name in (
        "mediaPlayback",
        "audioTrackData",
        "mediaPlaybackShuffle",
        "mediaPlaybackRepeat",
        "mediaTrackControl",
    )
]

# Onboarding resources that carry account tokens, a paired phone or nearby
# networks. Never read live (registry/flat.py); bound for captures that
# carry them.
ONBOARDING_HREFS = (
    "/CoapCloudConfResURI",
    "/DevConfResURI",
    "/EasySetupResURI",
    "/sec/accesspointlist",
    "/sec/provisioninginfo",
)

# Plumbing that nothing will automate.
IGNORED: list[Capability] = [
    *(Capability(href=href) for href in ONBOARDING_HREFS),
    Capability(href="/sec/languagelist"),
    # Samsung device-to-device/mirroring negotiation.
    Capability(href="/sec/mde"),
    Capability(href="/sec/mde/mirroring"),
    # Samsung Content Panel (art-store style frame content) support flags.
    Capability(href="/sec/contentPanel/support"),
    Capability(href="/sec/contentPanel/info"),
    # Phone-notification mirror (incoming calls/messages), not device state.
    Capability(href="/x.com.samsung/notification/receiver"),
]
