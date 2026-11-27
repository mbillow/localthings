"""Audio notifications through a soundbar's /x.com.samsung/notification/receiver.

The board downloads the URL it is given and plays it as raw 24 kHz PCM
(HW-S61B, audio_notify.py). These tests hold the payload, the PCM proxy and
the media player's dispatch to them.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from homeassistant.components.media_player import MediaPlayerEntityFeature, MediaType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.network import NoURLAvailableError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import audio_notify
from custom_components.localthings import media_player as mp
from custom_components.localthings.const import CONF_HOST, DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities.av import HREF_AUDIO_NOTIFICATION
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import MediaPlayerDesc
from custom_components.localthings.transport import Transport
from tests.conftest import _load_device

FIXTURE = "soundbar_s61b"
DEVICE_TYPES = ("oic.wk.d", "oic.d.networkaudio")
PATH = ["x.com.samsung", "notification", "receiver"]
CLIP_URL = "http://192.0.2.10:8123/api/localthings/audio/abc.pcm"
SOURCE = "http://192.0.2.10:8123/api/tts_proxy/x.mp3"


def _make(hass, monkeypatch, receiver: dict | None) -> LocalThingsCoordinator:
    """The S61B capture, with `receiver` as the receiver's rep (None: absent)."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "192.0.2.1"})
    entry.add_to_hass(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    session = MagicMock()
    session.write.return_value = (0x44, None)
    session.read.return_value = (0x45, {})
    coordinator._session = cast(Transport, session)
    coordinator.async_request_refresh = AsyncMock()
    monkeypatch.setattr(coordinator, "_session_answers", lambda sess: True)
    resources = _load_device(FIXTURE)
    resources.pop(HREF_AUDIO_NOTIFICATION)
    if receiver is not None:
        resources[HREF_AUDIO_NOTIFICATION] = receiver
    for href, rep in resources.items():
        coordinator._observe.apply(href, rep, source="poll")
    return coordinator


@pytest.fixture
def coordinator(hass, monkeypatch):
    return _make(hass, monkeypatch, _load_device(FIXTURE)[HREF_AUDIO_NOTIFICATION])


@pytest.fixture
def bare_coordinator(hass, monkeypatch):
    return _make(hass, monkeypatch, None)


def _player(hass, coordinator, **desc_changes) -> mp.LocalThingsMediaPlayer:
    resources = _load_device(FIXTURE)
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None
    bound = next(
        b
        for b in discover(resources, reg.capabilities, reg.pattern_capabilities)
        if isinstance(b.desc, MediaPlayerDesc)
    )
    if desc_changes:
        bound = dataclasses.replace(bound, desc=dataclasses.replace(bound.desc, **desc_changes))
    player = mp.LocalThingsMediaPlayer(coordinator, bound)
    player.hass = hass
    player.entity_id = "media_player.soundbar"
    return player


# -- capability ------------------------------------------------------------


def test_the_s61b_capture_takes_audio_notifications(coordinator):
    assert coordinator.av.can_play_audio_notification()


def test_a_receiver_without_the_audio_type_is_not_used(hass, monkeypatch):
    coordinator = _make(hass, monkeypatch, {"rt": ["x.com.samsung.other"], "if": ["oic.if.a"]})
    assert not coordinator.av.can_play_audio_notification()


def test_an_advertised_receiver_counts_before_it_was_read(bare_coordinator):
    """Write-only resources may never be read; /oic/res still names them."""
    coordinator = bare_coordinator
    assert not coordinator.av.can_play_audio_notification()
    coordinator._last_oic_res = [
        {
            "links": [
                {"href": HREF_AUDIO_NOTIFICATION, "rt": ["x.com.samsung.audionotification"]},
            ]
        }
    ]
    assert coordinator.av.can_play_audio_notification()


# -- the write -------------------------------------------------------------


async def test_the_write_carries_only_the_url_and_reads_nothing_back(coordinator):
    session = cast(MagicMock, coordinator._session)
    before = dict(coordinator.resource(HREF_AUDIO_NOTIFICATION))
    await coordinator.av.async_play_audio_notification(CLIP_URL)
    session.write.assert_called_once_with(
        PATH, {"x.com.samsung.audio": CLIP_URL}, timeout=coordinator._POST_TIMEOUT_S
    )
    session.read.assert_not_called()
    assert coordinator.resource(HREF_AUDIO_NOTIFICATION) == before


async def test_a_refused_write_is_reported(coordinator):
    cast(MagicMock, coordinator._session).write.return_value = (0x80, None)
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.av.async_play_audio_notification(CLIP_URL)
    assert err.value.translation_key == "command_refused"


async def test_a_lost_reply_is_not_resent(coordinator, monkeypatch):
    """A resend would play the clip twice."""
    session = cast(MagicMock, coordinator._session)
    session.write.side_effect = TimeoutError("reply lost")
    monkeypatch.setattr("custom_components.localthings.coordinator.asyncio.sleep", AsyncMock())
    monkeypatch.setattr(coordinator, "_connect_session", MagicMock())
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.av.async_play_audio_notification(CLIP_URL)
    assert err.value.translation_key == "command_failed"
    session.write.assert_called_once()


async def test_a_board_without_the_receiver_refuses(bare_coordinator):
    coordinator = bare_coordinator
    with pytest.raises(ServiceValidationError) as err:
        await coordinator.av.async_play_audio_notification(CLIP_URL)
    assert err.value.translation_key == "audio_notification_unsupported"
    cast(MagicMock, coordinator._session).write.assert_not_called()


# -- ffmpeg ----------------------------------------------------------------


def test_ffmpeg_writes_raw_24khz_mono_pcm():
    args = audio_notify.ffmpeg_args("ffmpeg", SOURCE)
    assert args[0] == "ffmpeg"
    assert args[args.index("-i") + 1] == SOURCE
    assert args.index("-protocol_whitelist") < args.index("-i")
    assert args[args.index("-i") + 2] == "-vn"
    assert args[args.index("-ac") + 1] == "1"
    assert args[args.index("-ar") + 1] == "24000"
    assert args[args.index("-c:a") + 1] == "pcm_s16le"
    assert args[args.index("-f") + 1] == "s16le"
    # One second past the limit, so a longer source is detected and cut.
    assert args[args.index("-t") + 1] == str(audio_notify.MAX_DURATION_S + 1)
    assert args[-1] == "pipe:1"


class _FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0, hang=False):
        self._out = (stdout, stderr)
        self._final = returncode
        self._hang = hang
        self.returncode: int | None = None
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.Event().wait()
        self.returncode = self._final
        return self._out

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


