"""The camera DASH view: a camera's feed as live MPEG-DASH, which the TV's
DASH engine plays a few seconds behind real time, against ~20 s for a
continuous MPEG-TS stream (docs/investigations/tv-casting-latency.md)."""

from __future__ import annotations

import asyncio
import shutil
import threading
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

from custom_components.localthings import camera_dash

_MPD = """<?xml version="1.0" encoding="utf-8"?>
<MPD type="dynamic" availabilityStartTime="2026-10-05T18:00:00.000Z">
\t<Period id="0" start="PT0.0S">
\t\t<AdaptationSet id="0" contentType="video">
\t\t\t<Representation id="0" mimeType="video/mp4">
\t\t\t\t<SegmentTemplate timescale="1000" initialization="init-stream$RepresentationID$.m4s"
\t\t\t\t\tmedia="chunk-stream$RepresentationID$-$Number%05d$.m4s" startNumber="1">
\t\t\t\t\t<SegmentTimeline><S t="0" d="1000" r="2" /></SegmentTimeline>
\t\t\t\t</SegmentTemplate>
\t\t\t</Representation>
\t\t</AdaptationSet>
\t</Period>
</MPD>
"""


@pytest.fixture(autouse=True)
def _quick_polls():
    with (
        patch.object(camera_dash, "_RELAUNCH_POLL_S", 0.01),
        patch.object(camera_dash, "_FIRST_PLAYLIST_POLL_S", 0.01),
    ):
        yield


class _FakeProc:
    """ffmpeg that runs until killed."""

    def __init__(self) -> None:
        self.stderr = asyncio.StreamReader()
        self.pid = 4242
        self.returncode: int | None = None
        self.killed = False
        self._done = asyncio.Event()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.stderr.feed_eof()
        self._done.set()

    def exit(self, code: int, stderr: bytes = b"") -> None:
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = code
        self._done.set()

    async def wait(self) -> int:
        await self._done.wait()
        return self.returncode or 0


@pytest.fixture(autouse=True)
def _casting_on():
    """A TV entry has camera casting on (CONF_CAMERA_CASTING); without one
    the views refuse every request."""
    with patch("custom_components.localthings.camera_stream.casting_enabled", return_value=True):
        yield


async def _setup_http(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "http", {})
    camera_dash.async_register_view(hass)
    assert camera_dash.is_available(hass)


async def _start(
    hass: HomeAssistant,
    proc: _FakeProc,
    entity_id: str = "camera.door",
    relaunch=None,
    source: str = "rtsp://cam/stream",
) -> str:
    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        token = await camera_dash.async_start(hass, entity_id, relaunch or AsyncMock(), source)
    assert token
    return token


def _folder(hass: HomeAssistant, token: str):
    return hass.data[camera_dash._DATA_SESSIONS][token].folder


def test_ffmpeg_copies_video_into_one_second_dash_segments(tmp_path) -> None:
    args = camera_dash.ffmpeg_args("ffmpeg", "rtsp://cam/stream", tmp_path)

    assert args[args.index("-rtsp_transport") + 1] == "tcp"
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-f") + 1] == "dash"
    assert args[args.index("-seg_duration") + 1] == "1"
    # Segments follow the camera's keyframes, so their lengths vary.
    assert args[args.index("-use_timeline") + 1] == "1"
    assert args[args.index("-format_options") + 1] == "write_prft=wallclock"
    assert "-nostdin" in args and "nobuffer" in args
    # Every track starts at zero, whatever the camera's clock says.
    assert args[args.index("-bsf:v") + 1] == "setts=pts=PTS-STARTDTS:dts=DTS-STARTDTS"
    assert args[args.index("-af") + 1] == "asetpts=PTS-STARTPTS"
    # No probe limit: the init segment needs the codec parameters.
    assert "-analyzeduration" not in args and "-probesize" not in args
    assert args[-1] == str(tmp_path / "manifest.mpd")


