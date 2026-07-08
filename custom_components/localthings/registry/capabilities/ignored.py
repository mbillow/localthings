"""Hrefs deliberately not modeled as capabilities.

Every entry here is either irrelevant plumbing (network/account/OTA
housekeeping that will never be home-automation-relevant) or a duplicate of
state we already expose through a friendlier href. Declaring them here,
rather than leaving them unregistered, keeps discover()'s unknown-resource
reporting limited to genuine coverage gaps.

Capability defaults entities=() and discover() treats any href that
resolves to a capability at all as matched, even one with no entities, so
this list uses the same declaration shape as a real capability with no
extra machinery. Fold into by_type registries (per-family gap detection)
and into the global ALL list (unknown-device-type fallback).

Deliberately exact hrefs only, no href_prefix/pattern capabilities: the
unknown-device-type fallback path (coordinator's `discover(resources,
CAPABILITIES)` call) doesn't pass pattern_caps, so a prefix-based entry
here would silently do nothing on that path. Enumerate each known href
instead; it's a short, stable list.

This list is maintainer-curated only; there is no per-installation
override. Grow it as real /device/0 dumps surface more universal noise —
do not add a href here on a guess. If a href's relevance is unclear, leave
it unbound so it surfaces as a gap for a human to look at.
"""
from ..capability import Capability

IGNORED: list[Capability] = [
    # Device serial/model is read directly by the coordinator for HA device
    # identity, not modeled as an entity capability.
    Capability(href='/information/vs/0'),

    # Bixby voice assistant: feature negotiation, account provisioning
    # (Samsung account email, access tokens), terms-of-service state, and
    # enable/disable status.
    Capability(href='/voice/feature/vs/0'),
    Capability(href='/voice/provisioning/vs/0'),
    Capability(href='/bixby/vs/0'),
    Capability(href='/bixby/status/vs/0'),
    Capability(href='/bixbyuservalidate/vs/0'),
    Capability(href='/bixbyterms/vs/0'),

    # Network/WiFi housekeeping — MAC addresses, supported auth/crypto
    # types, no controllable or observable appliance state.
    Capability(href='/wirelessinfo/vs/0'),
    Capability(href='/connectionconfig/vs/0'),

    # Static or internal-protocol metadata, not entity-worthy.
    Capability(href='/quickcontrol/info/vs/0'),
    Capability(href='/realtimenotiforclient/vs/0'),
    Capability(href='/file/information/vs/0'),
    Capability(href='/configuration/vs/0'),   # region/countryCode
    Capability(href='/setting/vs/0'),          # supported/selected UI language
    Capability(href='/timezone/vs/0'),         # redundant with HA's own timezone
    Capability(href='/wm/setinfo/vs/0'),       # model/manufacturing metadata

    # Demand Response Load Control — utility-company grid signals; requires
    # cloud registration with a utility program we don't support locally.
    Capability(href='/drlc/vs/0'),

    # Redundant with capabilities already declared elsewhere.
    # /speakersound/vs/0 duplicates /settings/sound/volume/vs/0 (laundry.SOUND_VOLUME).
    Capability(href='/speakersound/vs/0'),
    # /wm/editcourse/vs/0 is the raw encoded course-list byte string already
    # decoded and exposed via /course/vs/0 (dishwasher.CYCLE_OPTIONS).
    Capability(href='/wm/editcourse/vs/0'),

    # Bixby audio feedback (chime + volume played when Bixby starts/stops
    # listening) — only meaningful with Bixby enabled, which this
    # integration has no local path to configure or use.
    Capability(href='/sec/networkaudio/audio/vs/0'),

    # Static Bespoke-product-line flag, not appliance state.
    Capability(href='/bespoke/vs/0'),
    # Empty resource on every dump seen so far — nothing to expose.
    Capability(href='/defrost/prediction/vs/0'),
    # Seasonal defrost schedule (start/period/end per season). Automating
    # this cleanly would need a multi-field schedule editor; the practical
    # on/off control is fridge.DEFROST_DELAY.
    Capability(href='/defrost/reservation/vs/0'),
    # Warranty/service-plan enrollment status — every field reads "Unknown"
    # on hardware not enrolled in a Samsung Care+ style program.
    Capability(href='/dginformation/vs/0'),
    # AI energy-saving level; only one value ('1') is ever in
    # supportedAiLevel on hardware seen so far, so there's no real choice
    # to expose. Revisit if a device surfaces more than one supported level.
    Capability(href='/energy/ailevel/vs/0'),
    # Opaque integer with no supportedModes/options list to interpret it
    # against — meaning unclear from the raw resource alone.
    Capability(href='/runningmode/vs/0'),

    # Demand-response energy planner — same utility-program dependency as
    # /drlc/vs/0 above; every dump seen so far is inert (plan: 'none').
    Capability(href='/energy/planner/vs/0'),
    # Temperature-unit display preference, redundant with HA's own units.
    Capability(href='/wm/submode/vs/0'),
]
