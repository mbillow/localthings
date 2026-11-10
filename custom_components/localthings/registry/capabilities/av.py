"""Shared pieces for Samsung AV boards.

Resources with the same href on every AV board seen so far: onboarding,
provisioning and network. SmartThings' media resources come in two layouts,
see MediaHrefs.
"""

from dataclasses import dataclass

from ..capability import Capability
from ..entities import SensorDesc


@dataclass(frozen=True)
class MediaHrefs:
    """Where a board serves SmartThings' media resources. The reps are the
    same in both layouts."""

    playback: str
    track_data: str
    shuffle: str
    track_control: str
    repeat: str


def _media_hrefs(pattern: str) -> MediaHrefs:
    return MediaHrefs(
        playback=pattern.format("mediaPlayback"),
        track_data=pattern.format("audioTrackData"),
        shuffle=pattern.format("mediaPlaybackShuffle"),
        track_control=pattern.format("mediaTrackControl"),
        repeat=pattern.format("mediaPlaybackRepeat"),
    )


# HW-S61B and HW-Q950T.
CAPABILITY_MEDIA = _media_hrefs("/capability/{}/main/0")
# HW-Q700C (#796).
NETWORKAUDIO_MEDIA = _media_hrefs("/sec/networkaudio/av/{}")
MEDIA_LAYOUTS = (CAPABILITY_MEDIA, NETWORKAUDIO_MEDIA)

HREF_MEDIA_PLAYBACK = CAPABILITY_MEDIA.playback
HREF_TRACK_DATA = CAPABILITY_MEDIA.track_data
HREF_SHUFFLE = CAPABILITY_MEDIA.shuffle
HREF_TRACK_CONTROL = CAPABILITY_MEDIA.track_control


def path_of(href: str) -> list[str]:
    return [str(s) for s in href.strip("/").split("/")]


# No entities of their own: a family's MediaPlayerDesc names them, and
# media_player.py reads their reps.
MEDIA_RESOURCES: list[Capability] = [
    cap
    for layout in MEDIA_LAYOUTS
    for cap in (
        Capability(href=layout.track_data, poll_tier="hot"),
        Capability(href=layout.shuffle, poll_tier="warm"),
        Capability(href=layout.track_control, poll_tier="warm"),
        # No repeat write has been seen to take effect (HW-Q950T, #719).
        Capability(href=layout.repeat, poll_tier="never"),
    )
]


def first_mode(rep: dict):
    modes = rep.get("modes")
    return (modes[0] if modes else None) if isinstance(modes, (list, tuple)) else modes


def media_write(
    kind: str, value, hrefs: MediaHrefs = CAPABILITY_MEDIA
) -> tuple[list[str], dict] | None:
    """The media commands, the same on every AV board seen so far."""
    if kind == "playback":
        # playpause resumes BT/AirPlay where a bare play may do nothing.
        return path_of(hrefs.playback), {"modes": [value]}
    if kind == "shuffle":
        return path_of(hrefs.shuffle), {"mode": "enabled" if value else "disabled"}
    if kind in ("next_track", "previous_track"):
        return path_of(hrefs.track_control), {
            "modes": ["next" if kind == "next_track" else "previous"]
        }
    if kind == "seek":
        # A relative offset in milliseconds, clamped by the device to the
        # track (HW-Q950T, #719).
        return path_of(hrefs.playback), {"modes": ["seek"], "position": int(value)}
    return None


def resend_unsafe(payload) -> bool:
    """Commands that act relative to the current state, so a resend after a
    lost reply could apply them twice."""
    return isinstance(payload, tuple) and (
        payload[0] in ("seek", "next_track", "previous_track")
        or payload == ("playback", "playpause")
    )


def is_skip(payload) -> bool:
    # Next/previous change the track, which the track data reports; their
    # own resource has no state to show for them.
    return isinstance(payload, tuple) and payload[0] in ("next_track", "previous_track")


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