def test_the_playlist_waits_for_video_as_well_as_audio() -> None:
    audio_only = """<MPD>
    <AdaptationSet contentType="video"><Representation><SegmentTemplate>
        <SegmentTimeline>
        </SegmentTimeline></SegmentTemplate></Representation></AdaptationSet>
    <AdaptationSet contentType="audio"><Representation><SegmentTemplate>
        <SegmentTimeline><S t="0" d="16384" /></SegmentTimeline>
    </SegmentTemplate></Representation></AdaptationSet></MPD>"""
    both = audio_only.replace(
        "<SegmentTimeline>\n        </SegmentTimeline>",
        '<SegmentTimeline><S t="0" d="90000" /></SegmentTimeline>',
    )

    assert not camera_dash.has_every_track(audio_only)
    assert camera_dash.has_every_track(both)
    assert not camera_dash.has_every_track(None)


def test_a_missing_bandwidth_is_filled_in() -> None:
    mpd = _MPD.replace(
        '<Representation id="0" mimeType="video/mp4">',
        '<Representation id="0" mimeType="video/mp4">\n'
        '\t\t\t<Representation id="1" mimeType="audio/mp4" bandwidth="96000">\n'
        '\t\t\t<Representation id="2" mimeType="audio/mp4">',
    )

    served = camera_dash.serve_manifest(mpd, "http://ha/t/time")

    assert '<Representation bandwidth="4000000" id="0" mimeType="video/mp4">' in served
    assert '<Representation id="1" mimeType="audio/mp4" bandwidth="96000">' in served
    assert '<Representation bandwidth="128000" id="2" mimeType="audio/mp4">' in served