def _spawn(monkeypatch, process):
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(audio_notify.asyncio, "create_subprocess_exec", spawn)
    return spawn


async def test_conversion_returns_whole_frames(monkeypatch):
    _spawn(monkeypatch, _FakeProcess(stdout=b"\x01\x02\x03\x04\x05"))
    assert await audio_notify.async_convert("ffmpeg", SOURCE) == b"\x01\x02\x03\x04"


async def test_a_longer_source_is_cut_to_the_soundbars_limit(monkeypatch, caplog):
    """The HW-S61B stops a notification about 41 s in."""
    assert audio_notify._MAX_PCM_BYTES == 40 * 24000 * 2
    monkeypatch.setattr(audio_notify, "_MAX_PCM_BYTES", 4)
    _spawn(monkeypatch, _FakeProcess(stdout=b"\x01\x02\x03\x04\x05\x06"))
    assert await audio_notify.async_convert("ffmpeg", SOURCE) == b"\x01\x02\x03\x04"
    assert "cut to 40 s" in caplog.text


async def test_a_source_within_the_limit_is_kept_whole(monkeypatch, caplog):
    monkeypatch.setattr(audio_notify, "_MAX_PCM_BYTES", 4)
    _spawn(monkeypatch, _FakeProcess(stdout=b"\x01\x02\x03\x04"))
    assert await audio_notify.async_convert("ffmpeg", SOURCE) == b"\x01\x02\x03\x04"
    assert "cut to" not in caplog.text


async def test_a_failed_conversion_names_ffmpegs_error_without_credentials(monkeypatch):
    stderr = b"noise\nhttp://ha/api/x?authSig=SECRET.tok: Server returned 404 Not Found\n"
    _spawn(monkeypatch, _FakeProcess(stderr=stderr, returncode=1))
    with pytest.raises(audio_notify.AudioConversionError) as err:
        await audio_notify.async_convert("ffmpeg", SOURCE)
    assert "404 Not Found" in str(err.value)
    assert "SECRET" not in str(err.value)


async def test_empty_output_is_an_error(monkeypatch):
    _spawn(monkeypatch, _FakeProcess(stdout=b"\x01"))
    with pytest.raises(audio_notify.AudioConversionError, match="no audio"):
        await audio_notify.async_convert("ffmpeg", SOURCE)


async def test_a_conversion_that_runs_too_long_is_killed(monkeypatch):
    process = _FakeProcess(hang=True)
    _spawn(monkeypatch, process)
    monkeypatch.setattr(audio_notify, "CONVERT_TIMEOUT_S", 0)
    with pytest.raises(audio_notify.AudioConversionError, match="longer than"):
        await audio_notify.async_convert("ffmpeg", SOURCE)
    assert process.killed


