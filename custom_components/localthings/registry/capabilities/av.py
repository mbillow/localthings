"""Shared pieces for Samsung AV boards.

Resources with the same href on every AV board seen so far: onboarding,
provisioning, network and the /capability/* media resources.
"""

from ..capability import Capability
from ..entities import SensorDesc

HREF_MEDIA_PLAYBACK = "/capability/mediaPlayback/main/0"
HREF_TRACK_DATA = "/capability/audioTrackData/main/0"
HREF_SHUFFLE = "/capability/mediaPlaybackShuffle/main/0"
HREF_TRACK_CONTROL = "/capability/mediaTrackControl/main/0"


def _path(href: str) -> list[str]:
    return [str(s) for s in href.strip("/").split("/")]


# SmartThings' generic media resources. No entities of their own: a family's
# MediaPlayerDesc names them, and media_player.py reads their reps.
MEDIA_RESOURCES: list[Capability] = [
    Capability(href=HREF_TRACK_DATA, poll_tier="hot"),
    Capability(href=HREF_SHUFFLE, poll_tier="warm"),
    Capability(href=HREF_TRACK_CONTROL, poll_tier="warm"),
    # No repeat write has been seen to take effect (HW-Q950T, #719).
    Capability(href="/capability/mediaPlaybackRepeat/main/0", poll_tier="never"),
]


def first_mode(rep: dict):
    modes = rep.get("modes")
    return (modes[0] if modes else None) if isinstance(modes, (list, tuple)) else modes


def media_write(kind: str, value) -> tuple[list[str], dict] | None:
    """The /capability/* commands, the same on every AV board seen so far."""
    if kind == "playback":
        # playpause resumes BT/AirPlay where a bare play may do nothing.
        return _path(HREF_MEDIA_PLAYBACK), {"modes": [value]}
    if kind == "shuffle":
        return _path(HREF_SHUFFLE), {"mode": "enabled" if value else "disabled"}
    if kind in ("next_track", "previous_track"):
        return _path(HREF_TRACK_CONTROL), {
            "modes": ["next" if kind == "next_track" else "previous"]
        }
    if kind == "seek":
        # A relative offset in milliseconds, clamped by the device to the
        # track (HW-Q950T, #719).
        return _path(HREF_MEDIA_PLAYBACK), {"modes": ["seek"], "position": int(value)}
    return None


def resend_unsafe(payload) -> bool:
    """Commands that act relative to the current state, so a resend after a
    lost reply could apply them twice."""
    return isinstance(payload, tuple) and (
        payload[0] in ("seek", "next_track", "previous_track")
        or payload == ("playback", "playpause")
    )


def cache_after_ack(payload) -> bool:
    # A failed relative seek must not become the next seek's baseline.
    return isinstance(payload, tuple) and payload[0] == "seek"


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
