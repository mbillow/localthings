"""Samsung soundbar capabilities (oic.d.networkaudio), captured on an HW-S61B.

Polled per href (registry/flat.py). Unless noted, writes are inferred from
the resource's oic.if.a interface and representation.
"""

from ..capability import Capability
from ..entities import (
    BinarySensorDesc,
    ButtonDesc,
    NumberDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
)


def _write(field: str, encode=lambda p: p):
    """Write `field` on the entity's own href, `encode` turning Home
    Assistant's value into the wire's. No href, no write."""
    return lambda p, rep, href=None: (
        ([s for s in href.strip("/").split("/") if s], {field: encode(p)}) if href else None
    )


def _supported(rep: dict, resources: dict) -> bool:
    """0 means "no such control on this unit"."""
    return rep.get("x.com.samsung.networkaudio.isSupported") == 1


def _network_info(rep: dict) -> dict:
    return rep.get("x.com.samsung.networkaudio.networkInfo") or {}


def _number(p) -> int:
    return round(float(p))


def _is_on(p) -> bool:
    return p == "On"


def _int_on(p) -> int:
    return 1 if p == "On" else 0


def _on_off(p) -> str:
    return "on" if p == "On" else "off"


# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

# Ranges from the SmartThings lifestyleaudio plugin; the woofer's -12..+6
# matches the unit's own option list.

TONE = Capability(
    href="/sec/networkaudio/tone",
    # Warm so changes made in the app show up.
    poll_tier="warm",
    entities=(
        NumberDesc(
            key="bass",
            field="x.com.samsung.networkaudio.bass",
            native_min=-6,
            native_max=6,
            step=1,
            entity_category="config",
            write_fn=_write("x.com.samsung.networkaudio.bass", _number),
        ),
        NumberDesc(
            key="treble",
            field="x.com.samsung.networkaudio.treble",
            native_min=-6,
            native_max=6,
            step=1,
            entity_category="config",
            write_fn=_write("x.com.samsung.networkaudio.treble", _number),
        ),
    ),
)

WOOFER = Capability(
    href="/sec/networkaudio/woofer",
    poll_tier="warm",
    entities=(
        NumberDesc(
            key="woofer_level",
            field="x.com.samsung.networkaudio.woofer",
            # Only with a paired sub, as the plugin gates its woofer page.
            exists_fn=lambda rep, resources: (
                rep.get("x.com.samsung.networkaudio.connection") == "on"
            ),
            native_min=-12,
            native_max=6,
            step=1,
            entity_category="config",
            write_fn=_write("x.com.samsung.networkaudio.woofer", _number),
        ),
        BinarySensorDesc(
            key="woofer_connection",
            field="x.com.samsung.networkaudio.connection",
            value_fn=lambda v: v == "on",
            entity_category="diagnostic",
        ),
    ),
)

AUDIO_SYNC = Capability(
    href="/sec/networkaudio/audiosync",
    poll_tier="warm",
    entities=(
        NumberDesc(
            key="audio_sync",
            field="x.com.samsung.networkaudio.audiosync",
            unit="ms",
            native_min=0,
            native_max=300,
            step=1,
            entity_category="config",
            exists_fn=_supported,
            write_fn=_write("x.com.samsung.networkaudio.audiosync", _number),
        ),
    ),
)


def _eq_band_rep(band: int):
    def _rep(rep: dict):
        bands = rep.get("x.com.samsung.networkaudio.EQband") or []
        try:
            return int(bands[band])
        except (IndexError, TypeError, ValueError):
            return None

    return _rep


def _eq_band_write(band: int):
    def _write(p, rep, href=None):
        # All 7 bands with EQname 'NONE', as the plugin sends (EQBandAction.SET_VALUE).
        bands = list(rep.get("x.com.samsung.networkaudio.EQband") or ["0"] * 7)
        bands[band] = str(_number(p))
        return (
            ["sec", "networkaudio", "eq"],
            {
                "x.com.samsung.networkaudio.EQname": "NONE",
                "x.com.samsung.networkaudio.EQband": bands,
                "x.com.samsung.networkaudio.action": "setEQvalue",
            },
        )

    return _write


