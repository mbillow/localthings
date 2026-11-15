"""Casting images, videos, cameras and YouTube to the TV.

Without a prompt where the TV allows it (Gallery, the YouTube app; a camera
as live DASH); otherwise, or with extra.ask, through /content/renderer, where
the TV asks on screen first: an item of host + path + mimetype opens an image
(host carries the scheme), a YouTube item plays path[1:] as the video id, and
a video file or HLS stream plays once sent as video/*. A camera with only
MJPEG (which the TV can't play) shows its snapshot.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature, MediaType
from homeassistant.components.media_source import PlayMedia
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.capabilities import tv as tv_caps
from tests.localthings.test_soundbar import _mock_entry, _setup_soundbar
from tests.localthings.test_tv_media_player_tu7172 import _entry, _setup_tv
from tests.localthings.test_tv_notify import _setup_tv as _setup_tv_with_native_app


@pytest.fixture(autouse=True)
def _casting_on():
    """These TVs have camera casting on (CONF_CAMERA_CASTING, off by default)."""
    with patch.object(
        LocalThingsCoordinator, "camera_casting_enabled", new_callable=PropertyMock
    ) as enabled:
        enabled.return_value = True
        yield


_RENDERER = ["content", "renderer"]
_SENDER = {"deviceName": "Home Assistant", "userName": "Home Assistant", "userId": "home-assistant"}


def _item(host: str, path: str, mime: str) -> dict:
    return {
        "x.com.samsung.action": "play",
        "x.com.samsung.contents": [
            {
                "x.com.samsung.host": host,
                "x.com.samsung.path": path,
                "x.com.samsung.mimetype": mime,
            }
        ],
        "x.com.samsung.provider.user": _SENDER,
    }


@pytest.mark.parametrize(
    "value",
    [
        "aqz-KE-bpKQ",
        "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
        "https://www.youtube.com/watch?list=x&v=aqz-KE-bpKQ&t=10",
        "youtube.com/watch?v=aqz-KE-bpKQ",
        "https://m.youtube.com/watch?v=aqz-KE-bpKQ",
        "https://youtu.be/aqz-KE-bpKQ?t=3",
        "https://www.youtube.com/shorts/aqz-KE-bpKQ",
        "https://www.youtube.com/embed/aqz-KE-bpKQ",
    ],
)
def test_youtube_ids_are_found_in_every_link_form(value) -> None:
    assert tv_caps.youtube_id(value) == "aqz-KE-bpKQ"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "hello",
        "https://example.com/watch?v=aqz-KE-bpKQ",
        "https://youtu.be/short",
        "aqz-KE-bpK!",
    ],
)
def test_anything_else_is_no_youtube_id(value) -> None:
    assert tv_caps.youtube_id(value) is None


def test_an_image_url_splits_into_host_and_path() -> None:
    assert tv_caps.image_body("https://img.example.com:8443/a/b.jpg?w=1&h=2") == _item(
        "https://img.example.com:8443", "/a/b.jpg?w=1&h=2", "image/jpeg"
    )
    assert tv_caps.image_body("ftp://example.com/a.jpg") is None
    assert tv_caps.image_body("not a url") is None


async def _play(hass: HomeAssistant, media_type: str, media_id: str) -> None:
    player_id = hass.states.async_all("media_player")[0].entity_id
    await hass.services.async_call(
        "media_player",
        "play_media",
        {"entity_id": player_id, "media_content_type": media_type, "media_content_id": media_id},
        blocking=True,
    )


@pytest.mark.parametrize(
    ("media_type", "media_id"),
    [
        ("youtube", "aqz-KE-bpKQ"),
        ("video", "https://youtu.be/aqz-KE-bpKQ"),
        ("url", "https://www.youtube.com/watch?v=aqz-KE-bpKQ"),
    ],
)
async def test_youtube_links_start_the_youtube_app(
    hass: HomeAssistant, media_type, media_id
) -> None:
    session = await _setup_tv(hass, _entry(hass))

    await _play(hass, media_type, media_id)

    assert (
        ["sec", "tv", "launchapp"],
        {
            "x.com.samsung.tv.targetAppId": "111299001912",
            "x.com.samsung.tv.targetAppParam": "#play?v=aqz-KE-bpKQ",
        },
    ) in session.writes


@pytest.mark.parametrize("media_type", ["image", "url", "image/png"])
async def test_an_image_url_is_shown(hass: HomeAssistant, media_type) -> None:
    session = await _setup_tv(hass, _entry(hass))
    player = next(iter(hass.data["media_player"].entities))
    assert player.supported_features & MediaPlayerEntityFeature.BROWSE_MEDIA

    await player.async_play_media(media_type, "https://upload.wikimedia.org/x/flower.jpg")

    assert (
        _RENDERER,
        _item("https://upload.wikimedia.org", "/x/flower.jpg", "image/jpeg"),
    ) in session.writes


@pytest.mark.parametrize("ask", [False, True])
async def test_absolute_home_assistant_media_and_icons_are_signed(
    hass: HomeAssistant, ask: bool
) -> None:
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))
    hass.config.internal_url = "http://192.0.2.2:8123"
    url = "http://192.0.2.2:8123/api/camera_proxy/camera.door"

    await player.async_play_media("image", url, extra={"ask": ask, "icon": url})

    if ask:
        body = next(body for path, body in session.writes if path == _RENDERER)
        (item,) = body["x.com.samsung.contents"]
        assert item["x.com.samsung.path"].startswith("/api/camera_proxy/camera.door?authSig=")
        assert body["x.com.samsung.provider.user"]["iconUrl"].startswith(url + "?authSig=")
    else:
        body = next(body for path, body in session.writes if path == _NATIVE_APP)
        (bundle,) = body["x.com.samsung.tv.bundleParam"]
        assert bundle["x.com.samsung.tv.bundle.value"].startswith(url + "?authSig=")


@pytest.mark.parametrize(
    ("media_type", "media_id", "mimetype"),
    [
        ("video", "https://example.com/v/movie.mp4", "video/mp4"),
        ("url", "https://example.com/v/movie.mkv", "video/x-matroska"),
        ("url", "https://example.com/v/movie.webm?x=1", "video/webm"),
        ("video/webm", "https://example.com/v/movie", "video/webm"),
        ("video", "https://example.com/v/movie", "video/mp4"),
    ],
)
async def test_a_video_file_is_played(hass: HomeAssistant, media_type, media_id, mimetype) -> None:
    session = await _setup_tv(hass, _entry(hass))
    player = next(iter(hass.data["media_player"].entities))

    await player.async_play_media(media_type, media_id)

    path = media_id.removeprefix("https://example.com")
    assert (_RENDERER, _item("https://example.com", path, mimetype)) in session.writes


@pytest.mark.parametrize(
    ("media_type", "media_id"),
    [
        ("url", "https://example.com/live/index.m3u8"),
        ("video", "https://example.com/live/index.m3u8?token=1"),
        ("application/vnd.apple.mpegurl", "https://example.com/live/stream"),
        ("application/x-mpegURL", "https://example.com/live/stream"),
    ],
)
async def test_an_hls_stream_is_sent_as_video(hass: HomeAssistant, media_type, media_id) -> None:
    session = await _setup_tv(hass, _entry(hass))
    player = next(iter(hass.data["media_player"].entities))

    await player.async_play_media(media_type, media_id)

    path = media_id.removeprefix("https://example.com")
    assert (_RENDERER, _item("https://example.com", path, "video/mp4")) in session.writes


async def test_a_camera_plays_its_hls_stream(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass, _entry(hass))
    player = next(iter(hass.data["media_player"].entities))
    playlist = "/api/hls/abc/master_playlist.m3u8"

    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=False),
        patch("custom_components.localthings.camera_stream.is_available", return_value=False),
        patch(
            "homeassistant.components.media_source.async_resolve_media",
            AsyncMock(return_value=PlayMedia(playlist, "application/vnd.apple.mpegurl")),
        ),
        patch(
            "custom_components.localthings.media_player.async_process_play_media_url",
            lambda hass, url: f"http://192.0.2.2:8123{url}?authSig=x",
        ),
    ):
        await player.async_play_media(
            "application/vnd.apple.mpegurl", "media-source://camera/camera.door"
        )

    body = _item("http://192.0.2.2:8123", f"{playlist}?authSig=x", "video/mp4")
    # The prompt shows the camera's snapshot as its icon.
    body["x.com.samsung.provider.user"] = {
        **_SENDER,
        "iconUrl": "http://192.0.2.2:8123/api/camera_proxy/camera.door?authSig=x",
    }
    assert (_RENDERER, body) in session.writes


async def test_extra_sets_the_prompts_title_and_icon(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass, _entry(hass))

    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": hass.states.async_all("media_player")[0].entity_id,
            "media_content_type": "video",
            "media_content_id": "https://example.com/v/door.mp4",
            "extra": {"title": "Someone is at the door", "icon": "https://example.com/door.jpg"},
        },
        blocking=True,
    )

    body = _item("https://example.com", "/v/door.mp4", "video/mp4")
    body["x.com.samsung.provider.user"] = {
        **_SENDER,
        "userName": "Someone is at the door",
        "iconUrl": "https://example.com/door.jpg",
    }
    assert (_RENDERER, body) in session.writes


async def test_an_icon_that_isnt_a_web_link_is_refused(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass, _entry(hass))
    player = next(iter(hass.data["media_player"].entities))

    with pytest.raises(ServiceValidationError) as err:
        await player.async_play_media(
            "video", "https://example.com/v/door.mp4", extra={"icon": "ftp://example.com/a.jpg"}
        )
    assert err.value.translation_key == "invalid_media_url"
    assert not any(path == _RENDERER for path, _body in session.writes)


async def test_an_mjpeg_camera_shows_its_snapshot(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass, _entry(hass))
    player = next(iter(hass.data["media_player"].entities))

    with (
        patch(
            "homeassistant.components.media_source.async_resolve_media",
            AsyncMock(return_value=PlayMedia("/api/camera_proxy_stream/camera.door", "image/jpeg")),
        ),
        patch(
            "custom_components.localthings.media_player.async_process_play_media_url",
            lambda hass, url: f"http://192.0.2.2:8123{url}?authSig=x",
        ),
    ):
        await player.async_play_media("image/jpeg", "media-source://camera/camera.door")

    snapshot = "/api/camera_proxy/camera.door?authSig=x"
    body = _item("http://192.0.2.2:8123", snapshot, "image/jpeg")
    body["x.com.samsung.provider.user"] = {
        **_SENDER,
        "iconUrl": f"http://192.0.2.2:8123{snapshot}",
    }
    assert (_RENDERER, body) in session.writes


async def test_a_media_source_image_is_shown_from_home_assistant(hass: HomeAssistant) -> None:
    session = await _setup_tv(hass, _entry(hass))
    player = next(iter(hass.data["media_player"].entities))

    with (
        patch(
            "homeassistant.components.media_source.async_resolve_media",
            AsyncMock(return_value=PlayMedia("/media/local/cam.jpg?authSig=x", "image/jpeg")),
        ),
        patch(
            "custom_components.localthings.media_player.async_process_play_media_url",
            lambda hass, url: f"http://192.0.2.2:8123{url}",
        ),
    ):
        await player.async_play_media("image/jpeg", "media-source://media_source/local/cam.jpg")

    assert (
        _RENDERER,
        _item("http://192.0.2.2:8123", "/media/local/cam.jpg?authSig=x", "image/jpeg"),
    ) in session.writes


@pytest.mark.parametrize(
    ("media_type", "media_id", "key"),
    [
        ("video", "https://example.com/live/manifest.mpd", "invalid_media_url"),
        ("url", "https://example.com/live/manifest.mpd", "invalid_media_url"),
        ("multipart/x-mixed-replace", "https://example.com/mjpeg", "unsupported_media_type"),
        ("video", "nope", "invalid_media_url"),
        ("image", "ftp://example.com/a.jpg", "invalid_media_url"),
        ("music", "https://example.com/a.mp3", "unsupported_media_type"),
    ],
)
async def test_what_the_tv_cannot_show_is_refused(
    hass: HomeAssistant, media_type, media_id, key
) -> None:
    session = await _setup_tv(hass, _entry(hass))
    player = next(iter(hass.data["media_player"].entities))

    with pytest.raises(ServiceValidationError) as err:
        await player.async_play_media(media_type, media_id)
    assert err.value.translation_key == key
    assert not any(path == _RENDERER for path, _body in session.writes)


async def test_the_browser_offers_images_videos_and_cameras(hass: HomeAssistant) -> None:
    await _setup_tv(hass, _entry(hass))
    player = next(iter(hass.data["media_player"].entities))

    with patch("homeassistant.components.media_source.async_browse_media", AsyncMock()) as browse:
        await player.async_browse_media(MediaType.IMAGE, None)

    assert browse.await_args is not None
    content_filter = browse.await_args.kwargs["content_filter"]
    assert content_filter(type("I", (), {"media_content_type": "image/jpeg"})())
    assert content_filter(type("I", (), {"media_content_type": "video/mp4"})())
    assert content_filter(type("I", (), {"media_content_type": "application/vnd.apple.mpegurl"})())
    assert not content_filter(type("I", (), {"media_content_type": "audio/mpeg"})())


async def test_the_soundbar_casts_nothing(hass: HomeAssistant) -> None:
    session = await _setup_soundbar(hass, _mock_entry(hass))
    player = next(iter(hass.data["media_player"].entities))

    with pytest.raises(ServiceValidationError):
        await player.async_play_media("youtube", "aqz-KE-bpKQ")
    assert not any(path == _RENDERER for path, _body in session.writes)


async def test_a_camera_with_a_stream_source_plays_the_direct_stream(
    hass: HomeAssistant,
) -> None:
    session = await _setup_tv(hass, _entry(hass))
    player = next(iter(hass.data["media_player"].entities))
    path = "/api/localthings/camera_stream/camera.door"

    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=True),
        patch("custom_components.localthings.camera_stream.is_available", return_value=True),
        patch(
            "custom_components.localthings.camera_stream.async_stream_source",
            AsyncMock(return_value=True),
        ),
        patch("homeassistant.components.media_source.async_resolve_media") as resolve,
        patch(
            "custom_components.localthings.media_player.async_process_play_media_url",
            lambda hass, url: f"http://192.0.2.2:8123{url}?authSig=x",
        ),
    ):
        await player.async_play_media("video", "media-source://camera/camera.door")

    # No HLS: the TV gets the continuous MPEG-TS stream.
    resolve.assert_not_called()
    body = _item("http://192.0.2.2:8123", f"{path}?authSig=x", "video/mp2t")
    body["x.com.samsung.provider.user"] = {
        **_SENDER,
        "iconUrl": "http://192.0.2.2:8123/api/camera_proxy/camera.door?authSig=x",
    }
    assert (_RENDERER, body) in session.writes


_NATIVE_APP = ["sec", "tv", "launchnativeapp"]


def _gallery(kind: str, url: str) -> tuple[list[str], dict]:
    return (
        _NATIVE_APP,
        {
            "x.com.samsung.tv.targetAppId": "com.samsung.tv.gallery",
            "x.com.samsung.tv.bundleParam": [
                {
                    "x.com.samsung.tv.bundle.key": f"smart_thing_uri_{kind}0",
                    "x.com.samsung.tv.bundle.value": url,
                }
            ],
        },
    )


@pytest.mark.parametrize(
    ("media_type", "media_id", "kind"),
    [
        ("video", "https://example.com/v/door.mp4", "video"),
        ("image", "https://example.com/door.jpg", "image"),
        ("url", "https://example.com/door.jpg", "image"),
    ],
)
async def test_images_and_videos_open_gallery_without_a_prompt(
    hass: HomeAssistant, media_type, media_id, kind
) -> None:
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))

    await player.async_play_media(media_type, media_id, extra={"title": "ignored"})

    assert _gallery(kind, media_id) in session.writes
    assert not any(path == _RENDERER for path, _body in session.writes)


async def test_youtube_starts_in_the_youtube_app_without_a_prompt(hass: HomeAssistant) -> None:
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))

    await player.async_play_media("url", "https://youtu.be/aqz-KE-bpKQ")

    assert (
        ["sec", "tv", "launchapp"],
        {
            "x.com.samsung.tv.targetAppId": "111299001912",
            "x.com.samsung.tv.targetAppParam": "#play?v=aqz-KE-bpKQ",
        },
    ) in session.writes
    assert not any(path == _RENDERER for path, _body in session.writes)


async def test_youtube_with_ask_shows_the_prompt(hass: HomeAssistant) -> None:
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))

    await player.async_play_media("youtube", "aqz-KE-bpKQ", extra={"ask": True})

    assert (
        _RENDERER,
        _item("https://www.youtube.com", "/aqz-KE-bpKQ", "video/youtube"),
    ) in session.writes
    assert not any(path == ["sec", "tv", "launchapp"] for path, _body in session.writes)


async def test_a_camera_opens_as_live_dash_in_gallery(hass: HomeAssistant) -> None:
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))

    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=True),
        patch(
            "custom_components.localthings.camera_stream.async_stream_source",
            AsyncMock(return_value="rtsp://cam/stream"),
        ),
        patch(
            "custom_components.localthings.camera_dash.async_start",
            AsyncMock(return_value="tok"),
        ) as start,
    ):
        hass.config.internal_url = "http://192.0.2.2:8123"
        await player.async_play_media("video", "media-source://camera/camera.door")

    start.assert_awaited_once()
    assert start.await_args is not None
    assert start.await_args.args[:2] == (hass, "camera.door")
    # Unsigned: the token is the credential (camera_dash.manifest_url).
    url = "http://192.0.2.2:8123/api/localthings/camera_dash/tok/manifest.mpd"
    assert session.writes.count(_gallery("video", url)) == 1

    # A slow camera: camera_dash opens the playlist on the TV again once ready.
    assert start.await_args is not None
    relaunch = start.await_args.args[2]
    await relaunch("tok")
    assert session.writes.count(_gallery("video", url)) == 2


async def test_a_failed_gallery_launch_ends_the_camera_session(hass: HomeAssistant) -> None:
    from homeassistant.exceptions import HomeAssistantError

    await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))
    hass.config.internal_url = "http://192.0.2.2:8123"

    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=True),
        patch(
            "custom_components.localthings.camera_stream.async_stream_source",
            AsyncMock(return_value="rtsp://cam/stream"),
        ),
        patch(
            "custom_components.localthings.camera_dash.async_start",
            AsyncMock(return_value="tok"),
        ),
        patch("custom_components.localthings.camera_dash.async_release", AsyncMock()) as release,
        patch.object(
            player.coordinator.av,
            "async_launch_gallery",
            AsyncMock(side_effect=HomeAssistantError("TV off")),
        ),
        pytest.raises(HomeAssistantError),
    ):
        await player.async_play_media("video", "media-source://camera/camera.door")

    release.assert_awaited_once()
    assert release.await_args is not None
    assert release.await_args.args[:2] == (hass, "tok")
    assert player.coordinator.playback.camera_cast is None


async def test_a_failed_second_tv_cast_keeps_the_first_tvs_stream(hass: HomeAssistant) -> None:
    from custom_components.localthings import camera_dash
    from custom_components.localthings.coordinator import LocalThingsCoordinator
    from custom_components.localthings.media_player import LocalThingsMediaPlayer
    from tests.localthings.test_camera_dash import _FakeProc, _setup_http

    await _setup_http(hass)
    await _setup_tv_with_native_app(hass)
    first = next(iter(hass.data["media_player"].entities))
    coordinator = LocalThingsCoordinator(hass, first.coordinator._entry)
    coordinator._identity = first.coordinator._identity
    second = LocalThingsMediaPlayer(coordinator, first._bound)
    second.hass = hass
    hass.config.internal_url = "http://192.0.2.2:8123"
    proc = _FakeProc()

    with (
        patch(
            "custom_components.localthings.camera_stream.async_stream_source",
            AsyncMock(return_value="rtsp://cam/stream"),
        ),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as spawn,
    ):
        await first.async_play_media("video", "media-source://camera/camera.door")
        assert first.coordinator.playback.camera_cast is not None
        token = first.coordinator.playback.camera_cast[0]
        await first.async_play_media("video", "media-source://camera/camera.door")
        assert first.coordinator.playback.camera_cast is not None
        assert first.coordinator.playback.camera_cast[0] == token
        with (
            patch.object(
                second.coordinator.av,
                "async_launch_gallery",
                AsyncMock(side_effect=HomeAssistantError("TV off")),
            ),
            pytest.raises(HomeAssistantError),
        ):
            await second.async_play_media("video", "media-source://camera/camera.door")

    spawn.assert_awaited_once()
    assert token in hass.data[camera_dash._DATA_SESSIONS]
    assert not proc.killed
    await first.async_media_stop()
    assert token not in hass.data[camera_dash._DATA_SESSIONS]
    assert proc.killed


@pytest.mark.parametrize("command", ["media", "source", "off", "stop", "remove"])
async def test_a_camera_retry_cannot_override_newer_playback(
    hass: HomeAssistant, command: str
) -> None:
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))
    hass.config.internal_url = "http://192.0.2.2:8123"
    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=True),
        patch(
            "custom_components.localthings.camera_stream.async_stream_source",
            AsyncMock(return_value="rtsp://cam/stream"),
        ),
        patch(
            "custom_components.localthings.camera_dash.async_start",
            AsyncMock(return_value="tok"),
        ) as start,
    ):
        await player.async_play_media("video", "media-source://camera/camera.door")

    assert start.await_args is not None
    relaunch = start.await_args.args[2]
    if command == "media":
        await player.async_play_media("image", "https://example.com/new.jpg")
    elif command == "source":
        await player.async_select_source("HDMI 1")
    elif command == "off":
        await player.async_turn_off()
    elif command == "stop":
        await player.async_media_stop()
    else:
        await player.async_remove()
    writes = list(session.writes)

    await relaunch("tok")

    assert session.writes == writes


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("operation", ["app", "search", "remote_home", "remote_off"])
async def test_playback_changes_serialize_with_camera_retry(
    hass: HomeAssistant, fails: bool, operation: str
) -> None:
    await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))
    select = next(e for e in hass.data["select"].entities if e._bound.desc.key == "open_app")
    hass.config.internal_url = "http://192.0.2.2:8123"
    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=True),
        patch(
            "custom_components.localthings.camera_stream.async_stream_source",
            AsyncMock(return_value="rtsp://cam/stream"),
        ),
        patch(
            "custom_components.localthings.camera_dash.async_start", AsyncMock(return_value="tok")
        ) as start,
    ):
        await player.async_play_media("video", "media-source://camera/camera.door")
    assert start.await_args is not None
    relaunch = start.await_args.args[2]
    started, finish = asyncio.Event(), asyncio.Event()

    async def send(*args, **kwargs):
        started.set()
        await finish.wait()
        if fails:
            raise HomeAssistantError("write failed")

    with (
        patch.object(
            player.coordinator,
            "_async_put" if operation == "search" else "async_send_command",
            AsyncMock(side_effect=send),
        ),
        patch.object(player.coordinator.av, "async_launch_gallery", AsyncMock()) as launch,
        patch("custom_components.localthings.camera_dash.async_release", AsyncMock()) as release,
    ):
        remote = next(iter(hass.data["remote"].entities))
        actions = {
            "app": lambda: select.async_select_option("home"),
            "search": lambda: player.coordinator.av.async_search("youtube", "cats"),
            "remote_home": lambda: remote.async_send_command(["home"]),
            "remote_off": remote.async_turn_off,
        }
        selection = asyncio.create_task(actions[operation]())
        try:
            await started.wait()
            retry = asyncio.create_task(relaunch("tok"))
            await asyncio.sleep(0)
            assert not retry.done()
        finally:
            finish.set()
        if fails:
            with pytest.raises(HomeAssistantError):
                await selection
        else:
            await selection
        await retry
        if fails:
            launch.assert_awaited_once()
            release.assert_not_awaited()
        else:
            launch.assert_not_awaited()
            release.assert_awaited_once_with(hass, "tok", relaunch)


async def test_a_camera_retry_queued_behind_new_media_stays_cancelled(hass: HomeAssistant) -> None:
    await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))
    hass.config.internal_url = "http://192.0.2.2:8123"
    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=True),
        patch(
            "custom_components.localthings.camera_stream.async_stream_source",
            AsyncMock(return_value="rtsp://cam/stream"),
        ),
        patch(
            "custom_components.localthings.camera_dash.async_start",
            AsyncMock(return_value="tok"),
        ) as start,
    ):
        await player.async_play_media("video", "media-source://camera/camera.door")
    assert start.await_args is not None
    relaunch = start.await_args.args[2]
    started, finish = asyncio.Event(), asyncio.Event()

    async def _launch(kind, url):
        started.set()
        await finish.wait()

    with patch.object(
        player.coordinator.av, "async_launch_gallery", AsyncMock(side_effect=_launch)
    ) as launch:
        newer = asyncio.create_task(player.async_play_media("image", "https://example.com/new.jpg"))
        try:
            await started.wait()
            retry = asyncio.create_task(relaunch("tok"))
            await asyncio.sleep(0)
            assert not retry.done()
        finally:
            finish.set()
            await newer
        await retry

    launch.assert_awaited_once_with("image", "https://example.com/new.jpg")


@pytest.mark.parametrize(
    "failure", ["ask", "channel", "source", "off", "stop", "same_camera", "new_camera"]
)
async def test_failed_replacement_preserves_the_active_camera(
    hass: HomeAssistant, failure: str
) -> None:
    from custom_components.localthings import camera_dash
    from tests.localthings.test_camera_dash import _FakeProc, _setup_http

    await _setup_tv_with_native_app(hass)
    await _setup_http(hass)
    player = next(iter(hass.data["media_player"].entities))
    hass.config.internal_url = "http://192.0.2.2:8123"
    active, replacement = _FakeProc(), _FakeProc()
    with (
        patch(
            "custom_components.localthings.camera_stream.async_stream_source",
            AsyncMock(return_value="rtsp://cam/stream"),
        ),
        patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=[active, replacement])),
    ):
        await player.async_play_media("video", "media-source://camera/camera.door")
        previous = player.coordinator.playback.camera_cast
        assert previous is not None
        token, relaunch = previous
        with (
            patch.object(
                player.coordinator.av,
                "async_launch_gallery",
                AsyncMock(side_effect=HomeAssistantError("TV unavailable")),
            ),
            patch.object(
                player.coordinator,
                "async_send_command",
                AsyncMock(side_effect=HomeAssistantError("TV unavailable")),
            ),
            pytest.raises(HomeAssistantError),
        ):
            if failure == "ask":
                await player.async_play_media(
                    "image", "https://example.com/x.jpg", extra={"ask": "maybe"}
                )
            elif failure == "channel":
                await player.async_play_media("channel", "invalid")
            elif failure == "source":
                await player.async_select_source("HDMI")
            elif failure == "off":
                await player.async_turn_off()
            elif failure == "stop":
                await player.async_media_stop()
            else:
                camera = "camera.door" if failure == "same_camera" else "camera.other"
                await player.async_play_media("video", f"media-source://camera/{camera}")

        assert player.coordinator.playback.camera_cast == previous
        assert not active.killed
        assert set(camera_dash._sessions(hass)) == {token}
        assert camera_dash._sessions(hass)[token].relaunches == {relaunch}
        if failure == "new_camera":
            assert replacement.killed
        with patch.object(player.coordinator.av, "async_launch_gallery", AsyncMock()) as launch:
            await relaunch(token)
        launch.assert_awaited_once_with("video", camera_dash.manifest_url(hass, token))
        await player.async_media_stop()
        assert active.killed


async def test_a_camera_with_ask_keeps_the_continuous_stream_and_the_prompt(
    hass: HomeAssistant,
) -> None:
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))

    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=True),
        patch("custom_components.localthings.camera_dash.async_start", AsyncMock()) as start,
        patch("custom_components.localthings.camera_stream.is_available", return_value=True),
        patch(
            "custom_components.localthings.camera_stream.async_stream_source",
            AsyncMock(return_value="rtsp://cam/stream"),
        ),
        patch(
            "custom_components.localthings.media_player.async_process_play_media_url",
            lambda hass, url: f"http://192.0.2.2:8123{url}?authSig=x",
        ),
    ):
        await player.async_play_media(
            "video", "media-source://camera/camera.door", extra={"ask": True}
        )

    start.assert_not_awaited()
    assert any(
        path == _RENDERER
        and "/api/localthings/camera_stream/camera.door"
        in body["x.com.samsung.contents"][0]["x.com.samsung.path"]
        for path, body in session.writes
    )


async def test_a_camera_whose_dash_cannot_start_plays_home_assistants_stream(
    hass: HomeAssistant,
) -> None:
    """camera_stream needs the same ffmpeg DASH just failed to run."""
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))
    playlist = "/api/hls/abc/master_playlist.m3u8"

    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=True),
        patch(
            "custom_components.localthings.camera_dash.async_start", AsyncMock(return_value=None)
        ) as start,
        patch(
            "custom_components.localthings.camera_stream.async_stream_source",
            AsyncMock(return_value="rtsp://cam/stream"),
        ),
        patch(
            "homeassistant.components.media_source.async_resolve_media",
            AsyncMock(return_value=PlayMedia(playlist, "application/vnd.apple.mpegurl")),
        ) as resolve,
        patch(
            "custom_components.localthings.media_player.async_process_play_media_url",
            lambda hass, url: f"http://192.0.2.2:8123{url}?authSig=x",
        ),
    ):
        await player.async_play_media("video", "media-source://camera/camera.door")

    start.assert_awaited_once()
    resolve.assert_awaited_once()
    assert not any("camera_stream" in str(body) for _path, body in session.writes)


async def test_ask_shows_the_prompt_with_its_title_instead(hass: HomeAssistant) -> None:
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))

    await player.async_play_media(
        "video",
        "https://example.com/v/door.mp4",
        extra={"ask": True, "title": "Someone is at the door", "icon": "https://example.com/i.png"},
    )

    body = tv_caps.with_sender(
        _item("https://example.com", "/v/door.mp4", "video/mp4"),
        "Someone is at the door",
        "https://example.com/i.png",
    )
    assert (_RENDERER, body) in session.writes
    assert not any(path == _NATIVE_APP for path, _body in session.writes)


@pytest.mark.parametrize("ask", [False, "false", "off", 0])
async def test_ask_off_still_opens_gallery(hass: HomeAssistant, ask) -> None:
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))

    await player.async_play_media("image", "https://example.com/door.jpg", extra={"ask": ask})

    assert _gallery("image", "https://example.com/door.jpg") in session.writes


async def test_ask_must_be_a_boolean(hass: HomeAssistant) -> None:
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))

    with pytest.raises(ServiceValidationError) as err:
        await player.async_play_media(
            "image", "https://example.com/door.jpg", extra={"ask": "maybe"}
        )

    assert err.value.translation_key == "invalid_ask"
    assert not any(path in (_RENDERER, _NATIVE_APP) for path, _body in session.writes)


async def test_hls_goes_through_the_renderer_because_gallery_refuses_it(
    hass: HomeAssistant,
) -> None:
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))

    await player.async_play_media("url", "https://example.com/live/index.m3u8")

    assert (
        _RENDERER,
        _item("https://example.com", "/live/index.m3u8", "video/mp4"),
    ) in session.writes
    assert not any(path == _NATIVE_APP for path, _body in session.writes)


def test_cast_urls_are_logged_without_credentials() -> None:
    from custom_components.localthings.av import _without_credentials

    assert (
        _without_credentials(
            "http://ha:8123/api/localthings/camera_dash/DSqd4GFkRuOG_ZG/manifest.mpd"
        )
        == "http://ha:8123/api/localthings/camera_dash/DSqd4G…/manifest.mpd"
    )
    assert _without_credentials("http://ha/api/x?a=1&authSig=abc.def&b=2") == (
        "http://ha/api/x?a=1&authSig=…&b=2"
    )


@pytest.mark.parametrize("command", ["volume_up", "mute", "source", "KEY_HDMI1", "power"])
async def test_remote_keys_preserve_or_release_the_camera(
    hass: HomeAssistant, command: str
) -> None:
    await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))
    remote = next(iter(hass.data["remote"].entities))
    hass.config.internal_url = "http://192.0.2.2:8123"
    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=True),
        patch(
            "custom_components.localthings.camera_stream.async_stream_source",
            AsyncMock(return_value="rtsp://cam/stream"),
        ),
        patch(
            "custom_components.localthings.camera_dash.async_start", AsyncMock(return_value="tok")
        ) as start,
    ):
        await player.async_play_media("video", "media-source://camera/camera.door")
    assert start.await_args is not None
    relaunch = start.await_args.args[2]
    with (
        patch.object(player.coordinator.av, "async_launch_gallery", AsyncMock()) as launch,
        patch("custom_components.localthings.camera_dash.async_release", AsyncMock()) as release,
    ):
        await remote.async_send_command([command])
        await relaunch("tok")
    if command in {"volume_up", "mute"}:
        launch.assert_awaited_once()
        release.assert_not_awaited()
    else:
        launch.assert_not_awaited()
        release.assert_awaited_once_with(hass, "tok", relaunch)


async def test_source_timeout_does_not_retry_camera_resolution(hass: HomeAssistant) -> None:
    from custom_components.localthings import camera_stream

    await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))

    async def never_ready(*args):
        await asyncio.Event().wait()

    source = AsyncMock(side_effect=never_ready)
    hass.states.async_set("camera.door", "idle", {"supported_features": 2})
    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=True),
        patch("custom_components.localthings.camera_stream.is_available", return_value=True),
        patch.object(camera_stream, "SOURCE_TIMEOUT_S", 0.01),
        patch.dict(
            sys.modules,
            {
                "homeassistant.components.camera": SimpleNamespace(
                    async_get_stream_source=source, CameraEntityFeature=SimpleNamespace(STREAM=2)
                )
            },
        ),
        patch("custom_components.localthings.camera_dash.async_start", AsyncMock()) as start,
        patch("homeassistant.components.media_source.async_resolve_media", AsyncMock()) as resolve,
        pytest.raises(HomeAssistantError, match="too long"),
    ):
        await player.async_play_media("video", "media-source://camera/camera.door")
    source.assert_awaited_once()
    start.assert_not_awaited()
    resolve.assert_not_awaited()


async def test_camera_media_source_fallback_is_also_bounded(hass: HomeAssistant) -> None:
    from custom_components.localthings import camera_stream

    await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))

    async def never_ready(*args):
        await asyncio.Event().wait()

    with (
        patch("custom_components.localthings.camera_dash.is_available", return_value=False),
        patch("custom_components.localthings.camera_stream.is_available", return_value=False),
        patch.object(camera_stream, "SOURCE_TIMEOUT_S", 0.01),
        patch(
            "homeassistant.components.media_source.async_resolve_media", side_effect=never_ready
        ) as resolve,
        pytest.raises(HomeAssistantError, match="too long"),
    ):
        await player.async_play_media("video", "media-source://camera/camera.door")
    resolve.assert_awaited_once()


@pytest.mark.parametrize("ask", [False, True])
async def test_a_dash_link_opens_in_gallery_but_never_through_the_prompt(
    hass: HomeAssistant, ask: bool
) -> None:
    session = await _setup_tv_with_native_app(hass)
    player = next(iter(hass.data["media_player"].entities))
    url = "https://example.com/live/manifest.mpd"

    if ask:
        with pytest.raises(ServiceValidationError):
            await player.async_play_media("video", url, extra={"ask": True})
        assert not any(path == _RENDERER for path, _body in session.writes)
    else:
        await player.async_play_media("video", url)
        assert _gallery("video", url) in session.writes
