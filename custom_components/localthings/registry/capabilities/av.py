"""Shared pieces for Samsung AV boards.

Resources with the same href on every AV board seen so far: onboarding,
provisioning, network and the /capability/* media resources.
"""

from ..capability import Capability
from ..entities import SensorDesc


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
