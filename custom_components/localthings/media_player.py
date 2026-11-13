"""Media player platform for Local Things (AV boards).

One entity per device. Its MediaPlayerDesc names the sibling resources it
reads (power, volume, source, sound mode, track data) and its write_fn turns
(kind, value) pairs into writes, like climate.py. A feature is offered only
while the device reports it. A TV's descriptor adds the broadcast channel
and schedule, its thumbnail and play_media (channels, apps and casting).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
from typing import cast
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.components import media_source
from homeassistant.components.media_player import (
    ATTR_MEDIA_EXTRA,
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import camera_dash, camera_stream
from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, async_add_bound_entities
from .registry.capabilities import tv as tv_caps
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

# What play_media casts besides channel/app: images, video files, HLS
# streams and YouTube links or ids.
_CAST_TYPES = frozenset({MediaType.IMAGE, MediaType.URL, MediaType.VIDEO, "youtube"})

_VIDEO_MIMETYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".ts": "video/mp2t",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
}
_HLS_MIMETYPES = frozenset(
    {"application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl"}
)
# DASH plays when Gallery is launched on it directly (camera_dash); through
# the renderer's prompt it is untested, so refused there.
_DASH_SUFFIX = ".mpd"
# A camera without HLS resolves to its MJPEG stream, which the TV can't play;
# its snapshot is shown instead.
_MJPEG_STREAM_PATH = "/api/camera_proxy_stream/"
_SNAPSHOT_PATH = "/api/camera_proxy/"
_CAMERA_SOURCE = "media-source://camera/"


def _video_mimetype(url: str) -> str | None:
    """The video type a link's file extension names, if any."""
    path = urlsplit(url).path.lower()
    return next((mime for ext, mime in _VIDEO_MIMETYPES.items() if path.endswith(ext)), None)


def _is_hls(media_type: str, url: str) -> bool:
    return media_type.lower() in _HLS_MIMETYPES or urlsplit(url).path.lower().endswith(".m3u8")