async def test_a_session_that_never_served_video_logs_why(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    await _setup_http(hass)
    proc = _FakeProc()
    token = await _start(hass, proc)
    proc.stderr.feed_data(b"Connection timed out\n")
    await asyncio.sleep(0.05)

    await camera_dash.async_stop(hass, token)

    assert "never produced video for the TV: Connection timed out" in caplog.text


async def test_ffmpeg_errors_never_log_camera_credentials(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    await _setup_http(hass)
    proc = _FakeProc()
    source = "rtsp://camera-user:camera-password@cam/stream?token=camera-token"
    token = await _start(hass, proc, source=source)
    proc.stderr.feed_data(f"Error opening input file {source}.\n".encode())
    await asyncio.sleep(0.05)

    await camera_dash.async_stop(hass, token)

    assert "Error opening input file <redacted camera source>." in caplog.text
    assert "camera-password" not in caplog.text
    assert "camera-token" not in caplog.text


def test_long_segments_start_two_segments_behind() -> None:
    mpd = _MPD.replace('<S t="0" d="1000" r="2" />', '<S t="0" d="3000" r="2" />')
    mpd = mpd.replace('type="dynamic"', 'type="dynamic" suggestedPresentationDelay="PT1S"')

    served = camera_dash.serve_manifest(mpd, "http://ha/t/time")

    assert 'suggestedPresentationDelay="PT6S"' in served
    assert served.count("suggestedPresentationDelay") == 1


def test_the_tv_gets_an_unsigned_address(hass: HomeAssistant) -> None:
    hass.config.internal_url = "http://192.0.2.31:8123"

    url = camera_dash.manifest_url(hass, "tok")

    # No ?authSig=: the token is the credential.
    assert url == "http://192.0.2.31:8123/api/localthings/camera_dash/tok/manifest.mpd"


def test_the_playlist_gets_the_sessions_clock() -> None:
    served = camera_dash.serve_manifest(
        _MPD.replace("</MPD>", '<UTCTiming schemeIdUri="x" value="old"/>\n</MPD>'),
        "http://ha:8123/api/localthings/camera_dash/t/time",
    )

    assert served.count("<UTCTiming") == 1
    # 1 s segments: start 3 s behind the live edge, where the TV never runs dry.
    assert 'suggestedPresentationDelay="PT3S"' in served
    assert (
        '<UTCTiming schemeIdUri="urn:mpeg:dash:utc:http-xsdate:2014" '
        'value="http://ha:8123/api/localthings/camera_dash/t/time"/>' in served
    )
    assert served.rstrip().endswith("</MPD>")


async def test_the_tv_gets_playlist_segments_and_clock_without_login(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    await _setup_http(hass)
    client = await hass_client_no_auth()
    proc = _FakeProc()
    token = await _start(hass, proc)
    folder = _folder(hass, token)
    (folder / "manifest.mpd").write_text(_MPD)
    (folder / "init-stream0.m4s").write_bytes(b"init")
    (folder / "chunk-stream0-00001.m4s").write_bytes(b"segment one")

    manifest = await client.get(camera_dash.manifest_path(token))
    segment = await client.get(f"/api/localthings/camera_dash/{token}/chunk-stream0-00001.m4s")
    ranged = await client.get(
        f"/api/localthings/camera_dash/{token}/chunk-stream0-00001.m4s",
        headers={"Range": "bytes=0-6"},
    )
    clock = await client.get(f"/api/localthings/camera_dash/{token}/time")

    assert manifest.status == 200
    assert manifest.headers["Content-Type"].startswith("application/dash+xml")
    body = await manifest.text()
    assert f"/api/localthings/camera_dash/{token}/time" in body
    assert segment.status == 200 and await segment.read() == b"segment one"
    assert segment.headers["Content-Type"] == "video/iso.segment"
    assert ranged.status == 206 and await ranged.read() == b"segment"
    assert clock.status == 200 and (await clock.text()).endswith("Z")
    proc.kill()


async def test_unknown_tokens_and_odd_names_are_not_found(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    await _setup_http(hass)
    client = await hass_client_no_auth()
    proc = _FakeProc()
    token = await _start(hass, proc)
    (_folder(hass, token) / "manifest.mpd").write_text(_MPD)

    unknown = await client.get(camera_dash.manifest_path("not-a-token"))
    hidden = await client.get(f"/api/localthings/camera_dash/{token}/.secret")
    missing = await client.get(f"/api/localthings/camera_dash/{token}/chunk-stream0-00099.m4s")
    escape = await client.get(f"/api/localthings/camera_dash/{token}/..%2F..%2Fetc%2Fpasswd")

    assert unknown.status == 404
    assert hidden.status == 404
    assert missing.status == 404
    assert escape.status in (400, 404)  # Home Assistant refuses it before the view
    proc.kill()


async def test_the_first_playlist_request_waits_for_segments(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    await _setup_http(hass)
    client = await hass_client_no_auth()
    proc = _FakeProc()
    token = await _start(hass, proc)
    folder = _folder(hass, token)

    async def ffmpeg_catches_up() -> None:
        await asyncio.sleep(0.3)
        (folder / "manifest.mpd").write_text(_MPD.replace('<S t="0" d="1000" r="2" />', ""))
        await asyncio.sleep(0.3)
        (folder / "manifest.mpd").write_text(_MPD)

    writer = asyncio.create_task(ffmpeg_catches_up())
    resp = await client.get(camera_dash.manifest_path(token))
    await writer

    assert resp.status == 200
    assert "<S " in await resp.text()
    proc.kill()


async def _tv_gives_up_waiting(hass: HomeAssistant, token: str) -> None:
    """The TV hangs up on the first playlist request (HA then cancels it)."""
    session = hass.data[camera_dash._DATA_SESSIONS][token]
    view = camera_dash.CameraDashView(hass)
    request = asyncio.create_task(
        view._async_first_manifest(session, session.folder / camera_dash.MANIFEST)
    )
    await asyncio.sleep(0.3)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request


async def test_a_slow_camera_is_opened_again_once_ready(hass: HomeAssistant) -> None:
    await _setup_http(hass)
    proc = _FakeProc()
    relaunch = AsyncMock()
    token = await _start(hass, proc, relaunch=relaunch)
    await _tv_gives_up_waiting(hass, token)
    relaunch.assert_not_awaited()  # nothing to show yet

    _folder(hass, token).joinpath("manifest.mpd").write_text(_MPD)
    for _ in range(50):
        if relaunch.await_count:
            break
        await asyncio.sleep(0.1)

    relaunch.assert_awaited_once_with(token)
    proc.kill()


async def test_a_slow_camera_is_opened_again_only_once(hass: HomeAssistant) -> None:
    await _setup_http(hass)
    proc = _FakeProc()
    relaunch = AsyncMock()
    token = await _start(hass, proc, relaunch=relaunch)
    await _tv_gives_up_waiting(hass, token)
    await _tv_gives_up_waiting(hass, token)

    _folder(hass, token).joinpath("manifest.mpd").write_text(_MPD)
    for _ in range(50):
        if relaunch.await_count:
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.1)

    relaunch.assert_awaited_once_with(token)
    proc.kill()


async def test_a_failed_relaunch_is_logged_not_raised(hass: HomeAssistant, caplog) -> None:
    await _setup_http(hass)
    proc = _FakeProc()
    relaunch = AsyncMock(side_effect=HomeAssistantError("TV off"))
    token = await _start(hass, proc, relaunch=relaunch)
    await _tv_gives_up_waiting(hass, token)

    _folder(hass, token).joinpath("manifest.mpd").write_text(_MPD)
    for _ in range(50):
        if relaunch.await_count:
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.1)

    relaunch.assert_awaited_once()
    assert "Couldn't open camera.door on the TV again: TV off" in caplog.text
    proc.kill()


async def test_a_camera_slower_than_the_wait_is_opened_again_too(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    await _setup_http(hass)
    client = await hass_client_no_auth()
    proc = _FakeProc()
    relaunch = AsyncMock()
    token = await _start(hass, proc, relaunch=relaunch)

    with patch.object(camera_dash, "_FIRST_SEGMENTS_TIMEOUT_S", 0.3):
        resp = await client.get(camera_dash.manifest_path(token))
        assert resp.status == 502
        _folder(hass, token).joinpath("manifest.mpd").write_text(_MPD)
        for _ in range(50):
            if relaunch.await_count:
                break
            await asyncio.sleep(0.1)

    relaunch.assert_awaited_once_with(token)
    proc.kill()


async def test_casts_at_once_share_one_ffmpeg(hass: HomeAssistant) -> None:
    await _setup_http(hass)
    proc = _FakeProc()

    async def slow_spawn(*_args, **_kwargs):
        await asyncio.sleep(0.1)
        return proc

    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=slow_spawn)) as spawn,
    ):
        first, second = await asyncio.gather(
            camera_dash.async_start(hass, "camera.door", AsyncMock(), "rtsp://cam/stream"),
            camera_dash.async_start(hass, "camera.door", AsyncMock(), "rtsp://cam/stream"),
        )

    assert first == second
    assert spawn.await_count == 1
    proc.kill()


async def test_no_ffmpeg_means_no_session(hass: HomeAssistant, caplog) -> None:
    await _setup_http(hass)

    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError("ffmpeg"))),
    ):
        assert (
            await camera_dash.async_start(hass, "camera.door", AsyncMock(), "rtsp://cam/stream")
            is None
        )

    assert not hass.data[camera_dash._DATA_SESSIONS]
    assert "Can't run ffmpeg for the TV" in caplog.text