def _eq_band_exists(rep: dict, resources: dict) -> bool:
    bands = rep.get("x.com.samsung.networkaudio.EQband")
    return isinstance(bands, list) and len(bands) == 7


EQ = Capability(
    href="/sec/networkaudio/eq",
    # Warm like TONE.
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="equalizer",
            field="x.com.samsung.networkaudio.EQname",
            options_field="x.com.samsung.networkaudio.supportedList",
            entity_category="config",
            # The plugin sends action 'setEQmode' with a preset.
            write_fn=lambda p, rep, href=None: (
                ["sec", "networkaudio", "eq"],
                {
                    "x.com.samsung.networkaudio.EQname": p,
                    "x.com.samsung.networkaudio.action": "setEQmode",
                },
            ),
        ),
        # Integer strings on the wire (plugin's EQBandRange). Frequency labels
        # are in the translation catalogs.
        *(
            NumberDesc(
                key=f"eq_band_{band + 1}",
                rep_fn=_eq_band_rep(band),
                native_min=-6,
                native_max=6,
                step=1,
                entity_category="config",
                exists_fn=_eq_band_exists,
                write_fn=_eq_band_write(band),
            )
            for band in range(7)
        ),
    ),
)


# Per-channel trims in one list resource. The plugin posts only the changed
# row, never the whole list (ChannelLevelD2SDataSource).
_CH_LEVELS: tuple[tuple[str, str], ...] = (
    ("Spk_Center", "channel_level_center"),
    ("Spk_Side", "channel_level_side"),
    ("Spk_Wide", "channel_level_wide"),
    ("Spk_Front_Top", "channel_level_front_top"),
    ("Spk_Rear", "channel_level_rear"),
    ("Spk_Rear_Top", "channel_level_rear_top"),
    ("Spk_Rear_Side", "channel_level_rear_side"),
)

_CH_FIELD = "x.com.samsung.networkaudio.channelVolume"


def _ch_present(name: str):
    def _present(rep: dict, resources: dict) -> bool:
        if not _supported(rep, resources):
            return False
        # Plugin's ChannelSupport: -1 not supported, 0 disabled (e.g. a
        # sleeping rear pair), 1 enabled. Only -1 hides the entity.
        return any(
            item.get("name") == name and item.get("status") != -1
            for item in rep.get(_CH_FIELD) or []
            if isinstance(item, dict)
        )

    return _present


def _ch_value(name: str):
    def _rep(rep: dict):
        for item in rep.get(_CH_FIELD) or []:
            if isinstance(item, dict) and item.get("name") == name:
                return item.get("value")
        return None

    return _rep


def _ch_write(name: str):
    def _write(p, rep, href=None):
        return (
            ["sec", "networkaudio", "channelVolume"],
            {_CH_FIELD: [{"name": name, "value": _number(p)}]},
        )

    return _write


# A rear unit is present when its channelVolume row has status 1.
# surroundspeaker reports isSupport 0 even with a rear pair connected.
REAR_FAMILY = ("Spk_Rear", "Spk_Rear_Top", "Spk_Rear_Side")

CHANNEL_LEVEL = Capability(
    href="/sec/networkaudio/channelVolume",
    rt_filter="x.com.samsung.networkaudio.channelVolume",
    poll_tier="warm",
    entities=(
        *(
            NumberDesc(
                key=key,
                rep_fn=_ch_value(name),
                # -6..+6 confirmed by the unit's own slider bounds.
                native_min=-6,
                native_max=6,
                step=1,
                entity_category="config",
                exists_fn=_ch_present(name),
                write_fn=_ch_write(name),
            )
            for name, key in _CH_LEVELS
        ),
        BinarySensorDesc(
            key="rear_speakers",
            rep_fn=lambda rep: any(
                item.get("name") in REAR_FAMILY and item.get("status") == 1
                for item in rep.get(_CH_FIELD) or []
                if isinstance(item, dict)
            ),
            entity_category="diagnostic",
        ),
    ),
)

