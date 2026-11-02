"""Media player platform for Local Things (AV boards).

One entity per soundbar or TV. It reads power, volume, source, sound mode,
playback and track data from sibling resources in the coordinator snapshot
and writes through the bound write_fn as (kind, value) pairs, like
climate.py. Per-family differences live in _PROFILES.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included
from .registry.entities import MediaPlayerDesc

_PLAYBACK_STATES = {
    "play": MediaPlayerState.PLAYING,
    "pause": MediaPlayerState.PAUSED,
    "stop": MediaPlayerState.IDLE,
    "buffering": MediaPlayerState.BUFFERING,
}

# The soundbar's own playback status. /capability/mediaPlayback can say
# 'stop' while an AirPlay stream plays.
_NA_PLAYBACK_STATES = {
    "PLAYING": MediaPlayerState.PLAYING,
    "PAUSED": MediaPlayerState.PAUSED,
    "BUFFERING": MediaPlayerState.BUFFERING,
    "IDLE": MediaPlayerState.IDLE,
    "STOPPED": MediaPlayerState.IDLE,
}


@dataclass(frozen=True)
class _ModeSurface:
    """Where a selectable mode (source, sound mode) lives and how to read it."""

    href: str
    current: Callable[[dict], str | None]
    options: Callable[[dict], list[str] | None]


@dataclass(frozen=True)
class _MediaProfile:
    device_class: MediaPlayerDeviceClass
    power_href: str
    audio_href: str
    source: _ModeSurface
    sound_mode: _ModeSurface
    playback_href: str
    shuffle_href: str
    # The first rep with a non-empty title wins.
    track_hrefs: tuple[str, ...]
    repeat_href: str | None = None
    next_previous: bool = False
    seek: bool = False
    channel_href: str | None = None
    # Base64 artwork, the entity picture fallback.
    thumbnail_href: str | None = None
    # Program title and schedule, for plain DTV where track data is empty.
    content_info_href: str | None = None
    # See _NA_PLAYBACK_STATES.
    na_playback_href: str | None = None


_PROFILES: dict[str, _MediaProfile] = {
    "soundbar": _MediaProfile(
        device_class=MediaPlayerDeviceClass.SPEAKER,
        power_href="/sec/networkaudio/switch/binary",
        audio_href="/sec/networkaudio/audio",
        source=_ModeSurface(
            "/sec/networkaudio/mode",
            current=lambda rep: rep.get("mode"),
            options=lambda rep: rep.get("supportedModes"),
        ),
        sound_mode=_ModeSurface(
            "/sec/networkaudio/soundmode",
            current=lambda rep: rep.get("x.com.samsung.networkaudio.soundmode"),
            options=lambda rep: rep.get("x.com.samsung.networkaudio.supportedSoundmode"),
        ),
        playback_href="/capability/mediaPlayback/main/0",
        shuffle_href="/capability/mediaPlaybackShuffle/main/0",
        track_hrefs=("/capability/audioTrackData/main/0",),
        # Not advertised, but the plugin posts next/previous all the same.
        next_previous=True,
        seek=True,
        na_playback_href="/sec/networkaudio/playback",
    ),
}

_REPEAT_MODES = {
    "off": RepeatMode.OFF,
    "one": RepeatMode.ONE,
    "all": RepeatMode.ALL,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    profile = _PROFILES.get(coordinator.device_type_name or "")
    if profile is None:
        return
    async_add_entities(
        LocalThingsMediaPlayer(coordinator, b, profile)
        for b in coordinator.bound
        if isinstance(b.desc, MediaPlayerDesc) and _is_included(b, coordinator)
    )


def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


class LocalThingsMediaPlayer(LocalThingsEntity, MediaPlayerEntity):
    """Composite media player entity for a Samsung AV board."""

    @property
    def media_content_type(self) -> MediaType:
        if self._profile.device_class == MediaPlayerDeviceClass.TV:
            return MediaType.CHANNEL if self._is_broadcast() else MediaType.VIDEO
        return MediaType.MUSIC

    def __init__(self, coordinator: LocalThingsCoordinator, bound, profile: _MediaProfile) -> None:
        super().__init__(coordinator, bound)
        self._profile = profile
        self._attr_device_class = profile.device_class
        self._attr_name = None  # this entity *is* the device
        # The device steps in integers 0..100.
        self._attr_volume_step = 0.01
        # See _handle_coordinator_update.
        self._position_updated_at: datetime | None = None
        self._last_position_evidence: tuple | None = None
        features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.SELECT_SOUND_MODE
            | MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.SHUFFLE_SET
        )
        if profile.repeat_href:
            features |= MediaPlayerEntityFeature.REPEAT_SET
        if profile.next_previous:
            features |= (
                MediaPlayerEntityFeature.NEXT_TRACK | MediaPlayerEntityFeature.PREVIOUS_TRACK
            )
        if profile.seek:
            features |= MediaPlayerEntityFeature.SEEK
        self._attr_supported_features = features

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Discovery has already populated the cache before listeners are attached.
        self._last_position_evidence = self._position_evidence()
        self._position_updated_at = dt_util.utcnow()

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        # Seeking needs the playback feed's position; live TV only has a schedule.
        features = self._attr_supported_features
        if self._playback_position_ms() is None:
            features &= ~MediaPlayerEntityFeature.SEEK
        return features

    # -- resource helpers ---------------------------------------------------

    def _rep(self, href: str) -> dict:
        return self.coordinator.resource(href)

    # -- state --------------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState | None:
        power = self._rep(self._profile.power_href)
        if "value" not in power:
            return None
        if power.get("value") is not True:
            return MediaPlayerState.OFF
        # Prefer the soundbar's own status (see _NA_PLAYBACK_STATES).
        if self._profile.na_playback_href is not None:
            status = self._rep(self._profile.na_playback_href).get(
                "x.com.samsung.networkaudio.status"
            )
            if isinstance(status, str) and status in _NA_PLAYBACK_STATES:
                mapped = _NA_PLAYBACK_STATES[status]
                if mapped is not MediaPlayerState.IDLE:
                    return mapped
                # During AirPlay the bar says STOPPED, so a track title plus
                # an active sender counts as playing. If the title isn't
                # cleared when the session ends, this stays PLAYING.
                sound_from = (
                    self._rep("/sec/networkaudio/soundFrom").get(
                        "x.com.samsung.networkaudio.soundFrom"
                    )
                    or {}
                )
                if self._track().get("title") and sound_from.get("status") == 1:
                    return MediaPlayerState.PLAYING
        mode = _first(self._rep(self._profile.playback_href).get("modes"))
        state = _PLAYBACK_STATES.get(str(mode), MediaPlayerState.ON)
        # The playback feed idles on live TV; the broadcast schedule still advances.
        if state in (MediaPlayerState.IDLE, MediaPlayerState.ON) and self._schedule():
            return MediaPlayerState.PLAYING
        # A TV on HDMI or its home screen shows something; "stop" only says
        # its own player is idle, so it is just on.
        tv = self._profile.device_class == MediaPlayerDeviceClass.TV
        return MediaPlayerState.ON if tv and state is MediaPlayerState.IDLE else state

    @property
    def volume_level(self) -> float | None:
        volume = self._rep(self._profile.audio_href).get("volume")
        if volume is None:
            return None
        try:
            return max(0.0, min(1.0, float(volume) / 100.0))
        except (TypeError, ValueError):
            return None

    @property
    def is_volume_muted(self) -> bool | None:
        mute = self._rep(self._profile.audio_href).get("mute")
        return None if mute is None else bool(mute)

    @property
    def source(self) -> str | None:
        # The bar's own report goes empty during BT playback; HA wants None.
        return self._profile.source.current(self._rep(self._profile.source.href)) or None

    @property
    def source_list(self) -> list[str] | None:
        return self._profile.source.options(self._rep(self._profile.source.href))

    @property
    def sound_mode(self) -> str | None:
        return self._profile.sound_mode.current(self._rep(self._profile.sound_mode.href))

    @property
    def sound_mode_list(self) -> list[str] | None:
        return self._profile.sound_mode.options(self._rep(self._profile.sound_mode.href))

    @property
    def media_channel(self) -> str | None:
        href = self._profile.channel_href
        if href is None or not self._is_broadcast():
            return None
        rep = self._rep(href)
        number = rep.get("x.com.samsung.tv.channelNumber")
        name = rep.get("x.com.samsung.tv.channelName")
        # '4 - CT 24 HD'; the bare name/number where only one exists.
        if number and name:
            return f"{number} - {name}"
        return name or number

    @property
    def shuffle(self) -> bool | None:
        mode = self._rep(self._profile.shuffle_href).get("mode")
        return None if mode is None else mode == "enabled"

    @property
    def repeat(self) -> RepeatMode | None:
        if self._profile.repeat_href is None:
            return None
        mode = _first(self._rep(self._profile.repeat_href).get("modes"))
        return _REPEAT_MODES.get(str(mode))

    # -- track metadata (empty strings while idle, so keyed off title) ------

    def _track(self) -> dict:
        for href in self._profile.track_hrefs:
            rep = self._rep(href)
            if rep.get("title"):
                return rep
        return self._rep(self._profile.track_hrefs[0])

    def _content_info(self) -> dict:
        href = self._profile.content_info_href
        return self._rep(href) if href is not None else {}

    def _is_broadcast(self) -> bool:
        # Channel resources retain the last station while an app or HDMI is active.
        if self._profile.channel_href is None:
            return False
        track = self._track()
        if track.get("title") or track.get("appName"):
            return False
        content_input = self._content_info().get("x.com.samsung.tv.input")
        source = self._rep(self._profile.source.href).get("x.com.samsung.tv.currentMode")
        return content_input == "dtv" and source in (None, "dtv")

    def _schedule(self) -> tuple[int, int] | None:
        """(position, duration) of the current broadcast, from its own
        start/end/current clock -- strings like '2026-01-01 19:41'."""
        if not self._is_broadcast():
            return None
        rep = self._content_info()
        try:
            start = datetime.fromisoformat(rep["x.com.samsung.tv.starttime"])
            end = datetime.fromisoformat(rep["x.com.samsung.tv.endtime"])
            current = datetime.fromisoformat(rep["x.com.samsung.tv.currenttime"])
            position = max(0, int((current - start).total_seconds()))
            duration = int((end - start).total_seconds())
            return (position, duration) if duration > 0 else None
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def media_title(self) -> str | None:
        track = self._track()
        if track.get("title") or track.get("appName"):
            return track.get("title") or track.get("appName")
        content = self._content_info()
        if content.get("x.com.samsung.tv.input") == "dtv" and not self._is_broadcast():
            return None
        return content.get("x.com.samsung.tv.title") or None

    @property
    def media_artist(self) -> str | None:
        track = self._track()
        return track.get("artist") or track.get("author") or None

    @property
    def media_album_name(self) -> str | None:
        return self._track().get("album") or None

    @property
    def media_duration(self) -> int | None:
        duration = self._track().get("duration")
        if isinstance(duration, int) and duration > 0:
            # Milliseconds (133556 for a 2:13 track on the S61B).
            return duration // 1000 or None
        schedule = self._schedule()
        return schedule[1] if schedule else None

    def _playback_position_ms(self) -> int | None:
        """Playback position in ms, excluding idle or frozen feeds.

        Broadcasts idle at zero without a track. AirPlay/BT can play while
        this feed says stop and retains a stale position.
        """
        playback = self._rep(self._profile.playback_href)
        if _first(playback.get("modes")) == "stop":
            return None
        position = playback.get("position")
        if (
            isinstance(position, int)
            and position >= 0
            and (position > 0 or self._track().get("title"))
        ):
            return position
        return None

    @property
    def media_position(self) -> int | None:
        position_ms = self._playback_position_ms()
        if position_ms is not None:
            return position_ms // 1000
        schedule = self._schedule()
        return schedule[0] if schedule else None

    def _feed_playing(self) -> bool:
        """Whether the playback feed itself says play, so its position advances."""
        return _first(self._rep(self._profile.playback_href).get("modes")) == "play"

    def _position_evidence(self):
        """What media_position currently reads from, for change detection.

        The feed's play mode is part of it: resuming at an unchanged position
        must restamp, or the paused time would count as elapsed.
        """
        position_ms = self._playback_position_ms()
        if position_ms is not None:
            return ("playback", position_ms, self._feed_playing())
        return ("schedule", self._schedule())

    def _handle_coordinator_update(self) -> None:
        # Restamp only when the position or play mode moved, not on volume or
        # source changes, or the progress bar would jump.
        evidence = self._position_evidence()
        if evidence != self._last_position_evidence:
            self._position_updated_at = dt_util.utcnow()
            self._last_position_evidence = evidence
        super()._handle_coordinator_update()

    @property
    def media_position_updated_at(self):
        """When the position was sampled; HA draws no progress bar without it."""
        if self.media_position is None:
            return None
        return self._position_updated_at

    @property
    def app_name(self) -> str | None:
        return self._track().get("appName") or None

    @property
    def media_image_url(self) -> str | None:
        thumbnail = self._track().get("thumbnail")
        if not isinstance(thumbnail, str):
            return None
        return thumbnail if thumbnail.startswith(("http://", "https://")) else None

    def _thumbnail_blob_b64(self) -> str | None:
        href = self._profile.thumbnail_href
        if href is None:
            return None
        data = self._rep(href).get("x.com.samsung.tv.thumbnail")
        return data if isinstance(data, str) and data else None

    @property
    def media_image_remotely_accessible(self) -> bool:
        # Always proxy: artwork URLs can be plain http, which HTTPS frontends block.
        return False

    @property
    def media_image_hash(self) -> str | None:
        if self.media_image_url is not None:
            return super().media_image_hash
        blob_b64 = self._thumbnail_blob_b64()
        if blob_b64 is None:
            return None
        # Changes with the artwork, so the frontend refetches it.
        return hashlib.sha1(blob_b64.encode()).hexdigest()[:16]

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Track art by URL, else the TV's thumbnail blob."""
        if self.media_image_url is not None:
            return await super().async_get_media_image()
        blob_b64 = self._thumbnail_blob_b64()
        if blob_b64 is None:
            return None, None
        try:
            body = base64.b64decode(blob_b64)
        except Exception:
            return None, None
        return body, "image/jpeg" if body.startswith(b"\xff\xd8\xff") else "image/png"

    # -- commands -----------------------------------------------------------

    async def _send(self, kind: str, value=None) -> None:
        if kind == "source" or (kind, value) in (("power", False), ("playback", "stop")):
            async with self.coordinator.playback.async_change():
                await self.coordinator.async_send_command(self._bound, (kind, value))
            return
        optimistic_body = None
        if kind == "playback" and value in ("play", "pause"):
            optimistic_body = self._playback_optimistic_body(value)
        await self.coordinator.async_send_command(
            self._bound, (kind, value), optimistic_body=optimistic_body
        )

    def _playback_optimistic_body(self, mode: str) -> dict:
        body: dict = {"modes": [mode]}
        # An optimistic mode cannot make an AirPlay/BT feed's stale position
        # usable. Wait for the device to report a position again.
        if self._playback_position_ms() is None:
            body["position"] = None
        return body

    async def async_turn_on(self) -> None:
        await self._send("power", True)

    async def async_turn_off(self) -> None:
        await self._send("power", False)

    async def async_set_volume_level(self, volume: float) -> None:
        await self._send("volume", round(volume * 100))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._send("mute", mute)

    async def async_select_source(self, source: str) -> None:
        await self._send("source", source)

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        await self._send("sound_mode", sound_mode)

    async def async_media_play(self) -> None:
        await self._send("playback", "play")

    async def async_media_pause(self) -> None:
        await self._send("playback", "pause")

    async def async_media_stop(self) -> None:
        await self._send("playback", "stop")

    async def async_media_play_pause(self) -> None:
        # A paused AirPlay/BT stream ignores "play" but resumes on "playpause".
        # The cache gets the mode it toggles to: "playpause" is no state.
        toggled = "pause" if self.state == MediaPlayerState.PLAYING else "play"
        await self.coordinator.async_send_command(
            self._bound,
            ("playback", "playpause"),
            optimistic_body=self._playback_optimistic_body(toggled),
        )

    async def async_media_next_track(self) -> None:
        await self._send("next_track")

    async def async_media_previous_track(self) -> None:
        await self._send("previous_track")

    async def async_media_seek(self, position: float) -> None:
        async with self.coordinator.playback.lock:
            await self._async_seek(position)

    async def _async_seek(self, position: float) -> None:
        if self._playback_position_ms() is None:
            return
        target_ms = round(position * 1000)
        previous = None

        def prepare_payload():
            nonlocal previous
            # Polls and reconnects can hold the session while playback advances.
            # Compute the relative seek only once the transport is ready.
            position_ms = self._playback_position_ms()
            if position_ms is None:
                return None
            now = dt_util.utcnow()
            current = position_ms / 1000
            playing = self._feed_playing()
            if playing and self._position_updated_at is not None:
                current += (now - self._position_updated_at).total_seconds()
            previous = self._position_updated_at, self._last_position_evidence
            self._position_updated_at = now
            self._last_position_evidence = ("playback", target_ms, playing)
            return "seek", round(position - current)

        try:
            await self.coordinator.async_send_command(
                self._bound,
                ("seek", 0),
                optimistic_body={"position": target_ms},
                prepare_payload=prepare_payload,
            )
        except BaseException:
            if previous is not None:
                self._position_updated_at, self._last_position_evidence = previous
            raise

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._send("shuffle", shuffle)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        await self._send("repeat", repeat.value)