async def test_a_camera_that_never_delivers_fails_the_playlist(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    await _setup_http(hass)
    client = await hass_client_no_auth()
    proc = _FakeProc()
    token = await _start(hass, proc)
    proc.exit(1, b"Connection refused\n")

    resp = await client.get(camera_dash.manifest_path(token))

    assert resp.status == 502


async def test_a_second_cast_of_the_same_camera_reuses_the_session(hass: HomeAssistant) -> None:
    await _setup_http(hass)
    proc = _FakeProc()
    first = await _start(hass, proc)

    with patch("asyncio.create_subprocess_exec", AsyncMock()) as spawn:
        again = await camera_dash.async_start(hass, "camera.door", AsyncMock(), "rtsp://cam/stream")

    assert again == first
    spawn.assert_not_awaited()
    proc.kill()


async def test_a_camera_without_a_source_gets_no_session(hass: HomeAssistant) -> None:
    await _setup_http(hass)

    assert await camera_dash.async_start(hass, "camera.door", AsyncMock(), "") is None


async def test_shared_camera_retries_each_tv_and_releases_them_independently(
    hass: HomeAssistant,
) -> None:
    await _setup_http(hass)
    proc = _FakeProc()
    first = AsyncMock(side_effect=HomeAssistantError("TV off"))
    second = AsyncMock()
    token = await _start(hass, proc, relaunch=first)
    assert await camera_dash.async_start(hass, "camera.door", second, "rtsp://cam/stream") == token
    _folder(hass, token).joinpath("manifest.mpd").write_text(_MPD)

    await camera_dash._async_relaunch_when_ready(hass, token)

    first.assert_awaited_once_with(token)
    second.assert_awaited_once_with(token)
    await camera_dash.async_release(hass, token, first)
    assert token in hass.data[camera_dash._DATA_SESSIONS]
    assert not proc.killed
    await camera_dash.async_release(hass, token, second)
    assert token not in hass.data[camera_dash._DATA_SESSIONS]
    assert proc.killed


async def test_the_session_ends_when_the_camera_stalls(hass: HomeAssistant, caplog) -> None:
    await _setup_http(hass)
    proc = _FakeProc()
    with (
        patch.object(camera_dash, "_STALL_TIMEOUT_S", 0.05),
        patch.object(camera_dash, "_IDLE_CHECK_S", 0.01),
    ):
        token = await _start(hass, proc)
        sessions = hass.data[camera_dash._DATA_SESSIONS]
        # 10 ms segments, so the stall limit is the patched one.
        short = _MPD.replace('timescale="1000"', 'timescale="100000"')
        sessions[token].folder.joinpath("manifest.mpd").write_text(short)
        for _ in range(100):
            sessions[token].touch()  # the TV keeps asking
            await asyncio.sleep(0.02)
            if token not in sessions:
                break
        await hass.async_block_till_done(wait_background_tasks=True)

    assert token not in sessions
    assert proc.killed
    assert "Camera camera.door stopped sending video to the TV" in caplog.text


async def test_the_session_ends_when_the_tv_stops_asking(hass: HomeAssistant) -> None:
    await _setup_http(hass)
    proc = _FakeProc()
    with (
        patch.object(camera_dash, "_IDLE_TIMEOUT_S", 0.05),
        patch.object(camera_dash, "_IDLE_CHECK_S", 0.01),
    ):
        token = await _start(hass, proc)
        folder = _folder(hass, token)
        for _ in range(100):
            if token not in hass.data[camera_dash._DATA_SESSIONS]:
                break
            await asyncio.sleep(0.02)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert token not in hass.data[camera_dash._DATA_SESSIONS]
    assert proc.killed
    assert not folder.exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
async def test_real_ffmpeg_writes_a_playable_live_playlist(
    hass: HomeAssistant, hass_client_no_auth, tmp_path
) -> None:
    clip = tmp_path / "clip.mp4"
    make = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=320x180:rate=10", "-t", "4", "-c:v", "libx264", "-g", "10",
        str(clip),
    )  # fmt: skip
    assert await make.wait() == 0
    await _setup_http(hass)
    client = await hass_client_no_auth()

    token = await camera_dash.async_start(hass, "camera.door", AsyncMock(), str(clip))
    assert token
    resp = await client.get(camera_dash.manifest_path(token))
    mpd = await resp.text()
    init = await client.get(f"/api/localthings/camera_dash/{token}/init-stream0.m4s")

    assert resp.status == 200
    # A file ends at once, so ffmpeg has already finalised the playlist.
    assert "<SegmentTimeline>" in mpd
    assert "<UTCTiming" in mpd
    assert init.status == 200 and b"ftyp" in await init.read()
    await camera_dash.async_stop(hass, token)