async def test_a_cancelled_conversion_kills_ffmpeg(monkeypatch):
    process = _FakeProcess(hang=True)
    _spawn(monkeypatch, process)
    task = asyncio.ensure_future(audio_notify.async_convert("ffmpeg", SOURCE))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed


async def test_missing_ffmpeg_is_a_conversion_error(monkeypatch):
    monkeypatch.setattr(
        audio_notify.asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=FileNotFoundError("ffmpeg")),
    )
    with pytest.raises(audio_notify.AudioConversionError, match="could not start"):
        await audio_notify.async_convert("ffmpeg", SOURCE)


# -- the proxy -------------------------------------------------------------


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(audio_notify.time, "monotonic", lambda: now[0])
    return now


async def _get(hass, name: str) -> web.Response:
    return await audio_notify.AudioClipView(hass).get(MagicMock(), name)


async def test_a_clip_is_served_whole_as_pcm(hass, clock):
    token = audio_notify.add_clip(hass, b"\x00\x01" * 10)
    response = await _get(hass, f"{token}.pcm")
    assert response.status == 200
    assert response.body == b"\x00\x01" * 10
    assert response.headers["Content-Type"] == "audio/L16;rate=24000;channels=1"
    assert response.headers["Cache-Control"] == "no-store"
    # The board retries once on a failed download, so a fetch keeps the clip.
    assert (await _get(hass, f"{token}.pcm")).status == 200


@pytest.mark.parametrize("name", ["{token}", "{token}.mp3", "../{token}.pcm", "unknown.pcm"])
async def test_anything_but_a_known_token_and_suffix_is_not_found(hass, clock, name):
    token = audio_notify.add_clip(hass, b"\x00\x00")
    with pytest.raises(web.HTTPNotFound):
        await _get(hass, name.format(token=token))


async def test_a_clip_expires(hass, clock):
    token = audio_notify.add_clip(hass, b"\x00\x00")
    clock[0] += audio_notify.CLIP_TTL_S
    with pytest.raises(web.HTTPNotFound):
        await _get(hass, f"{token}.pcm")


async def test_clearing_drops_every_clip(hass, clock):
    token = audio_notify.add_clip(hass, b"\x00\x00")
    audio_notify.async_clear(hass)
    with pytest.raises(web.HTTPNotFound):
        await _get(hass, f"{token}.pcm")


async def test_the_store_keeps_the_newest_clips(hass, clock):
    tokens = [audio_notify.add_clip(hass, b"\x00\x00") for _ in range(audio_notify.MAX_CLIPS + 2)]
    for token in tokens[:2]:
        with pytest.raises(web.HTTPNotFound):
            await _get(hass, f"{token}.pcm")
    for token in tokens[2:]:
        assert (await _get(hass, f"{token}.pcm")).status == 200


async def test_prepare_returns_a_local_pcm_url(hass, clock, monkeypatch, caplog):
    caplog.set_level("DEBUG", logger=audio_notify.__name__)
    get_url = MagicMock(return_value="http://192.0.2.10:8123")
    monkeypatch.setattr(audio_notify, "get_url", get_url)
    monkeypatch.setattr(audio_notify, "async_convert", AsyncMock(return_value=b"\x00\x00"))
    url = await audio_notify.async_prepare(hass, SOURCE)
    prefix = "http://192.0.2.10:8123/api/localthings/audio/"
    assert url.startswith(prefix) and url.endswith(".pcm")
    assert (await _get(hass, url.removeprefix(prefix))).body == b"\x00\x00"
    assert get_url.call_args.kwargs == {"allow_cloud": False, "prefer_external": False}
    assert "served from http://192.0.2.10:8123" in caplog.text
    assert url.removeprefix(prefix).removesuffix(".pcm") not in caplog.text


async def test_prepare_without_a_url_fails_before_converting(hass, monkeypatch):
    monkeypatch.setattr(audio_notify, "get_url", MagicMock(side_effect=NoURLAvailableError))
    convert = AsyncMock()
    monkeypatch.setattr(audio_notify, "async_convert", convert)
    with pytest.raises(HomeAssistantError) as err:
        await audio_notify.async_prepare(hass, SOURCE)
    assert err.value.translation_key == "audio_notification_no_url"
    convert.assert_not_called()


async def test_a_conversion_error_reaches_the_user(hass, monkeypatch):
    monkeypatch.setattr(audio_notify, "get_url", MagicMock(return_value="http://192.0.2.10"))
    monkeypatch.setattr(
        audio_notify,
        "async_convert",
        AsyncMock(side_effect=audio_notify.AudioConversionError("Invalid data")),
    )
    with pytest.raises(HomeAssistantError) as err:
        await audio_notify.async_prepare(hass, SOURCE)
    assert err.value.translation_key == "audio_notification_conversion_failed"
    assert err.value.translation_placeholders == {"error": "Invalid data"}