ADVANCED_AUDIO = Capability(
    href="/sec/networkaudio/advancedaudio",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="night_mode",
            field="x.com.samsung.networkaudio.nightmode",
            value_fn=lambda v: v == 1,
            entity_category="config",
            write_fn=_write("x.com.samsung.networkaudio.nightmode", _int_on),
        ),
        SwitchDesc(
            key="voice_amplifier",
            field="x.com.samsung.networkaudio.voiceamplifier",
            value_fn=lambda v: v == 1,
            entity_category="config",
            write_fn=_write("x.com.samsung.networkaudio.voiceamplifier", _int_on),
        ),
        # -1 means not applicable in the current sound mode.
        SwitchDesc(
            key="bass_boost",
            field="x.com.samsung.networkaudio.bassboost",
            value_fn=lambda v: v == 1,
            entity_category="config",
            exists_fn=lambda rep, resources: rep.get("x.com.samsung.networkaudio.bassboost") != -1,
            write_fn=_write("x.com.samsung.networkaudio.bassboost", _int_on),
        ),
    ),
)

ACTIVE_VOICE_AMPLIFIER = Capability(
    href="/sec/networkaudio/activeVoiceAmplifier",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="active_voice_amplifier",
            field="x.com.samsung.networkaudio.activeVoiceAmplifier",
            value_fn=lambda v: v == 1,
            entity_category="config",
            write_fn=_write("x.com.samsung.networkaudio.activeVoiceAmplifier", _int_on),
        ),
    ),
)

SPACEFIT_SOUND = Capability(
    href="/sec/networkaudio/spacefitSound",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="spacefit_sound",
            field="x.com.samsung.networkaudio.spacefitSound",
            value_fn=lambda v: v == 1,
            entity_category="config",
            write_fn=_write("x.com.samsung.networkaudio.spacefitSound", _int_on),
        ),
    ),
)

AUTO_POWER_DOWN = Capability(
    href="/sec/networkaudio/autoPowerDown",
    # Warm, as are all writable settings: a write the bar ignores is put
    # right by the next sub-poll rather than the next full sweep.
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="auto_power_down",
            field="x.com.samsung.networkaudio.autoPowerDown",
            value_fn=lambda v: bool(v),
            entity_category="config",
            write_fn=_write("x.com.samsung.networkaudio.autoPowerDown", _is_on),
        ),
    ),
)

# "on"/"off" strings, not a bool.
AUTO_UPDATE = Capability(
    href="/sec/networkaudio/autoUpdate",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="auto_update",
            field="x.com.samsung.networkaudio.autoUpdate",
            value_fn=lambda v: v == "on",
            entity_category="config",
            write_fn=_write("x.com.samsung.networkaudio.autoUpdate", _on_off),
        ),
    ),
)

# Q-Symphony: play the paired TV's speakers together with the bar.
SYMPHONY = Capability(
    href="/sec/networkaudio/symphony",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="q_symphony",
            field="x.com.samsung.networkaudio.symphony",
            value_fn=lambda v: v == "on",
            entity_category="config",
            exists_fn=_supported,
            write_fn=_write("x.com.samsung.networkaudio.symphony", _on_off),
        ),
    ),
)

AUDIOPROMPT = Capability(
    href="/sec/networkaudio/audioPrompt",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="audio_prompt",
            field="x.com.samsung.networkaudio.audioPrompt",
            value_fn=lambda v: bool(v),
            entity_category="config",
            write_fn=_write("x.com.samsung.networkaudio.audioPrompt", _is_on),
        ),
        SelectDesc(
            key="audio_prompt_language",
            field="x.com.samsung.networkaudio.language",
            options_field="x.com.samsung.networkaudio.supportedList",
            entity_category="config",
            write_fn=_write("x.com.samsung.networkaudio.language"),
        ),
    ),
)

PASS_THROUGH = Capability(
    href="/sec/networkaudio/passThrough",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="pass_through",
            field="x.com.samsung.networkaudio.passThruEnable",
            value_fn=lambda v: bool(v),
            entity_category="config",
            exists_fn=_supported,
            write_fn=_write("x.com.samsung.networkaudio.passThruEnable", _is_on),
        ),
    ),
)

# ------------------------------------------------------------------
# Diagnostics
# ------------------------------------------------------------------

