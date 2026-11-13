"""The camera stream view: a camera's RTSP feed remuxed to one continuous
MPEG-TS stream, which the TV starts at once (HLS waits for segments)."""

from __future__ import annotations

import asyncio
import shutil
import sys
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.localthings import camera_stream

_SOURCE = "custom_components.localthings.camera_stream.async_stream_source"


class _FakeProc:
    def __init__(self, chunks: list[bytes], stderr: bytes = b"", *, ends: bool = True) -> None:
        self.stdout = asyncio.StreamReader()
        for chunk in chunks:
            self.stdout.feed_data(chunk)
        if ends:
            self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = None
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


async def _setup_http(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "http", {})
    camera_stream.async_register_view(hass)
    assert camera_stream.is_available(hass)


def test_ffmpeg_copies_video_to_mpegts_over_tcp() -> None:
    args = camera_stream.ffmpeg_args("ffmpeg", "rtsp://cam/stream")

    assert args[args.index("-rtsp_transport") + 1] == "tcp"
    assert args[args.index("-i") + 1] == "rtsp://cam/stream"
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-f") + 1] == "mpegts"
    assert args[-1] == "pipe:1"
    assert "nobuffer" in args
    # A short probe: ffmpeg otherwise trails a live camera by ~5 s.
    assert args[args.index("-analyzeduration") + 1] == "1000000"
    assert args[args.index("-max_interleave_delta") + 1] == "0"
    local = camera_stream.ffmpeg_args("ffmpeg", "/media/clip.mp4")
    assert "-rtsp_transport" not in local and "nobuffer" not in local
    assert "-analyzeduration" not in local


async def test_the_view_streams_ffmpegs_output(hass: HomeAssistant, hass_client) -> None:
    await _setup_http(hass)
    client = await hass_client()
    proc = _FakeProc([b"\x47" * 188, b"\x47" * 376])

    with (
        patch(_SOURCE, AsyncMock(return_value="rtsp://cam/stream")),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as spawn,
    ):
        resp = await client.get(camera_stream.stream_path("camera.door"))
        body = await resp.read()

    assert resp.status == 200
    assert resp.headers["Content-Type"] == "video/mp2t"
    assert "Content-Length" not in resp.headers
    assert body == b"\x47" * 564
    assert spawn.await_args is not None
    assert "rtsp://cam/stream" in spawn.await_args.args
    assert proc.killed


async def test_a_camera_that_stalls_ends_the_stream(hass: HomeAssistant, hass_client) -> None:
    await _setup_http(hass)
    client = await hass_client()
    proc = _FakeProc([b"\x47" * 188], ends=False)

    with (
        patch.object(camera_stream, "STALL_TIMEOUT_S", 0.2),
        patch(_SOURCE, AsyncMock(return_value="rtsp://cam/stream")),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        resp = await client.get(camera_stream.stream_path("camera.door"))
        body = await resp.read()

    assert body == b"\x47" * 188
    assert proc.killed


async def test_a_camera_that_sends_nothing_fails_the_request(
    hass: HomeAssistant, hass_client, caplog: pytest.LogCaptureFixture
) -> None:
    await _setup_http(hass)
    client = await hass_client()
    source = "rtsp://camera-user:camera-password@cam/stream?token=camera-token"

    async def _spawn(*args, **kwargs):
        proc = _FakeProc([], f"Error opening input file {source}.\n".encode(), ends=False)
        # FFmpeg writes its error before closing stdout.
        hass.loop.call_later(0.01, proc.stdout.feed_eof)
        return proc

    with (
        patch(_SOURCE, AsyncMock(return_value=source)),
        patch("asyncio.create_subprocess_exec", _spawn),
    ):
        resp = await client.get(camera_stream.stream_path("camera.door"))

    assert resp.status == 502
    assert "Error opening input file <redacted camera source>." in caplog.text
    assert "camera-password" not in caplog.text
    assert "camera-token" not in caplog.text


@pytest.mark.parametrize(("entity_id", "source"), [("camera.door", None), ("light.x", "rtsp://")])
async def test_no_stream_source_is_not_found(
    hass: HomeAssistant, hass_client, entity_id, source
) -> None:
    await _setup_http(hass)
    client = await hass_client()

    with patch(_SOURCE, AsyncMock(return_value=source)):
        resp = await client.get(camera_stream.stream_path(entity_id))

    assert resp.status == 404


async def test_the_tv_needs_the_signed_path(hass: HomeAssistant, hass_client_no_auth) -> None:
    await _setup_http(hass)
    client = await hass_client_no_auth()
    path = camera_stream.stream_path("camera.door")

    with (
        patch(_SOURCE, AsyncMock(return_value="rtsp://cam/stream")),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_FakeProc([b"x"]))),
    ):
        unsigned = await client.get(path)
        signed = await client.get(async_sign_path(hass, path, timedelta(minutes=5)))

    assert unsigned.status == 401
    assert signed.status == 200


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
async def test_real_ffmpeg_remuxes_a_clip(hass: HomeAssistant, hass_client, tmp_path) -> None:
    clip = tmp_path / "clip.mp4"
    make = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=320x180:rate=10", "-t", "2", "-c:v", "libx264", str(clip),
    )  # fmt: skip
    assert await make.wait() == 0
    await _setup_http(hass)
    client = await hass_client()

    with patch(_SOURCE, AsyncMock(return_value=str(clip))):
        resp = await client.get(camera_stream.stream_path("camera.door"))
        body = await resp.read()

    assert resp.status == 200
    # MPEG-TS: 188-byte packets, each starting with the 0x47 sync byte.
    assert len(body) > 188 * 10 and body[0] == 0x47 and body[188] == 0x47


async def test_stream_source_timeout_returns_gateway_timeout(
    hass: HomeAssistant, hass_client
) -> None:
    await _setup_http(hass)
    client = await hass_client()

    async def never_ready(*args):
        await asyncio.Event().wait()

    source = AsyncMock(side_effect=never_ready)
    hass.states.async_set("camera.door", "idle", {"supported_features": 2})
    with (
        patch.object(camera_stream, "SOURCE_TIMEOUT_S", 0.01),
        patch.dict(
            sys.modules,
            {
                "homeassistant.components.camera": SimpleNamespace(
                    async_get_stream_source=source, CameraEntityFeature=SimpleNamespace(STREAM=2)
                )
            },
        ),
        patch("asyncio.create_subprocess_exec", AsyncMock()) as spawn,
    ):
        response = await client.get(camera_stream.stream_path("camera.door"))
    assert response.status == 504
    source.assert_awaited_once()
    spawn.assert_not_awaited()


async def test_a_camera_without_the_stream_feature_has_no_source(hass: HomeAssistant) -> None:
    """An MJPEG camera reports its MJPEG URL as stream source; the TV can't
    play that, so the cast falls back to the snapshot instead."""
    hass.states.async_set("camera.mjpeg", "idle", {"supported_features": 0})
    source = AsyncMock(return_value="http://cam/mjpg/video.mjpg")
    with patch.dict(
        sys.modules,
        {
            "homeassistant.components.camera": SimpleNamespace(
                async_get_stream_source=source, CameraEntityFeature=SimpleNamespace(STREAM=2)
            )
        },
    ):
        assert await camera_stream.async_stream_source(hass, "camera.mjpeg") is None
    source.assert_not_awaited()
