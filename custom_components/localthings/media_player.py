"""Media player platform for Local Things (AV boards).

One entity per device. Its MediaPlayerDesc names the sibling resources it
reads (power, volume, source, sound mode, track data) and its write_fn turns
(kind, value) pairs into writes, like climate.py. Nothing here is specific
to one family; a feature is offered only while the device reports it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import cast

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included
from .registry.capabilities.av import first_mode
from .registry.entities import MediaModeSurface, MediaPlayerDesc

# /capability/mediaPlayback's modes.
_PLAYBACK_STATES = {
    "play": MediaPlayerState.PLAYING,
    "pause": MediaPlayerState.PAUSED,
    "stop": MediaPlayerState.IDLE,
    "buffering": MediaPlayerState.BUFFERING,
}

# A family's own status (MediaPlayerDesc.status_fn).
_STATUS_STATES = {
    "PLAYING": MediaPlayerState.PLAYING,
    "PAUSED": MediaPlayerState.PAUSED,
    "BUFFERING": MediaPlayerState.BUFFERING,
    "IDLE": MediaPlayerState.IDLE,
    "STOPPED": MediaPlayerState.IDLE,
}

_PLAYBACK_FEATURES = (
    ("play", MediaPlayerEntityFeature.PLAY),
    ("pause", MediaPlayerEntityFeature.PAUSE),
    ("stop", MediaPlayerEntityFeature.STOP),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LocalThingsMediaPlayer(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, MediaPlayerDesc) and _is_included(b, coordinator)
    )


def _strings(value) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


class LocalThingsMediaPlayer(LocalThingsEntity, MediaPlayerEntity):
    """Composite media player entity for a Samsung AV board."""

    _attr_media_content_type = MediaType.MUSIC
    # Always proxied: artwork URLs can be plain http, which HTTPS frontends block.
    _attr_media_image_remotely_accessible = False

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        self._desc = cast(MediaPlayerDesc, bound.desc)
        self._attr_device_class = MediaPlayerDeviceClass(self._desc.device_class)
        self._attr_name = None  # this entity *is* the device
        # The device steps in integers 0..100.
        self._attr_volume_step = 0.01
        # Every command goes through here, so a skip can't land mid-seek and
        # a seek's offset is computed against the track it will move.
        self._command_lock = asyncio.Lock()
        # See _handle_coordinator_update.
        self._position_updated_at: datetime | None = None
        self._last_position_evidence: tuple | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Discovery has already populated the cache before listeners are attached.
        self._last_position_evidence = self._position_evidence()
        self._position_updated_at = dt_util.utcnow()

    # -- resource helpers ---------------------------------------------------

    def _rep(self, href: str | None) -> dict:
        if not href:
            return {}
        return self.coordinator.resource(self._bound.subdevice.to_actual(href))

    def _playback(self) -> dict:
        return self.coordinator.resource(self._bound.href or "")

    def _stream_active(self) -> bool:
        fn = self._desc.stream_active_fn
        return fn is None or fn(self._resources)

    def _track(self) -> dict:
        return self._rep(self._desc.track_href) if self._stream_active() else {}

    def _feed_state(self) -> MediaPlayerState | None:
        return _PLAYBACK_STATES.get(str(first_mode(self._playback())))

    def _family_state(self) -> MediaPlayerState | None:
        status_fn = self._desc.status_fn
        status = status_fn(self._resources) if status_fn is not None else None
        return _STATUS_STATES.get(status) if status is not None else None

    def _has_media(self) -> bool:
        """Whether there is anything for playback commands to act on. An
        idle bar acknowledges play and next and does nothing (HW-Q950T)."""
        if not self._stream_active():
            return False
        track = self._track()
        if track.get("title") or track.get("appName"):
            return True
        return any(
            state not in (None, MediaPlayerState.IDLE)
            for state in (self._feed_state(), self._family_state())
        )

    def _mode_options(self, surface: MediaModeSurface | None) -> list[str]:
        return _strings(self._rep(surface.href).get(surface.options_field)) if surface else []

    @staticmethod
    def _label(surface: MediaModeSurface, value: str) -> str:
        return (surface.labels or {}).get(value, value)

    def _wire_value(self, surface: MediaModeSurface | None, label: str) -> str:
        for value in self._mode_options(surface):
            if surface is not None and self._label(surface, value) == label:
                return value
        return label

    # -- features -----------------------------------------------------------

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
        )
        if self._mode_options(self._desc.source):
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        if self._mode_options(self._desc.sound_mode):
            features |= MediaPlayerEntityFeature.SELECT_SOUND_MODE
        if "mode" in self._rep(self._desc.shuffle_href):
            features |= MediaPlayerEntityFeature.SHUFFLE_SET
        if not self._has_media():
            return features
        supported = self._playback().get("supportedModes")
        for mode, feature in _PLAYBACK_FEATURES:
            if not isinstance(supported, list) or mode in supported:
                features |= feature
        if not self._desc.stop_when_paused and self.state is MediaPlayerState.PAUSED:
            features &= ~MediaPlayerEntityFeature.STOP
        # Not in mediaPlayback's supportedModes; the track control resource
        # lists them while a stream is active (HW-Q950T).
        skips = _strings(self._rep(self._desc.track_control_href).get("supportedModes"))
        if "next" in skips:
            features |= MediaPlayerEntityFeature.NEXT_TRACK
        if "previous" in skips:
            features |= MediaPlayerEntityFeature.PREVIOUS_TRACK
        if self._playback_position_ms() is not None:
            features |= MediaPlayerEntityFeature.SEEK
        return features

    # -- state --------------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState | None:
        power = self._rep(self._desc.power_href)
        if "value" not in power:
            return None
        if power.get("value") is not True:
            return MediaPlayerState.OFF
        if not self._stream_active():
            return MediaPlayerState.ON
        feed = self._feed_state()
        if feed not in (None, MediaPlayerState.IDLE):
            return feed
        # Our own stop, not yet polled: the family's status still says what
        # played before it.
        if feed is MediaPlayerState.IDLE and self.coordinator.write_settling(
            self._bound.href or ""
        ):
            return feed
        # The generic feed can say stop while an AirPlay or BT stream plays;
        # the family's own status, where it has one, knows better.
        return self._family_state() or feed or MediaPlayerState.ON

    @property
    def volume_level(self) -> float | None:
        volume = self._rep(self._desc.audio_href).get("volume")
        if volume is None:
            return None
        try:
            return max(0.0, min(1.0, float(volume) / 100.0))
        except (TypeError, ValueError):
            return None

    @property
    def is_volume_muted(self) -> bool | None:
        mute = self._rep(self._desc.audio_href).get("mute")
        return None if mute is None else bool(mute)

    def _current_mode(self, surface: MediaModeSurface | None) -> str | None:
        if surface is None:
            return None
        value = self._rep(surface.href).get(surface.field)
        # A soundbar's source goes empty while it streams; HA wants None.
        return self._label(surface, value) if isinstance(value, str) and value else None

    def _mode_list(self, surface: MediaModeSurface | None) -> list[str] | None:
        options = self._mode_options(surface)
        if surface is None or not options:
            return None
        return [self._label(surface, value) for value in options]

    @property
    def source(self) -> str | None:
        return self._current_mode(self._desc.source)

    @property
    def source_list(self) -> list[str] | None:
        return self._mode_list(self._desc.source)

    @property
    def sound_mode(self) -> str | None:
        return self._current_mode(self._desc.sound_mode)

    @property
    def sound_mode_list(self) -> list[str] | None:
        return self._mode_list(self._desc.sound_mode)

    @property
    def shuffle(self) -> bool | None:
        mode = self._rep(self._desc.shuffle_href).get("mode")
        return None if mode is None else mode == "enabled"

    # -- track metadata (empty strings while idle) ---------------------------

    @property
    def media_title(self) -> str | None:
        track = self._track()
        return track.get("title") or track.get("appName") or None

    @property
    def media_artist(self) -> str | None:
        return self._track().get("artist") or None

    @property
    def media_album_name(self) -> str | None:
        return self._track().get("album") or None

    @property
    def media_duration(self) -> int | None:
        duration = self._track().get("duration")
        if not isinstance(duration, int) or duration <= 0:
            return None
        # Milliseconds (133556 for a 2:13 track on the S61B).
        return duration // 1000 or None

    @property
    def app_name(self) -> str | None:
        return self._track().get("appName") or None

    @property
    def media_image_url(self) -> str | None:
        thumbnail = self._track().get("thumbnail")
        if isinstance(thumbnail, str) and thumbnail.startswith(("http://", "https://")):
            return thumbnail
        return None

    # -- position -----------------------------------------------------------

    def _playback_position_ms(self) -> int | None:
        """Playback position in ms, or None when there is none to show:
        a stopped feed, or a stale one left over from a stream."""
        if not self._stream_active():
            return None
        playback = self._playback()
        if first_mode(playback) == "stop":
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
        return None if position_ms is None else position_ms // 1000

    def _feed_playing(self) -> bool:
        """Whether the playback feed itself says play, so its position advances."""
        return first_mode(self._playback()) == "play"

    def _position_evidence(self) -> tuple:
        """What media_position reads from, for change detection.

        The play mode is part of it: resuming at an unchanged position must
        restamp, or the paused time would count as elapsed.
        """
        return (self._playback_position_ms(), self._feed_playing())

    def _handle_coordinator_update(self) -> None:
        # Restamp only when the position or play mode moved, not on volume or
        # source changes, or the progress bar would jump.
        evidence = self._position_evidence()
        if evidence != self._last_position_evidence:
            self._position_updated_at = dt_util.utcnow()
            self._last_position_evidence = evidence
        super()._handle_coordinator_update()

    @property
    def media_position_updated_at(self) -> datetime | None:
        """When the position was sampled; HA draws no progress bar without it."""
        if self.media_position is None:
            return None
        return self._position_updated_at

    # -- commands -----------------------------------------------------------

    async def _send(
        self, kind: str, value=None, *, optimistic: Callable[[], dict] | None = None
    ) -> None:
        # The optimistic body is worked out inside the lock, against the
        # state the previous command left. The confirming refresh runs after
        # it, so the next command needn't wait for a poll.
        async with self._command_lock:
            sent = await self.coordinator.async_send_command(
                self._bound,
                (kind, value),
                optimistic_body=optimistic() if optimistic is not None else None,
                refresh=False,
            )
        if sent:
            await self.coordinator.async_request_refresh()

    def _playback_optimistic_body(self, mode: str) -> dict:
        body: dict = {"modes": [mode]}
        # An optimistic mode cannot make a feed with no usable position
        # usable. Wait for the device to report one again.
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
        await self._send("source", self._wire_value(self._desc.source, source))

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        await self._send("sound_mode", self._wire_value(self._desc.sound_mode, sound_mode))

    async def async_media_play(self) -> None:
        await self._send(
            "playback", "play", optimistic=lambda: self._playback_optimistic_body("play")
        )

    async def async_media_pause(self) -> None:
        await self._send(
            "playback", "pause", optimistic=lambda: self._playback_optimistic_body("pause")
        )

    async def async_media_stop(self) -> None:
        await self._send("playback", "stop")

    async def async_media_play_pause(self) -> None:
        # A paused AirPlay/BT stream ignores "play" but resumes on "playpause".
        # The cache gets the mode it toggles to: "playpause" is no state.
        def optimistic() -> dict:
            playing = self.state in (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING)
            toggled = "pause" if playing else "play"
            return self._playback_optimistic_body(toggled)

        await self._send("playback", "playpause", optimistic=optimistic)

    async def async_media_next_track(self) -> None:
        await self._send("next_track")

    async def async_media_previous_track(self) -> None:
        await self._send("previous_track")

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._send("shuffle", shuffle)

    async def async_media_seek(self, position: float) -> None:
        async with self._command_lock:
            sent = await self._async_seek(position)
        if sent:
            await self.coordinator.async_request_refresh()

    async def _async_seek(self, position: float) -> bool:
        target_ms = max(0, round(position * 1000))
        duration = self._track().get("duration")
        if isinstance(duration, int) and duration > 0:
            # The device clamps a seek past either end the same way.
            target_ms = min(target_ms, duration)
        prepared_at: list[tuple[datetime, bool]] = []

        def prepare_payload():
            # Polls and reconnects can hold the session while playback
            # advances, so the offset is worked out again once it is ready,
            # always from the same stamp.
            position_ms = self._playback_position_ms()
            if position_ms is None:
                return None
            now = dt_util.utcnow()
            current_ms = position_ms
            playing = self._feed_playing()
            if playing and self._position_updated_at is not None:
                current_ms += round((now - self._position_updated_at).total_seconds() * 1000)
            prepared_at[:] = [(now, playing)]
            return "seek", target_ms - current_ms

        payload = prepare_payload()
        if payload is None:
            return False
        sent = await self.coordinator.async_send_command(
            self._bound,
            payload,
            optimistic_body={"position": target_ms},
            prepare_payload=prepare_payload,
            refresh=False,
        )
        if sent:
            # The device is at the target as of the send, whatever the
            # cache's own update stamped.
            ((now, playing),) = prepared_at
            self._position_updated_at = now
            self._last_position_evidence = (target_ms, playing)
        return sent