# The button starts pairing as the plugin's setBTPairingMode does; the
# binary sensor shows the state.
BT_PAIRING = Capability(
    href="/sec/networkaudio/btPairingMode",
    # Warm so pairing state shows up promptly.
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="bluetooth_pairing",
            field="x.com.samsung.networkaudio.pairingMode",
            value_fn=lambda v: bool(v),
            entity_category="diagnostic",
        ),
        ButtonDesc(
            key="pair_bluetooth",
            payload=True,
            entity_category="config",
            write_fn=_write("x.com.samsung.networkaudio.pairingMode"),
        ),
    ),
)

CURRENT_EQ_MODE = Capability(
    href="/sec/networkaudio/currentEQMode",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="current_eq_mode",
            field="x.com.samsung.networkaudio.currentEQMode",
            entity_category="diagnostic",
        ),
    ),
)

INSTALLATION_TYPE = Capability(
    href="/sec/networkaudio/installationType",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="installation_type",
            field="x.com.samsung.networkaudio.installationType",
            entity_category="diagnostic",
        ),
    ),
)

SOUND_FROM = Capability(
    href="/sec/networkaudio/soundFrom",
    poll_tier="hot",
    entities=(
        SensorDesc(
            key="sound_from",
            field="x.com.samsung.networkaudio.name",
            entity_category="diagnostic",
            extra_state_attributes_fn=lambda rep, resources: {
                "connection_type": (rep.get("x.com.samsung.networkaudio.soundFrom") or {}).get(
                    "connectionType"
                )
            },
        ),
    ),
)

SPEAKER_STATUS = Capability(
    href="/sec/networkaudio/speakerStatus",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="speaker_status",
            field="x.com.samsung.networkaudio.speakerStatus",
            entity_category="diagnostic",
        ),
    ),
)

VERSION_INFO = Capability(
    href="/sec/networkaudio/versionInfo",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="micom_version",
            field="x.com.samsung.networkaudio.micomVersion",
            entity_category="diagnostic",
            extra_state_attributes_fn=lambda rep, resources: {
                "dial_version": rep.get("x.com.samsung.networkaudio.dialVersion")
            },
        ),
    ),
)


def _wifi_signal_pct(rep: dict):
    rssi = _network_info(rep).get("rssi")
    if not isinstance(rssi, (int, float)) or rssi < 0:
        return None
    return round(min(rssi, 4) / 4 * 100)


NETWORK_INFO = Capability(
    href="/sec/networkaudio/networkInfo",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="wifi_signal",
            # The 0..4 bar index as a percent; the raw index is an attribute.
            unit="%",
            rep_fn=_wifi_signal_pct,
            entity_category="diagnostic",
            extra_state_attributes_fn=lambda rep, resources: {
                "raw_index": _network_info(rep).get("rssi"),
                "channel": _network_info(rep).get("ch"),
                "ip_address": _network_info(rep).get("ip"),
            },
        ),
    ),
)


# Nothing worth an entity; bound to keep the coverage repair quiet.
COVERAGE: list[Capability] = [
    # Power, volume, input, sound mode and playback state: a media player's.
    Capability(href="/sec/networkaudio/switch/binary"),
    Capability(href="/sec/networkaudio/audio"),
    Capability(href="/sec/networkaudio/mode"),
    Capability(href="/sec/networkaudio/soundmode"),
    Capability(href="/sec/networkaudio/playback"),
    # Firmware update status; only "noupdate" has been seen.
    Capability(href="/sec/networkaudio/swUpdate"),
    # Empty on every capture so far.
    Capability(href="/sec/networkaudio/musicinfo"),
    # AutoEQ calibration counters with no known vocabulary.
    Capability(href="/sec/networkaudio/autoeq"),
    # Static identity strings (MACs) -- HA connections carry the same.
    Capability(href="/sec/networkaudio/deviceinfo"),
    # Static capability bitmap per input type, not state.
    Capability(href="/sec/networkaudio/feature"),
    # Connection history, not state.
    Capability(href="/sec/networkaudio/lastConnections"),
    # Static "has a mic" flag.
    Capability(href="/sec/networkaudio/mic"),
    # Rear-speaker steering (isSupport says whether the bar has it).
    Capability(href="/sec/networkaudio/surroundspeaker"),
    # Static "has a VFD" flag.
    Capability(href="/sec/networkaudio/vfd"),
    # Relative volume steps, command-only.
    Capability(href="/sec/networkaudio/volumeUpDown"),
]
