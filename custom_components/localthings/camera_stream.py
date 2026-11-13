"""Camera casting through a signed, continuous MPEG-TS stream.

Used when Gallery's DASH launch is unavailable or the cast requests a TV
prompt. ffmpeg copies video and encodes audio as AAC; disconnects end the
process. Latency measurements: docs/investigations/tv-casting-latency.md.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import re

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

URL_PREFIX = "/api/localthings/camera_stream/"
MIMETYPE = "video/mp2t"
_DATA_REGISTERED = f"{DOMAIN}_camera_stream_view"
SOURCE_TIMEOUT_S = 10.0
_CHUNK = 64 * 1024
# The first bytes come after ffmpeg has connected and seen a keyframe.
_FIRST_DATA_TIMEOUT_S = 20.0
# A camera that stops sending without closing the connection leaves ffmpeg
# waiting forever (its own timeout option means different things across
# versions), so the stream ends after this long without output on stdout.
STALL_TIMEOUT_S = 15.0


def stream_path(entity_id: str) -> str:
    """The view's path for a camera entity."""
    return f"{URL_PREFIX}{entity_id}"


def rtsp_args(source: str) -> list[str]:
    """ffmpeg input options for an RTSP source."""
    if source.startswith(("rtsp://", "rtsps://")):
        return ["-rtsp_transport", "tcp"]
    return []


def ffmpeg_args(binary: str, source: str) -> list[str]:
    """ffmpeg remuxing `source` to MPEG-TS on stdout: video copied, the first
    audio track (if any) to AAC, each packet out as soon as it is in.

    The probe is capped (1 s / 500 kB instead of 5 s), as ffmpeg would
    otherwise trail the camera by the whole probe for the entire stream; an
    RTSP description carries the codec parameters, so nothing is lost."""
    args = [binary, "-hide_banner", "-loglevel", "error"]
    if "://" in source:
        # A live source: pass frames on as they come (on a file this
        # drops everything) and don't study it for seconds first.
        args += [
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-analyzeduration",
            "1000000",
            "-probesize",
            "500000",
        ]
    return [
        *args,
        *rtsp_args(source),
        "-i",
        source,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        # Don't hold video back to interleave it with (sparse) audio, and
        # write every packet straight to the TV.
        "-max_interleave_delta",
        "0",
        "-flush_packets",
        "1",
        "-f",
        "mpegts",
        "-muxdelay",
        "0",
        "-muxpreload",
        "0",
        "pipe:1",
    ]


def ffmpeg_binary(hass: HomeAssistant) -> str:
    try:
        from homeassistant.components.ffmpeg import get_ffmpeg_manager

        return get_ffmpeg_manager(hass).binary
    except (ImportError, ValueError):
        return "ffmpeg"


@callback
def async_register_view(hass: HomeAssistant) -> None:
    """Register the view once, when Home Assistant serves HTTP."""
    if hass.data.get(_DATA_REGISTERED) or getattr(hass, "http", None) is None:
        return
    hass.http.register_view(CameraStreamView(hass))
    hass.data[_DATA_REGISTERED] = True


def is_available(hass: HomeAssistant) -> bool:
    """True once the view is registered."""
    return bool(hass.data.get(_DATA_REGISTERED))


async def async_stream_source(hass: HomeAssistant, entity_id: str) -> str | None:
    """The camera's source ffmpeg can open (RTSP and the like), if any.

    None for a camera without the stream feature: an MJPEG camera reports its
    MJPEG URL, which the TV can't play; its snapshot is shown instead."""
    # Imported here: the camera component pulls in image libraries that the
    # rest of the integration doesn't need at import time.
    from homeassistant.components.camera import CameraEntityFeature, async_get_stream_source

    state = hass.states.get(entity_id)
    if not (state and state.attributes.get("supported_features", 0) & CameraEntityFeature.STREAM):
        return None

    try:
        async with asyncio.timeout(SOURCE_TIMEOUT_S):
            return await async_get_stream_source(hass, entity_id)
    except TimeoutError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="camera_timeout",
            translation_placeholders={"camera": entity_id},
        ) from err
    except HomeAssistantError:
        return None


class CameraStreamView(HomeAssistantView):
    """GET /api/localthings/camera_stream/<camera entity>: live MPEG-TS."""

    url = URL_PREFIX + "{entity_id}"
    name = "api:localthings:camera_stream"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, entity_id: str) -> web.StreamResponse:
        if not entity_id.startswith("camera."):
            raise web.HTTPNotFound
        try:
            source = await async_stream_source(self.hass, entity_id)
        except HomeAssistantError as err:
            raise web.HTTPGatewayTimeout from err
        if not source:
            raise web.HTTPNotFound

        try:
            proc = await asyncio.create_subprocess_exec(
                *ffmpeg_args(ffmpeg_binary(self.hass), source),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as err:
            _LOGGER.error("Can't run ffmpeg for the TV: %s", err)
            raise web.HTTPBadGateway from err
        errors: collections.deque[str] = collections.deque(maxlen=5)
        drain = asyncio.create_task(collect_lines(proc.stderr, errors, source=source))
        try:
            stdout = proc.stdout
            if stdout is None:
                raise web.HTTPBadGateway
            # Headers wait for the first bytes, so a camera that never
            # answers fails the TV's request instead of hanging it.
            try:
                first = await asyncio.wait_for(stdout.read(_CHUNK), _FIRST_DATA_TIMEOUT_S)
            except TimeoutError:
                first = b""
            if not first:
                _LOGGER.warning(
                    "No video from %s for the TV: %s", entity_id, " | ".join(errors) or "timeout"
                )
                raise web.HTTPBadGateway
            response = web.StreamResponse(
                headers={"Content-Type": MIMETYPE, "Cache-Control": "no-cache"}
            )
            response.enable_chunked_encoding()
            await response.prepare(request)
            data = first
            # The TV hanging up (closing the player) ends the stream.
            with contextlib.suppress(ConnectionError, TimeoutError):
                while data:
                    await response.write(data)
                    data = await asyncio.wait_for(stdout.read(_CHUNK), STALL_TIMEOUT_S)
            return response
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            await proc.wait()
            drain.cancel()


async def collect_lines(
    stream: asyncio.StreamReader | None,
    into: collections.deque[str],
    ignore: re.Pattern[str] | None = None,
    *,
    source: str | None = None,
) -> None:
    """Keep stderr for failure logs, omitting noise and the camera source."""
    if stream is None:
        return
    while line := await stream.readline():
        text = line.decode(errors="replace").strip()
        if ignore is None or not ignore.search(text):
            # FFmpeg repeats its input URL in errors, including credentials
            # in the userinfo, path or query string.
            if source:
                text = text.replace(source, "<redacted camera source>")
            into.append(text)
