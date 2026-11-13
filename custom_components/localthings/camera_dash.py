"""Low-delay camera casting through live MPEG-DASH.

ffmpeg copies video and encodes audio as AAC into a temporary folder. A
random path token authenticates the TV; idle sessions are removed. DASH
avoids Gallery's ~20 s MPEG-TS buffer (docs/investigations/tv-casting-latency.md).
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.network import NoURLAvailableError, get_url

from . import camera_stream
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

URL_PREFIX = "/api/localthings/camera_dash/"
MANIFEST = "manifest.mpd"
_DATA_REGISTERED = f"{DOMAIN}_camera_dash_view"
_DATA_SESSIONS = f"{DOMAIN}_camera_dash_sessions"
_DATA_START_LOCKS = f"{DOMAIN}_camera_dash_start_locks"
# The TV's first playlist request waits for ffmpeg to have written segments
# (it connects to the camera and completes a keyframe interval meanwhile).
_FIRST_SEGMENTS_TIMEOUT_S = 20.0
# A camera that stops sending without closing the connection leaves ffmpeg
# waiting forever: the session ends once ffmpeg has written nothing for this
# long, or three segments' length if that is longer (a video-only camera's
# output only grows once per keyframe interval).
_STALL_TIMEOUT_S = 15.0
# The TV asks for a segment every second while it plays; this long without
# a request means it stopped.
_IDLE_TIMEOUT_S = 30.0
_IDLE_CHECK_S = 5.0
# How often the first-playlist wait and the relaunch wait look again.
_FIRST_PLAYLIST_POLL_S = 0.2
_RELAUNCH_POLL_S = 0.5
_RELAUNCH_TASK = "localthings dash relaunch"
# Printed for every camera without audio (the AAC options go unused), and
# when it states no bitrate; not why a cast failed.
_FFMPEG_NOISE = re.compile(r"has not been used for any stream|No bit rate set")
_STDERR_TASK = "localthings dash stderr"
_TAG_REFUSED = re.compile(r"incompatible with output codec id '\d+' \((\w{4})\)")
_FILE_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")
_TYPES = {".mpd": "application/dash+xml", ".m4s": "video/iso.segment"}
# Where the player starts, behind the live edge: closer, it runs dry waiting
# for the next segment and rebuffers deeper than this. Long segments (a
# camera's longer keyframe interval) start it two segments back instead.
_START_BEHIND_S = 3.0
# Segments a playlist lists per track: enough that even with long segments
# the audio track reaches back past where the player starts.
_WINDOW_SEGMENTS = 30
# Only a hint for bitrate switching, of which there is none (one quality).
_FALLBACK_BANDWIDTH = {False: 4_000_000, True: 128_000}


def ffmpeg_args(binary: str, source: str, folder: Path, tag: str | None = None) -> list[str]:
    """Copy video and encode AAC into DASH segments with wall-clock timing.

    ffmpeg's default probe (not camera_stream's short one) keeps codecs sent
    only with keyframes; the TV rejects an init segment without them.
    Keyframe spacing limits segment length.
    """
    # Warnings are kept for the log at session end.
    args = [binary, "-nostdin", "-hide_banner", "-loglevel", "warning"]
    if "://" in source:
        args += ["-fflags", "nobuffer", "-flags", "low_delay"]
    return [
        *args,
        *camera_stream.rtsp_args(source),
        "-i",
        source,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "copy",
        # MPEG-TS/HLS sources carry a tag the MP4 muxer refuses (RTSP none).
        # H.264 is assumed; another codec is retried with the tag ffmpeg names.
        *(["-tag:v", tag or "avc1"] if tag or not camera_stream.rtsp_args(source) else []),
        # Each track from zero, on the camera's own timing. Some cameras send
        # timestamps hours from zero; the TV then waits for segments it thinks
        # lie hours ahead and never fetches one.
        "-bsf:v",
        # One offset for both, so reordered (B-)frames keep pts >= dts.
        "setts=pts=PTS-STARTDTS:dts=DTS-STARTDTS",
        "-af",
        "asetpts=PTS-STARTPTS",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-f",
        "dash",
        "-seg_duration",
        "1",
        "-use_template",
        "1",
        "-use_timeline",
        "1",
        "-window_size",
        str(_WINDOW_SEGMENTS),
        "-extra_window_size",
        "6",
        "-format_options",
        "write_prft=wallclock",
        str(folder / MANIFEST),
    ]


def _longest_segment_s(mpd: str) -> float:
    """The longest segment a SegmentTimeline lists, in seconds."""
    longest = 0.0
    for block in re.finditer(r"<SegmentTemplate\b([^>]*)>(.*?)</SegmentTemplate>", mpd, re.S):
        scale = re.search(r'timescale="(\d+)"', block.group(1))
        timescale = int(scale.group(1)) if scale else 1
        for d in re.findall(r'<S\b[^>]*\bd="(\d+)"', block.group(2)):
            longest = max(longest, int(d) / timescale)
    return longest


def _with_bandwidth(representation: re.Match[str]) -> str:
    tag = representation.group(0)
    if "bandwidth=" in tag:
        return tag
    audio = 'mimeType="audio' in tag
    return tag.replace(
        "<Representation ", f'<Representation bandwidth="{_FALLBACK_BANDWIDTH[audio]}" ', 1
    )


def serve_manifest(mpd: str, time_url: str) -> str:
    """Add the TV's required bandwidth, a safe live delay and HA's clock."""
    mpd = re.sub(r"<Representation\b[^>]*>", _with_bandwidth, mpd)
    longest = _longest_segment_s(mpd)
    behind = max(_START_BEHIND_S, 2 * longest)
    if longest:
        # ffmpeg states its target (1 s), not what a camera's keyframe
        # interval made of it.
        mpd = re.sub(
            r'maxSegmentDuration="[^"]*"', f'maxSegmentDuration="PT{longest:g}S"', mpd, count=1
        )
    delay = f'suggestedPresentationDelay="PT{behind:g}S"'
    if "suggestedPresentationDelay=" in mpd:
        mpd = re.sub(r'suggestedPresentationDelay="[^"]*"', delay, mpd, count=1)
    else:
        mpd = re.sub(r"(<MPD\b)", rf"\1 {delay}", mpd, count=1)
    mpd = re.sub(r"\s*<UTCTiming\b[^>]*/>", "", mpd)
    timing = f'<UTCTiming schemeIdUri="urn:mpeg:dash:utc:http-xsdate:2014" value="{time_url}"/>'
    return mpd.replace("</MPD>", f"\t{timing}\n</MPD>", 1)


def has_every_track(mpd: str | None) -> bool:
    """True once every track's SegmentTimeline lists a segment.

    Audio segments come every second; the first video segment only once a
    whole keyframe interval has come in after the first keyframe. A playlist
    with audio but no video yet makes the TV give up."""
    if not mpd:
        return False
    timelines = re.findall(r"<SegmentTimeline>(.*?)</SegmentTimeline>", mpd, re.S)
    if not timelines:
        return "<S " in mpd
    return all("<S " in timeline for timeline in timelines)


def manifest_path(token: str) -> str:
    return f"{URL_PREFIX}{token}/{MANIFEST}"


def manifest_url(hass: HomeAssistant, token: str) -> str:
    """The playlist's absolute address for the TV, unsigned: the token in
    the path is the credential, and a signature would only lengthen it."""
    try:
        return get_url(hass) + manifest_path(token)
    except NoURLAvailableError as err:
        raise HomeAssistantError("Home Assistant has no URL the TV can reach") from err


def _now_iso() -> str:
    now = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now * 1000) % 1000:03d}Z"


@dataclass
class _Session:
    entity_id: str
    folder: Path
    proc: asyncio.subprocess.Process
    source: str = ""
    tag: str | None = None  # the video tag a restarted ffmpeg was given
    retag: asyncio.Task[bool] | None = None
    errors: collections.deque[str] = field(default_factory=lambda: collections.deque(maxlen=20))
    last_request: float = field(default_factory=time.monotonic)
    produced: bool = False
    # Each TV owns its retry; removing one must not end another TV's stream.
    relaunches: set[Callable[[str], Awaitable[None]]] = field(default_factory=set)
    relaunch_scheduled: bool = False
    tasks: list[asyncio.Task] = field(default_factory=list)

    def touch(self) -> None:
        self.last_request = time.monotonic()

    @property
    def running(self) -> bool:
        return self.proc.returncode is None


def _sessions(hass: HomeAssistant) -> dict[str, _Session]:
    return hass.data.setdefault(_DATA_SESSIONS, {})


@callback
def async_register_view(hass: HomeAssistant) -> None:
    """Register the view once, when Home Assistant serves HTTP."""
    if hass.data.get(_DATA_REGISTERED) or getattr(hass, "http", None) is None:
        return
    hass.http.register_view(CameraDashView(hass))
    hass.data[_DATA_REGISTERED] = True

    async def _stop_all(_event: Event) -> None:
        for token in list(_sessions(hass)):
            await async_stop(hass, token)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop_all)


def is_available(hass: HomeAssistant) -> bool:
    """True once the view is registered."""
    return bool(hass.data.get(_DATA_REGISTERED))


async def async_start(
    hass: HomeAssistant,
    entity_id: str,
    relaunch: Callable[[str], Awaitable[None]],
    source: str,
) -> str | None:
    """Start or reuse a session; return None if unavailable or shutting down.

    The first HTTP request waits for segments, overlapping Gallery's launch.
    `relaunch` retries a TV that gave up before the playlist became ready.
    """
    if hass.is_stopping:
        return None
    locks: dict[str, asyncio.Lock] = hass.data.setdefault(_DATA_START_LOCKS, {})
    lock = locks.setdefault(entity_id, asyncio.Lock())
    async with lock:  # one ffmpeg per camera, however many casts at once
        return await _async_start_locked(hass, entity_id, relaunch, source)


async def _async_start_locked(
    hass: HomeAssistant,
    entity_id: str,
    relaunch: Callable[[str], Awaitable[None]],
    source: str,
) -> str | None:
    if hass.is_stopping:
        return None
    for token, session in _sessions(hass).items():
        if session.entity_id == entity_id and session.running:
            session.touch()
            session.relaunches.add(relaunch)
            return token
    if not source or hass.is_stopping:
        return None
    creation = hass.async_add_executor_job(lambda: tempfile.mkdtemp(prefix="localthings-dash-"))
    try:
        folder = Path(await asyncio.shield(creation))
    except asyncio.CancelledError:
        # The executor keeps running after cancellation; collect its folder.
        folder = Path(await asyncio.shield(creation))
        await hass.async_add_executor_job(shutil.rmtree, folder, True)
        raise
    try:
        if hass.is_stopping:
            await hass.async_add_executor_job(shutil.rmtree, folder, True)
            return None
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_args(camera_stream.ffmpeg_binary(hass), source, folder),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except BaseException as err:
        await hass.async_add_executor_job(shutil.rmtree, folder, True)
        if not isinstance(err, OSError):
            raise
        _LOGGER.error("Can't run ffmpeg for the TV: %s", err)
        return None
    if hass.is_stopping:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        await hass.async_add_executor_job(shutil.rmtree, folder, True)
        return None
    token = secrets.token_urlsafe(24)
    _LOGGER.debug(
        "Camera %s: DASH session %s… started (ffmpeg pid %s)", entity_id, token[:6], proc.pid
    )
    session = _Session(entity_id, folder, proc, source=source, relaunches={relaunch})
    _sessions(hass)[token] = session
    session.tasks = [
        _collect_stderr(hass, session),
        hass.async_create_background_task(_async_watch(hass, token), "localthings dash idle watch"),
    ]
    return token


def _collect_stderr(hass: HomeAssistant, session: _Session) -> asyncio.Task:
    return hass.async_create_background_task(
        camera_stream.collect_lines(
            session.proc.stderr, session.errors, ignore=_FFMPEG_NOISE, source=session.source
        ),
        _STDERR_TASK,
    )


async def _async_retag(hass: HomeAssistant, session: _Session) -> bool:
    """True once an ffmpeg that refused the video's tag runs again with the
    tag it named (an HEVC stream from an MPEG-TS/HLS source). Once per
    session, shared by whoever noticed the exit first."""
    if session.retag is None:
        session.retag = hass.async_create_task(_async_restart_tagged(hass, session))
    return await asyncio.shield(session.retag) and session.running


async def _async_restart_tagged(hass: HomeAssistant, session: _Session) -> bool:
    readers = [t for t in session.tasks if t.get_name() == _STDERR_TASK and not t.done()]
    if readers:  # ffmpeg's last words may still be in the pipe
        await asyncio.wait(readers, timeout=1)
    match = next(filter(None, map(_TAG_REFUSED.search, session.errors)), None)
    if match is None or not _is_live(hass, session):
        return False
    session.tag = match.group(1)
    _LOGGER.debug("Camera %s: retrying with video tag %s", session.entity_id, session.tag)
    try:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_args(
                camera_stream.ffmpeg_binary(hass), session.source, session.folder, session.tag
            ),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        return False
    if not _is_live(hass, session):  # stopped meanwhile: nobody would end it
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return False
    session.proc = proc
    session.errors.clear()
    session.tasks.append(_collect_stderr(hass, session))
    return True


def _is_live(hass: HomeAssistant, session: _Session) -> bool:
    """Still registered (async_stop hasn't taken it), and HA isn't stopping."""
    return not hass.is_stopping and any(s is session for s in _sessions(hass).values())


async def _async_relaunch_when_ready(hass: HomeAssistant, token: str) -> None:
    """The TV gave up on the playlist before the camera delivered: open it on
    the TV again once every track has a segment (once per session)."""
    deadline = time.monotonic() + 2 * _FIRST_SEGMENTS_TIMEOUT_S
    while (session := _sessions(hass).get(token)) is not None and session.running:
        session.touch()  # keep the session while the camera catches up
        mpd = await hass.async_add_executor_job(_read_text, session.folder / MANIFEST)
        if has_every_track(mpd):
            # The TV may have come back for it on its own meanwhile.
            if session.relaunches and not session.produced:
                _LOGGER.info(
                    "Camera %s was slow to start; opening it on the TV again", session.entity_id
                )
                for relaunch in list(session.relaunches):
                    if relaunch not in session.relaunches:
                        continue
                    try:
                        await relaunch(token)
                    except HomeAssistantError as err:
                        _LOGGER.warning(
                            "Couldn't open %s on the TV again: %s", session.entity_id, err
                        )
            return
        if time.monotonic() > deadline:
            return
        await asyncio.sleep(_RELAUNCH_POLL_S)


async def _async_watch(hass: HomeAssistant, token: str) -> None:
    """End the session once ffmpeg exits or stalls, or the TV stops asking."""
    last_output, last_change = None, time.monotonic()
    while (session := _sessions(hass).get(token)) is not None:
        if not session.running:
            if await _async_retag(hass, session):
                continue
            _LOGGER.warning(
                "Camera %s stopped streaming to the TV: %s",
                session.entity_id,
                " | ".join(session.errors) or f"ffmpeg exited ({session.proc.returncode})",
            )
            break
        now = time.monotonic()
        if now - session.last_request > _IDLE_TIMEOUT_S:
            break
        output, mpd = await hass.async_add_executor_job(_output_state, session.folder)
        stall_after = max(_STALL_TIMEOUT_S, 3 * _longest_segment_s(mpd or ""))
        if output != last_output or not has_every_track(mpd):
            # Not judged before the first complete playlist: that wait has
            # its own limit (_FIRST_SEGMENTS_TIMEOUT_S).
            last_output, last_change = output, now
        elif now - last_change > stall_after:
            _LOGGER.warning("Camera %s stopped sending video to the TV", session.entity_id)
            break
        await asyncio.sleep(_IDLE_CHECK_S)
    await async_stop(hass, token)


async def async_release(
    hass: HomeAssistant, token: str, relaunch: Callable[[str], Awaitable[None]]
) -> None:
    """Release one TV's cast, stopping only an unshared session."""
    session = _sessions(hass).get(token)
    if session is None:
        return
    session.relaunches.discard(relaunch)
    if not session.relaunches:
        await async_stop(hass, token)


async def async_stop(hass: HomeAssistant, token: str) -> None:
    """End a session: ffmpeg, its tasks and its folder."""
    session = _sessions(hass).pop(token, None)
    if session is None:
        return
    # Before any await: nothing else knows the session once it is popped.
    was_running = session.running
    if was_running:
        with contextlib.suppress(ProcessLookupError):
            session.proc.kill()
    _LOGGER.debug(
        "Camera %s: DASH session %s… ended; ffmpeg said: %s",
        session.entity_id,
        token[:6],
        " | ".join(session.errors) or "nothing",
    )
    # Finished in its own task: a caller cancelled mid-cleanup must not leave
    # the folder behind, as nothing else can reach the session any more.
    caller = asyncio.current_task()
    await asyncio.shield(
        hass.async_create_background_task(
            _async_finish(hass, session, was_running, caller), "localthings dash cleanup"
        )
    )


async def _async_finish(
    hass: HomeAssistant, session: _Session, was_running: bool, caller: asyncio.Task | None
) -> None:
    if not session.produced and was_running:
        # The TV never got a playlist: say whether the camera was just slow
        # (the TV gave up first) or ffmpeg produced nothing at all.
        mpd = await hass.async_add_executor_job(_read_text, session.folder / MANIFEST)
        if has_every_track(mpd):
            _LOGGER.warning(
                "Camera %s took too long to start for the TV (it stopped waiting)",
                session.entity_id,
            )
        else:
            _LOGGER.warning(
                "Camera %s never produced video for the TV: %s",
                session.entity_id,
                " | ".join(session.errors) or "no output from ffmpeg",
            )
    await session.proc.wait()
    for task in session.tasks:
        # A relaunch already under way finishes on its own: cancelled
        # mid-write, it would leave the TV's write half done.
        if task is not caller and task.get_name() != _RELAUNCH_TASK:
            task.cancel()
    await hass.async_add_executor_job(shutil.rmtree, session.folder, True)


class CameraDashView(HomeAssistantView):
    """GET /api/localthings/camera_dash/<token>/<file>: a session's playlist,
    segments and clock."""

    url = URL_PREFIX + "{token}/{name}"
    name = "api:localthings:camera_dash"
    # The token is the credential: the TV can't log in or sign each segment.
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, token: str, name: str) -> web.StreamResponse:
        session = _sessions(self.hass).get(token)
        if session is None:
            _LOGGER.debug("DASH request for unknown session %s…", token[:6])
            raise web.HTTPNotFound
        session.touch()
        if name == "time":
            return web.Response(text=_now_iso(), content_type="text/plain")
        if not _FILE_NAME.fullmatch(name):
            raise web.HTTPNotFound
        _LOGGER.debug(
            "Camera %s: TV asks for %s (%s)",
            session.entity_id,
            name,
            request.headers.get("Range") or "whole",
        )
        path = session.folder / name
        if name == MANIFEST:
            mpd = await self._async_first_manifest(session, path)
            time_url = f"{request.scheme}://{request.host}{URL_PREFIX}{token}/time"
            _LOGGER.debug("Camera %s: playlist served:\n%s", session.entity_id, mpd)
            return web.Response(
                text=serve_manifest(mpd, time_url),
                content_type=_TYPES[".mpd"],
                headers={"Cache-Control": "no-cache"},
            )
        if not await self.hass.async_add_executor_job(path.is_file):
            _LOGGER.debug("Camera %s: %s isn't there (yet)", session.entity_id, name)
            raise web.HTTPNotFound
        return web.FileResponse(
            path, headers={"Content-Type": _TYPES.get(path.suffix, "video/mp4")}
        )

    async def _async_first_manifest(self, session: _Session, path: Path) -> str:
        """The playlist once every track lists a segment; the first request
        waits for ffmpeg (camera connection, first keyframe)."""
        deadline = time.monotonic() + _FIRST_SEGMENTS_TIMEOUT_S
        try:
            while True:
                mpd = await self.hass.async_add_executor_job(_read_text, path)
                if mpd is not None and has_every_track(mpd):
                    session.produced = True
                    return mpd
                if not session.running and await _async_retag(self.hass, session):
                    continue
                if not session.running or time.monotonic() > deadline:
                    if session.running:  # just slow: open it again once ready
                        self._schedule_relaunch(session)
                    _LOGGER.warning(
                        "No video from %s for the TV: %s",
                        session.entity_id,
                        " | ".join(session.errors) or "timeout",
                    )
                    raise web.HTTPBadGateway
                await asyncio.sleep(_FIRST_PLAYLIST_POLL_S)
        except asyncio.CancelledError:
            # The TV hung up first (it waits less than a slow camera needs).
            self._schedule_relaunch(session)
            raise

    def _schedule_relaunch(self, session: _Session) -> None:
        token = next((t for t, s in _sessions(self.hass).items() if s is session), None)
        if token is None or session.relaunch_scheduled or not session.relaunches:
            return
        session.relaunch_scheduled = True
        _LOGGER.debug("Camera %s: the TV got no playlist in time", session.entity_id)
        session.tasks.append(
            self.hass.async_create_background_task(
                _async_relaunch_when_ready(self.hass, token), _RELAUNCH_TASK
            )
        )


def _output_state(folder: Path) -> tuple[float | None, str | None]:
    """When ffmpeg last wrote to the folder, and its playlist."""
    try:
        newest = max((entry.stat().st_mtime for entry in folder.iterdir()), default=None)
    except OSError:
        newest = None
    return newest, _read_text(folder / MANIFEST)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None