def _is_browsable(item) -> bool:
    media_type = item.media_content_type.lower()
    return media_type.startswith(("image/", "video/")) or media_type in _HLS_MIMETYPES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_bound_entities(
        entry,
        coordinator,
        async_add_entities,
        lambda b: isinstance(b.desc, MediaPlayerDesc),
        lambda b: LocalThingsMediaPlayer(coordinator, b),
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

    async def async_will_remove_from_hass(self) -> None:
        await self.coordinator.playback.async_close()
        await super().async_will_remove_from_hass()

    @property
    def media_content_type(self) -> MediaType:
        if self._desc.device_class == MediaPlayerDeviceClass.TV:
            return MediaType.CHANNEL if self._is_broadcast() else MediaType.VIDEO
        return MediaType.MUSIC

    # -- resource helpers ---------------------------------------------------

    def _rep(self, href: str | None) -> dict:
        if not href:
            return {}
        return self.coordinator.resource(self._bound.subdevice.to_actual(href))

    def _playback_href(self) -> str:
        return self._desc.playback_href or self._bound.href or ""

    def _playback(self) -> dict:
        return self._rep(self._playback_href())

    def _stream_active(self) -> bool:
        fn = self._desc.stream_active_fn
        return fn is None or fn(self._resources)

    def _track(self) -> dict:
        if not self._stream_active():
            return {}
        track = self._rep(self._desc.track_href)
        if not track.get("title") and self._desc.fallback_track_href:
            fallback = self._rep(self._desc.fallback_track_href)
            if fallback.get("title"):
                return fallback
        return track

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
        if surface is None:
            return []
        rep = self._rep(surface.href)
        if surface.options_fn is not None:
            return list(surface.options_fn(rep))
        return _strings(rep.get(surface.options_field))

    def _label(self, surface: MediaModeSurface, value: str) -> str:
        if surface.options_fn is not None:
            return surface.options_fn(self._rep(surface.href)).get(value, value)
        return (surface.labels or {}).get(value, value)

    def _supports_command(self, command: str) -> bool:
        fn = self._desc.supported_commands_fn
        return fn is None or command in fn(self._resources)

    def _turn_on_available(self) -> bool:
        if self._desc.wake_on_lan:
            return self.coordinator.av.wakeable()
        return self._supports_command("power_on")

    def _wire_value(self, surface: MediaModeSurface | None, label: str) -> str:
        for value in self._mode_options(surface):
            if surface is not None and self._label(surface, value) == label:
                return value
        return label

    # -- features -----------------------------------------------------------

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = MediaPlayerEntityFeature(0)
        if self._turn_on_available():
            features |= MediaPlayerEntityFeature.TURN_ON
        for command, feature in (
            ("power_off", MediaPlayerEntityFeature.TURN_OFF),
            ("volume", MediaPlayerEntityFeature.VOLUME_SET),
            ("mute", MediaPlayerEntityFeature.VOLUME_MUTE),
        ):
            if self._supports_command(command):
                features |= feature
        if self._supports_command("source") and self._mode_options(self._desc.source):
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        if self._supports_command("sound_mode") and self._mode_options(self._desc.sound_mode):
            features |= MediaPlayerEntityFeature.SELECT_SOUND_MODE
        if self._supports_command("shuffle") and "mode" in self._rep(self._desc.shuffle_href):
            features |= MediaPlayerEntityFeature.SHUFFLE_SET
        if self._desc.play_media:
            # Channels, apps and casting (async_play_media); the browser picks
            # from Home Assistant's media sources.
            features |= MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.BROWSE_MEDIA
        if not self._has_media():
            return features
        supported = self._playback().get("supportedModes")
        for mode, feature in _PLAYBACK_FEATURES:
            if self._supports_command("playback") and (
                not isinstance(supported, list) or mode in supported
            ):
                features |= feature
        # _stream_state, not state: _has_media above already ruled out a
        # stale stream, so this skips re-reading power and the stream check.
        if (
            not self._desc.stop_when_paused
            and features & MediaPlayerEntityFeature.STOP
            and self._stream_state() is MediaPlayerState.PAUSED
        ):
            features &= ~MediaPlayerEntityFeature.STOP
        # Not in mediaPlayback's supportedModes; the track control resource
        # lists them while a stream is active (HW-Q950T).
        skips = _strings(self._rep(self._desc.track_control_href).get("supportedModes"))
        if "next" in skips and self._supports_command("next_track"):
            features |= MediaPlayerEntityFeature.NEXT_TRACK
        if "previous" in skips and self._supports_command("previous_track"):
            features |= MediaPlayerEntityFeature.PREVIOUS_TRACK
        if self._supports_command("seek") and self._playback_position_ms() is not None:
            features |= MediaPlayerEntityFeature.SEEK
        return features

    # -- state --------------------------------------------------------------

    @property
    def available(self) -> bool:
        # A TV that is off still takes turn-on (Wake-on-LAN).
        return self.coordinator.av.reads_as_off() or super().available

    @property
    def state(self) -> MediaPlayerState | None:
        if self.coordinator.av.reads_as_off():
            return MediaPlayerState.OFF
        power = self._rep(self._desc.power_href)
        if "value" not in power:
            return None
        if power.get("value") is not True:
            return MediaPlayerState.OFF
        if not self._stream_active():
            return MediaPlayerState.ON
        state = self._stream_state()
        if self._desc.channel_href is None:
            return state
        # The playback feed idles on live TV; the broadcast schedule still
        # advances. Otherwise an idle feed only says the TV's own player is
        # idle: on HDMI or its home screen the TV is just on.
        if state in (MediaPlayerState.IDLE, MediaPlayerState.ON) and self._schedule():
            return MediaPlayerState.PLAYING
        return MediaPlayerState.ON if state is MediaPlayerState.IDLE else state

    def _stream_state(self) -> MediaPlayerState:
        """`state` for a powered-on player whose stream feeds are current."""
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
    def media_channel(self) -> str | None:
        href = self._desc.channel_href
        if href is None or not self._is_broadcast():
            return None
        rep = self._rep(href)
        number = rep.get("x.com.samsung.tv.channelNumber")
        name = rep.get("x.com.samsung.tv.channelName")
        # '4 - News 24 HD'; the bare name or number where only one exists.
        if number and name:
            return f"{number} - {name}"
        return name or number

    def _content_info(self) -> dict:
        return self._rep(self._desc.content_info_href)

    def _is_broadcast(self) -> bool:
        # The channel resource keeps the last station while an app or HDMI is on.
        if self._desc.channel_href is None:
            return False
        track = self._track()
        if track.get("title") or track.get("appName"):
            return False
        source = self._desc.source
        current = self._rep(source.href).get(source.field) if source is not None else None
        return self._content_info().get("x.com.samsung.tv.input") == "dtv" and current in (
            None,
            "dtv",
        )

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
    def shuffle(self) -> bool | None:
        mode = self._rep(self._desc.shuffle_href).get("mode")
        return None if mode is None else mode == "enabled"

    # -- track metadata (empty strings while idle) ---------------------------

    @property
    def media_title(self) -> str | None:
        track = self._track()
        if track.get("title") or track.get("appName"):
            return track.get("title") or track.get("appName")
        # A TV's content info: the program on a broadcast, the app in front.
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

    @property
    def app_name(self) -> str | None:
        return self._track().get("appName") or None

    @property
    def media_image_url(self) -> str | None:
        thumbnail = self._track().get("thumbnail")
        if isinstance(thumbnail, str) and thumbnail.startswith(("http://", "https://")):
            return thumbnail
        return None

    def _thumbnail_blob_b64(self) -> str | None:
        data = self._rep(self._desc.thumbnail_href).get("x.com.samsung.tv.thumbnail")
        return data if isinstance(data, str) and data else None

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
        """Track art by URL, else a TV's thumbnail blob."""
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
        if position_ms is not None:
            return position_ms // 1000
        # Live TV: the playback feed idles, the broadcast schedule advances.
        schedule = self._schedule()
        return schedule[0] if schedule else None

    def _feed_playing(self) -> bool:
        """Whether the playback feed itself says play, so its position advances."""
        return first_mode(self._playback()) == "play"

    def _position_evidence(self) -> tuple:
        """What media_position reads from, for change detection.

        The play mode is part of it: resuming at an unchanged position must
        restamp, or the paused time would count as elapsed.
        """
        position_ms = self._playback_position_ms()
        if position_ms is None and (schedule := self._schedule()) is not None:
            return ("schedule", schedule)
        return (position_ms, self._feed_playing())

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
        # it, so the next command needn't wait for a poll. What replaces the
        # picture on a TV releases a camera it was casting (playback.py).
        replaces = kind == "source" or (kind, value) in (("power", False), ("playback", "stop"))
        async with (
            self.coordinator.playback.async_change() if replaces else nullcontext(),
            self._command_lock,
        ):
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
        if not self._desc.wake_on_lan:
            await self._send("power", True)
        elif self.coordinator.av.wakeable():
            await self.coordinator.av.async_power_on(self._bound)
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="media_option_not_supported"
            )

    async def async_turn_off(self) -> None:
        if not self.coordinator.av.reads_as_off():  # else: already off, nothing to reach
            await self._send("power", False)

    async def async_set_volume_level(self, volume: float) -> None:
        await self._send("volume", round(volume * 100))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._send("mute", mute)

    async def async_select_source(self, source: str) -> None:
        await self._send("source", self._wire_value(self._desc.source, source))

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        await self._send("sound_mode", self._wire_value(self._desc.sound_mode, sound_mode))

    async def async_browse_media(
        self, media_content_type: MediaType | str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        """Home Assistant's media sources the TV can show: images, videos and cameras."""
        return await media_source.async_browse_media(
            self.hass, media_content_id, content_filter=_is_browsable
        )

    async def async_play_media(self, media_type: MediaType | str, media_id: str, **kwargs) -> None:
        """Play media, superseding any pending camera retry."""
        async with self.coordinator.playback.async_change():
            await self._async_play_media(media_type, media_id, **kwargs)

    async def _async_play_media(self, media_type: MediaType | str, media_id: str, **kwargs) -> None:
        """Tune channels, launch apps or cast media; extra.ask requests a TV prompt."""
        if not self._desc.play_media:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_media_type",
                translation_placeholders={"media_type": str(media_type)},
            )
        extra = kwargs.get(ATTR_MEDIA_EXTRA)
        extra = extra if isinstance(extra, dict) else {}
        title = str(extra["title"]) if extra.get("title") else None
        icon = str(extra["icon"]) if extra.get("icon") else None
        try:
            ask = cv.boolean(extra.get("ask", False))
        except vol.Invalid as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_ask",
                translation_placeholders={"value": str(extra.get("ask"))},
            ) from err
        camera = None
        source = None
        if str(media_id).startswith(_CAMERA_SOURCE):
            camera = str(media_id).removeprefix(_CAMERA_SOURCE)
            if icon is None:
                icon = _SNAPSHOT_PATH + camera
            if camera_dash.is_available(self.hass):  # registered with camera_stream's
                source = await camera_stream.async_stream_source(self.hass, camera)
        if camera and source and not ask and self.coordinator.av.can_launch_gallery():
            if await self.coordinator.playback.async_cast_camera(camera, source):
                return
            # DASH couldn't start, nor could camera_stream's ffmpeg: HA's own.
            source = None
        if camera and source:
            # A continuous stream starts at once; HLS waits for segments.
            media_type, media_id = camera_stream.MIMETYPE, camera_stream.stream_path(camera)
        elif media_source.is_media_source_id(media_id):
            try:
                async with asyncio.timeout(camera_stream.SOURCE_TIMEOUT_S if camera else None):
                    sourced = await media_source.async_resolve_media(
                        self.hass, media_id, self.entity_id
                    )
            except TimeoutError as err:
                if camera is None:
                    raise
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="camera_timeout",
                    translation_placeholders={"camera": camera},
                ) from err
            media_type, media_id = sourced.mime_type, sourced.url
        if media_type not in (MediaType.CHANNEL, MediaType.APP):
            await self._async_cast(str(media_type), str(media_id), title, icon, ask=ask)
            return
        if media_type == MediaType.APP:
            app = str(media_id).strip()
            if not app:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_app_id",
                )
            await self._send("app", app)
            return
        number = str(media_id).strip()
        if not tv_caps.CHANNEL_NUMBER.fullmatch(number):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_channel_number",
                translation_placeholders={"channel": str(media_id)},
            )
        # The name follows on the next read; until then the number alone,
        # not the old station's name under the new number.
        await self.coordinator.async_send_command(
            self._bound,
            ("channel", number),
            optimistic_body={
                "x.com.samsung.tv.channelNumber": number,
                "x.com.samsung.tv.channelName": "",
            },
        )

    async def _async_cast(
        self,
        media_type: str,
        media_id: str,
        title: str | None = None,
        icon: str | None = None,
        *,
        ask: bool = False,
    ) -> None:
        """Cast through Gallery/YouTube, or the renderer when a prompt is needed.

        HLS and TVs without direct app launch use the renderer. Title and
        icon apply only to its prompt.
        """
        if media_id.startswith(_MJPEG_STREAM_PATH):
            media_type = MediaType.IMAGE
            media_id = _SNAPSHOT_PATH + media_id.removeprefix(_MJPEG_STREAM_PATH)
        is_image = media_type == MediaType.IMAGE or media_type.startswith("image/")
        is_video = media_type.startswith("video/")
        is_hls = _is_hls(media_type, media_id)
        if not (is_image or is_video or is_hls) and media_type not in _CAST_TYPES:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_media_type",
                translation_placeholders={"media_type": media_type},
            )
        # A media-source or other local path becomes an absolute URL the TV
        # can fetch from Home Assistant; YouTube ids are left alone.
        url = media_id
        if media_id.startswith("/") or urlsplit(media_id).scheme in ("http", "https"):
            url = async_process_play_media_url(self.hass, media_id)
        body = None
        kind = "video"
        video_id = None
        if not is_image and (video_id := tv_caps.youtube_id(media_id)):
            body = tv_caps.youtube_body(video_id)
            kind = "youtube"
        elif urlsplit(url).path.lower().endswith(_DASH_SUFFIX):
            if not ask and self.coordinator.av.can_launch_gallery():
                body = {}  # Gallery only; never the renderer (below)
        elif is_hls:
            # The TV only routes video/* to its player, which reads HLS itself.
            body = tv_caps.video_body(url, "video/mp4")
        elif is_video or media_type == MediaType.VIDEO or _video_mimetype(url):
            mime = media_type if is_video else _video_mimetype(url) or "video/mp4"
            body = tv_caps.video_body(url, mime)
        elif is_image or media_type == MediaType.URL:
            body = tv_caps.image_body(url)
            kind = "image"
        if body is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_media_url",
                translation_placeholders={"url": media_id},
            )
        icon_url = None
        if icon:
            icon_url = async_process_play_media_url(self.hass, icon)
            if urlsplit(icon_url).scheme not in ("http", "https"):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_media_url",
                    translation_placeholders={"url": icon},
                )
        if not ask and video_id and self.coordinator.av.can_launch_youtube():
            await self.coordinator.av.async_launch_youtube(video_id)
            return
        # Gallery refuses an HLS playlist ("not supported", measured); the
        # renderer passes it on as video/mp4 and the player reads it.
        direct = kind != "youtube" and not is_hls
        if not ask and direct and self.coordinator.av.can_launch_gallery():
            await self.coordinator.av.async_launch_gallery(kind, url)
            return
        await self.coordinator.av.async_cast(tv_caps.with_sender(body, title, icon_url))

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