async def test_a_video_only_camera_with_long_keyframes_is_not_a_stall(
    hass: HomeAssistant,
) -> None:
    """ffmpeg writes only once per keyframe interval: 8 s segments allow
    24 s of quiet, more than the bare stall limit."""
    await _setup_http(hass)
    proc = _FakeProc()
    with (
        patch.object(camera_dash, "_STALL_TIMEOUT_S", 0.05),
        patch.object(camera_dash, "_IDLE_CHECK_S", 0.01),
    ):
        token = await _start(hass, proc)
        sessions = hass.data[camera_dash._DATA_SESSIONS]
        long = _MPD.replace('d="1000"', 'd="8000"')
        sessions[token].folder.joinpath("manifest.mpd").write_text(long)
        for _ in range(20):
            sessions[token].touch()
            await asyncio.sleep(0.02)

    assert token in sessions
    proc.kill()


def test_long_segments_set_the_playlists_max_segment_duration() -> None:
    mpd = _MPD.replace("<MPD ", '<MPD maxSegmentDuration="PT1.0S" ').replace('d="1000"', 'd="4000"')
    served = camera_dash.serve_manifest(mpd, "http://tv/time")
    assert 'maxSegmentDuration="PT4S"' in served


async def test_no_relaunch_once_the_tv_came_back_by_itself(hass: HomeAssistant) -> None:
    await _setup_http(hass)
    proc = _FakeProc()
    relaunch = AsyncMock()
    token = await _start(hass, proc, relaunch=relaunch)
    await _tv_gives_up_waiting(hass, token)

    hass.data[camera_dash._DATA_SESSIONS][token].produced = True  # the TV asked again
    _folder(hass, token).joinpath("manifest.mpd").write_text(_MPD)
    await asyncio.sleep(0.1)

    relaunch.assert_not_awaited()
    proc.kill()


