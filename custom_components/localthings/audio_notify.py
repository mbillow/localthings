"""Audio clips a soundbar downloads and plays as a notification.

The board plays whatever it downloads from /x.com.samsung/notification/receiver
as headerless 16-bit mono 24 kHz PCM, after its own chime, and stops after
about 41 s. It fetches only a path that ends in a file extension, follows no
redirect and gives up on a download after 30 s (HW-S61B), so each clip is
converted in full before the write and served from memory under a random token.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
import time
from dataclasses import dataclass

from aiohttp import hdrs, web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.network import NoURLAvailableError, get_url

from . import camera_stream
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

URL_PREFIX = "/api/localthings/audio/"
SUFFIX = ".pcm"
SAMPLE_RATE = 24000
CONTENT_TYPE = f"audio/L16;rate={SAMPLE_RATE};channels=1"
_FRAME_BYTES = 2
# The HW-S61B stops a notification about 41 s in, so longer clips are cut
# here, where it can be logged.
MAX_DURATION_S = 40
_MAX_PCM_BYTES = MAX_DURATION_S * SAMPLE_RATE * _FRAME_BYTES
CONVERT_TIMEOUT_S = 60.0
# The board fetches within seconds of the write and retries once.
CLIP_TTL_S = 300.0
MAX_CLIPS = 8

_TOKEN = re.compile(r"[A-Za-z0-9_-]{22}")
# Home Assistant's signature in a source URL ffmpeg may echo back.
_CREDENTIALS = re.compile(r"(authSig=)[^&\s]+")
_DATA_CLIPS = f"{DOMAIN}_audio_clips"
_DATA_REGISTERED = f"{DOMAIN}_audio_view"


class AudioConversionError(Exception):
    """ffmpeg couldn't turn a source into PCM."""


@dataclass
class _Clip:
    pcm: bytes
    expires: float


def ffmpeg_args(binary: str, source: str) -> list[str]:
    return [
        binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        # Nothing but the download itself, even from inside a playlist.
        "-protocol_whitelist",
        "http,https,tcp,tls",
        "-i",
        source,
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        # One second past the limit, to tell a longer source from one that fits.
        "-t",
        str(MAX_DURATION_S + 1),
        "-c:a",
        "pcm_s16le",
        "-f",
        "s16le",
        "pipe:1",
    ]


def _scrub(text: str) -> str:
    return _CREDENTIALS.sub(r"\1…", text)


async def async_convert(binary: str, source: str) -> bytes:
    """`source` as PCM, whole frames only. ffmpeg is killed on a timeout or
    a cancelled call, never left running."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_args(binary, source),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as err:
        raise AudioConversionError(f"ffmpeg could not start: {err}") from err
    try:
        async with asyncio.timeout(CONVERT_TIMEOUT_S):
            out, err_out = await proc.communicate()
    except TimeoutError:
        raise AudioConversionError(
            f"the source took longer than {CONVERT_TIMEOUT_S:.0f} s to read"
        ) from None
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
    if proc.returncode != 0:
        lines = err_out.decode(errors="replace").strip().splitlines()
        raise AudioConversionError(
            _scrub(lines[-1]) if lines else f"ffmpeg exited with {proc.returncode}"
        )
    pcm = out[: len(out) - len(out) % _FRAME_BYTES]
    if not pcm:
        raise AudioConversionError("the source has no audio")
    if len(pcm) > _MAX_PCM_BYTES:
        _LOGGER.warning(
            "Audio notification cut to %d s: the soundbar stops longer ones", MAX_DURATION_S
        )
        pcm = pcm[:_MAX_PCM_BYTES]
    return pcm


def _clips(hass: HomeAssistant) -> dict[str, _Clip]:
    return hass.data.setdefault(_DATA_CLIPS, {})


def _prune(clips: dict[str, _Clip]) -> None:
    now = time.monotonic()
    for token in [token for token, clip in clips.items() if clip.expires <= now]:
        del clips[token]


def add_clip(hass: HomeAssistant, pcm: bytes) -> str:
    """Store `pcm` and return its token, dropping the oldest clip past
    MAX_CLIPS."""
    clips = _clips(hass)
    _prune(clips)
    token = secrets.token_urlsafe(16)
    clips[token] = _Clip(pcm, time.monotonic() + CLIP_TTL_S)
    while len(clips) > MAX_CLIPS:
        del clips[next(iter(clips))]
    return token


@callback
def async_clear(hass: HomeAssistant) -> None:
    """Drop every clip; expired ones otherwise wait for the next request."""
    hass.data.pop(_DATA_CLIPS, None)


def _base_url(hass: HomeAssistant) -> str:
    # The board fetches from the LAN: an internal URL first, never a cloud
    # one, which answers through a redirect the board won't follow.
    try:
        return get_url(hass, allow_cloud=False, prefer_external=False)
    except NoURLAvailableError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="audio_notification_no_url",
        ) from err


async def async_prepare(hass: HomeAssistant, source: str) -> str:
    """Convert `source` and return the URL the board downloads it from."""
    base = _base_url(hass)
    try:
        pcm = await async_convert(camera_stream.ffmpeg_binary(hass), source)
    except AudioConversionError as err:
        _LOGGER.warning("Audio notification not converted: %s", err)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="audio_notification_conversion_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    async_register_view(hass)
    return f"{base}{URL_PREFIX}{add_clip(hass, pcm)}{SUFFIX}"


@callback
def async_register_view(hass: HomeAssistant) -> None:
    """Register the view once, when Home Assistant serves HTTP."""
    if hass.data.get(_DATA_REGISTERED) or getattr(hass, "http", None) is None:
        return
    hass.http.register_view(AudioClipView(hass))
    hass.data[_DATA_REGISTERED] = True


class AudioClipView(HomeAssistantView):
    """Serves a stored clip to the board. The token is the credential."""

    url = URL_PREFIX + "{name}"
    name = "api:localthings:audio"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, name: str) -> web.Response:
        token = name.removesuffix(SUFFIX)
        if token == name or not _TOKEN.fullmatch(token):
            raise web.HTTPNotFound
        clips = _clips(self.hass)
        _prune(clips)
        if (clip := clips.get(token)) is None:
            raise web.HTTPNotFound
        return web.Response(
            body=clip.pcm,
            headers={hdrs.CONTENT_TYPE: CONTENT_TYPE, hdrs.CACHE_CONTROL: "no-store"},
        )