# -- the media player ------------------------------------------------------

_NOTIFICATION_FEATURES = (
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.MEDIA_ANNOUNCE
)


def test_the_soundbar_offers_play_media_with_the_receiver(hass, coordinator):
    player = _player(hass, coordinator)
    assert player.supported_features & _NOTIFICATION_FEATURES == _NOTIFICATION_FEATURES


def test_no_play_media_without_the_receiver(hass, bare_coordinator):
    player = _player(hass, bare_coordinator)
    assert not player.supported_features & _NOTIFICATION_FEATURES


def test_a_tv_keeps_its_own_play_media(hass, coordinator):
    player = _player(hass, coordinator, play_media=True)
    assert not player._plays_audio_notifications()
    assert not player.supported_features & MediaPlayerEntityFeature.MEDIA_ANNOUNCE


@pytest.fixture
def pipeline(monkeypatch, coordinator):
    prepare = AsyncMock(return_value=CLIP_URL)
    monkeypatch.setattr(mp.audio_notify, "async_prepare", prepare)
    play = AsyncMock()
    monkeypatch.setattr(coordinator.av, "async_play_audio_notification", play)
    monkeypatch.setattr(mp, "async_process_play_media_url", lambda hass, url: url)
    return prepare, play


async def test_a_media_source_clip_is_resolved_converted_and_sent(
    hass, coordinator, pipeline, monkeypatch
):
    prepare, play = pipeline
    resolved = MagicMock(url=SOURCE, mime_type="audio/mpeg")
    resolve_media = AsyncMock(return_value=resolved)
    monkeypatch.setattr(mp.media_source, "async_resolve_media", resolve_media)
    player = _player(hass, coordinator)
    await player.async_play_media(MediaType.MUSIC, "media-source://tts/x", announce=True)
    resolve_media.assert_awaited_once_with(hass, "media-source://tts/x", player.entity_id)
    prepare.assert_awaited_once_with(hass, SOURCE)
    play.assert_awaited_once_with(CLIP_URL)


async def test_a_plain_audio_url_is_sent(hass, coordinator, pipeline):
    prepare, play = pipeline
    await _player(hass, coordinator).async_play_media("audio/mpeg", SOURCE)
    prepare.assert_awaited_once_with(hass, SOURCE)
    play.assert_awaited_once_with(CLIP_URL)


@pytest.mark.parametrize("media_type", [MediaType.VIDEO, MediaType.IMAGE, "video/mp4", "app"])
async def test_other_media_types_are_refused(hass, coordinator, pipeline, media_type):
    prepare, play = pipeline
    with pytest.raises(ServiceValidationError) as err:
        await _player(hass, coordinator).async_play_media(media_type, SOURCE)
    assert err.value.translation_key == "audio_notification_media_type"
    prepare.assert_not_called()
    play.assert_not_called()


async def test_a_media_source_that_is_not_audio_is_refused(
    hass, coordinator, pipeline, monkeypatch
):
    prepare, _play = pipeline
    resolved = MagicMock(url="http://x/clip.mp4", mime_type="video/mp4")
    monkeypatch.setattr(mp.media_source, "async_resolve_media", AsyncMock(return_value=resolved))
    with pytest.raises(ServiceValidationError) as err:
        await _player(hass, coordinator).async_play_media(MediaType.MUSIC, "media-source://x")
    assert err.value.translation_key == "audio_notification_media_type"
    prepare.assert_not_called()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "/config/secrets.yaml", "concat:a|b"])
async def test_only_http_sources_reach_ffmpeg(hass, coordinator, pipeline, url):
    prepare, _play = pipeline
    with pytest.raises(ServiceValidationError) as err:
        await _player(hass, coordinator).async_play_media(MediaType.MUSIC, url)
    assert err.value.translation_key == "audio_notification_invalid_url"
    prepare.assert_not_called()


async def test_a_failed_preparation_sends_nothing(hass, coordinator, pipeline):
    prepare, play = pipeline
    prepare.side_effect = HomeAssistantError("no url")
    with pytest.raises(HomeAssistantError):
        await _player(hass, coordinator).async_play_media(MediaType.MUSIC, SOURCE)
    play.assert_not_called()


async def test_browsing_shows_audio_only(hass, coordinator, monkeypatch):
    browse = AsyncMock()
    monkeypatch.setattr(mp.media_source, "async_browse_media", browse)
    await _player(hass, coordinator).async_browse_media()
    content_filter = browse.call_args.kwargs["content_filter"]
    assert content_filter(MagicMock(media_content_type="audio/mpeg"))
    assert not content_filter(MagicMock(media_content_type="video/mp4"))