@pytest.mark.parametrize("stage", ["folder", "spawn", "lock"])
async def test_shutdown_during_camera_start_leaves_no_session(
    hass: HomeAssistant, tmp_path, stage: str
) -> None:
    await _setup_http(hass)
    stopping = False
    proc = _FakeProc()
    folder = tmp_path / "dash"

    def make_folder(**kwargs):
        nonlocal stopping
        folder.mkdir()
        if stage == "folder":
            stopping = True
        return str(folder)

    async def spawn(*args, **kwargs):
        nonlocal stopping
        stopping = True
        return proc

    with (
        patch.object(type(hass), "is_stopping", property(lambda self: stopping)),
        patch.object(camera_dash.tempfile, "mkdtemp", side_effect=make_folder),
        patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=spawn)) as create,
    ):
        if stage == "lock":
            lock = asyncio.Lock()
            hass.data[camera_dash._DATA_START_LOCKS] = {"camera.door": lock}
            await lock.acquire()
            task = asyncio.create_task(
                camera_dash.async_start(hass, "camera.door", AsyncMock(), "rtsp://cam/stream")
            )
            await asyncio.sleep(0)
            assert not task.done()
            stopping = True
            lock.release()
            assert await task is None
        else:
            assert (
                await camera_dash.async_start(hass, "camera.door", AsyncMock(), "rtsp://cam/stream")
                is None
            )
    assert not camera_dash._sessions(hass)
    assert not folder.exists()
    if stage == "spawn":
        assert proc.killed
        create.assert_awaited_once()
    else:
        create.assert_not_awaited()


async def test_no_session_starts_while_home_assistant_stops(hass: HomeAssistant) -> None:
    await _setup_http(hass)
    with (
        patch.object(type(hass), "is_stopping", property(lambda self: True)),
        patch("asyncio.create_subprocess_exec", AsyncMock()) as spawn,
    ):
        assert (
            await camera_dash.async_start(hass, "camera.door", AsyncMock(), "rtsp://cam/stream")
            is None
        )
    spawn.assert_not_awaited()


async def test_cancelled_folder_creation_removes_the_executors_folder(
    hass: HomeAssistant, tmp_path
) -> None:
    await _setup_http(hass)
    started = asyncio.Event()
    finish = threading.Event()
    loop = asyncio.get_running_loop()
    folder = tmp_path / "cancelled-dash"

    def make_folder(**kwargs):
        loop.call_soon_threadsafe(started.set)
        finish.wait()
        folder.mkdir()
        return str(folder)

    with (
        patch.object(camera_dash.tempfile, "mkdtemp", side_effect=make_folder),
        patch("asyncio.create_subprocess_exec", AsyncMock()) as spawn,
    ):
        task = asyncio.create_task(
            camera_dash.async_start(hass, "camera.door", AsyncMock(), "rtsp://cam/stream")
        )
        try:
            await started.wait()
            task.cancel()
            await asyncio.sleep(0)
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not folder.exists()
    assert not camera_dash._sessions(hass)
    spawn.assert_not_awaited()


