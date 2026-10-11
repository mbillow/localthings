"""Samsung soundbar device registry (oic.d.networkaudio).

A flat AV board (registry/flat.py) with no /information/vs/0, so it routes
on /oic/d alone.
"""

from ..capabilities import av, common, soundbar
from ..capability import Capability
from ._base import DeviceRegistry, _build, unpolled

REGISTRY = DeviceRegistry(
    name="soundbar",
    capabilities=_build(
        [
            *unpolled(
                av.IGNORED,
                av.MEDIA_COVERAGE,
                soundbar.COVERAGE,
                # Voice-assistant negotiation state (Alexa account linkage,
                # locale lists) -- cloud plumbing, not device state.
                [
                    Capability(href="/sec/networkaudio/vasupport"),
                    Capability(href="/sec/networkaudio/googlesupport"),
                ],
            ),
            av.NETWORK,
            soundbar.TONE,
            soundbar.WOOFER,
            soundbar.AUDIO_SYNC,
            soundbar.EQ,
            soundbar.CHANNEL_LEVEL,
            soundbar.ADVANCED_AUDIO,
            soundbar.ACTIVE_VOICE_AMPLIFIER,
            soundbar.SPACEFIT_SOUND,
            soundbar.AUTO_POWER_DOWN,
            soundbar.AUTO_UPDATE,
            soundbar.SYMPHONY,
            soundbar.AUDIOPROMPT,
            soundbar.PASS_THROUGH,
            soundbar.BT_PAIRING,
            soundbar.NETWORK_INFO,
            soundbar.CURRENT_EQ_MODE,
            soundbar.INSTALLATION_TYPE,
            soundbar.SOUND_FROM,
            soundbar.SPEAKER_STATUS,
            soundbar.VERSION_INFO,
            # PROBE_HREFS are read on every appliance whatever its type, so
            # every registry carries them (issue #301); both answer 4.04 here.
            common.FILE_LIST,
            common.FILE_TRANSFER,
        ]
    ),
    pattern_capabilities=[
        # Alexa sign-in/profile sub-tree: account linkage resources.
        Capability(href_prefix="/sec/alexa/", poll_tier="never"),
    ],
    flat=True,
    # No OBSERVE: subscriptions drop the SmartThings app's session to the
    # device until it is power-cycled.
    subscribe_tiers=(),
    # Writes read back on the very next GET, so the optimistic value only
    # has to outlast one warm sub-poll (6 s), not the default's allowance for
    # boards that settle long after the ACK.
    write_settle_s=6.0,
)