def test_only_non_rtsp_sources_get_an_h264_tag(tmp_path) -> None:
    """MPEG-TS/HLS carry a stream tag the MP4 muxer refuses; RTSP none."""
    hls = camera_dash.ffmpeg_args("ffmpeg", "http://cam/live.m3u8", tmp_path)
    rtsp = camera_dash.ffmpeg_args("ffmpeg", "rtsp://cam/stream", tmp_path)
    assert hls[hls.index("-tag:v") + 1] == "avc1"
    assert "-tag:v" not in rtsp


async def test_a_cancelled_stop_still_removes_the_folder(hass: HomeAssistant) -> None:
    await _setup_http(hass)
    proc = _FakeProc()
    token = await _start(hass, proc)
    folder = _folder(hass, token)
    exited = asyncio.Event()
    proc.wait = AsyncMock(side_effect=exited.wait)  # ffmpeg slow to exit

    stop = asyncio.create_task(camera_dash.async_stop(hass, token))
    await asyncio.sleep(0.05)
    stop.cancel()
    exited.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert stop.cancelled()
    assert not folder.exists()


async def test_an_hevc_stream_is_restarted_with_the_tag_ffmpeg_names(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    """ffmpeg refuses avc1 for HEVC from an MPEG-TS/HLS source and names the
    tag it wants; the session restarts with it and the TV still gets its playlist."""
    await _setup_http(hass)
    client = await hass_client_no_auth()
    refused, retagged = _FakeProc(), _FakeProc()
    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(side_effect=[refused, retagged])
    ) as spawn:
        token = await camera_dash.async_start(
            hass, "camera.door", AsyncMock(), "http://cam/live.m3u8"
        )
        assert token is not None
        refused.exit(183, b"[mp4 @ 0x1] Tag avc1 incompatible with output codec id '173' (hev1)\n")
        request = asyncio.create_task(client.get(camera_dash.manifest_path(token)))
        for _ in range(100):
            if spawn.await_count == 2:
                break
            await asyncio.sleep(0.01)
        _folder(hass, token).joinpath("manifest.mpd").write_text(_MPD)
        resp = await request

    assert resp.status == 200
    args = spawn.await_args_list[1].args
    assert args[args.index("-tag:v") + 1] == "hev1"
    assert hass.data[camera_dash._DATA_SESSIONS][token].proc is retagged
    retagged.kill()


async def test_a_retag_after_stop_leaves_no_ffmpeg_running(hass: HomeAssistant) -> None:
    await _setup_http(hass)
    refused, late = _FakeProc(), _FakeProc()
    token = await _start(hass, refused, source="http://cam/live.m3u8")
    session = hass.data[camera_dash._DATA_SESSIONS][token]
    refused.exit(183, b"Tag avc1 incompatible with output codec id '173' (hev1)\n")
    spawned = asyncio.Event()

    async def slow_spawn(*args, **kwargs):
        spawned.set()
        await asyncio.sleep(0.1)  # stopped while ffmpeg starts
        return late

    with patch("asyncio.create_subprocess_exec", side_effect=slow_spawn):
        retag = asyncio.create_task(camera_dash._async_retag(hass, session))
        await spawned.wait()
        await camera_dash.async_stop(hass, token)
        assert await retag is False

    assert late.killed
    assert session.proc is refused


async def test_a_live_session_is_refused_once_casting_is_off(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    await _setup_http(hass)
    client = await hass_client_no_auth()
    token = await _start(hass, _FakeProc())
    (_folder(hass, token) / "manifest.mpd").write_text(_MPD)

    with patch("custom_components.localthings.camera_stream.casting_enabled", return_value=False):
        response = await client.get(camera_dash.manifest_path(token))

    assert response.status == 404
    await camera_dash.async_stop(hass, token)
