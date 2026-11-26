"""A TV announcing itself over SSDP is looked at at once (announce.py)."""

from __future__ import annotations

import asyncio
import sys
import time
import types
from enum import Enum
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from smartthings_local.errors import SessionClosedError, SessionError

from custom_components.localthings import coordinator as coordinator_module
from custom_components.localthings.announce import async_watch_alive
from custom_components.localthings.const import CONF_DEVICE_TYPE, CONF_HOST, CONF_MAC, DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from tests.localthings.test_tv_media_player_tu7172 import ENTRY_DATA, MOCK_TV_DI, _setup_tv

TV_MAC = "02:00:00:00:00:0a"
_Change = Enum("_Change", "ALIVE BYEBYE UPDATE")


class _FakeSsdp(types.ModuleType):
    """homeassistant.components.ssdp as far as announce.py uses it."""

    SsdpChange = _Change

    def __init__(self) -> None:
        super().__init__("homeassistant.components.ssdp")
        self.registered: list[tuple] = []

    async def async_register_callback(self, hass, callback, match_dict=None):
        entry = (callback, match_dict)
        self.registered.append(entry)
        return lambda: self.registered.remove(entry)


@pytest.fixture
def ssdp(hass: HomeAssistant, monkeypatch) -> _FakeSsdp:
    """The ssdp integration loaded, without its network listener."""
    import homeassistant.components as components

    fake = _FakeSsdp()
    monkeypatch.setitem(sys.modules, "homeassistant.components.ssdp", fake)
    monkeypatch.setattr(components, "ssdp", fake, raising=False)
    hass.config.components.add("ssdp")
    return fake


async def test_nothing_is_watched_without_the_ssdp_integration(hass: HomeAssistant) -> None:
    assert await async_watch_alive(hass, "192.0.2.103", lambda: None) is None


async def test_only_alives_from_the_host_are_passed_on(hass: HomeAssistant, ssdp) -> None:
    seen: list[None] = []

    unwatch = await async_watch_alive(hass, "192.0.2.103", lambda: seen.append(None))

    assert unwatch is not None
    [(callback, match)] = ssdp.registered
    assert match == {"_host": "192.0.2.103"}
    callback(None, _Change.BYEBYE)
    callback(None, _Change.UPDATE)
    assert seen == []
    callback(None, _Change.ALIVE)
    assert seen == [None]
    unwatch()
    assert ssdp.registered == []


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_MAC: TV_MAC},
        unique_id=f"localthings_{MOCK_TV_DI}",
        version=4,
    )
    entry.add_to_hass(hass)
    return entry


class _Handshake:
    def __init__(self) -> None:
        self.cancelled = 0

    def cancel_connect(self) -> None:
        self.cancelled += 1


async def test_an_announcing_tv_that_reads_dark_is_looked_at_once(
    hass: HomeAssistant, ssdp
) -> None:
    """One look per burst of alives, with the handshake in flight cancelled,
    the stale session dropped and the port lookup due."""
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, match)] = ssdp.registered
    assert match == {"_host": entry.data[CONF_HOST]}
    coordinator._failed_cycles = 3
    coordinator._next_rediscovery_ts = 10**9
    handshake = _Handshake()
    coordinator._connecting = handshake
    release = asyncio.Event()

    async def _look() -> None:
        await release.wait()

    with (
        patch.object(coordinator, "async_refresh", AsyncMock(side_effect=_look)) as look,
        patch.object(coordinator, "_close_session", wraps=coordinator._close_session) as close,
    ):
        for _ in range(4):  # one NOTIFY per announced service
            callback(None, _Change.ALIVE)
        await asyncio.sleep(0)
        release.set()
        async with asyncio.timeout(5):
            await coordinator._announce_task

    assert look.await_count == 1
    assert handshake.cancelled == 1
    close.assert_called_once()
    assert coordinator._next_rediscovery_ts == 0.0


async def test_an_announcing_tv_that_answers_is_left_alone(hass: HomeAssistant, ssdp) -> None:
    """The scanner replays known devices on registration, and a TV that is
    on re-announces: neither costs a look."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered

    with patch.object(coordinator, "async_refresh", AsyncMock()) as look:
        callback(None, _Change.ALIVE)
        await hass.async_block_till_done(wait_background_tasks=True)

    look.assert_not_awaited()
    assert coordinator._announce_task is None
    assert coordinator._session is session


async def test_unloading_stops_the_watch(hass: HomeAssistant, ssdp) -> None:
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    assert ssdp.registered

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert ssdp.registered == []


async def test_a_look_queued_behind_a_poll_that_found_the_tv_is_dropped(
    hass: HomeAssistant, ssdp
) -> None:
    """The announcement arrived while a poll held the session; that poll
    found the TV answering, so its session is kept and no look follows."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    coordinator._failed_cycles = 2

    with patch.object(coordinator, "async_refresh", AsyncMock()) as look:
        async with coordinator._session_lock:
            callback(None, _Change.ALIVE)
            await asyncio.sleep(0)
            coordinator._failed_cycles = 0
        async with asyncio.timeout(5):
            await coordinator._announce_task

    look.assert_not_awaited()
    assert coordinator._session is session


async def test_a_tv_announcing_from_standby_is_read_on_its_open_session(
    hass: HomeAssistant, ssdp
) -> None:
    """A session that just read standby answers: the look reads on it
    rather than handshaking again."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    session.resources["/sec/tv/switch/binary"] = {"value": False}
    await coordinator.async_refresh()
    assert coordinator._standby_cycle

    with patch(
        "custom_components.localthings.coordinator.create_transport", return_value=session
    ) as connect:
        callback(None, _Change.ALIVE)
        async with asyncio.timeout(5):
            await coordinator._announce_task

    connect.assert_not_called()
    assert coordinator._session is session
    assert coordinator._standby_cycle


@pytest.mark.parametrize(
    ("announced", "error", "intervals"),
    [
        (True, TimeoutError("timed out"), [30, 60, 120, 120, 120, 120]),
        (True, SessionClosedError(), [30, 60, 120, 120, 120, 120]),
        (True, SessionError(), [30] * 6),
        (False, TimeoutError("timed out"), [30] * 6),
    ],
    ids=["announces", "handshake-cancelled", "refused", "silent"],
)
async def test_a_dark_tv_is_polled_less_often_once_it_announces_itself(
    hass: HomeAssistant,
    ssdp,
    clock: _Clock,
    announced: bool,
    error: Exception,
    intervals: list[int],
) -> None:
    """Its power-on announcement finds it; the polls back off to a cap and
    return to the summary interval when it answers. A TV that refuses the
    session (a fatal alert) answers, so it keeps the summary interval."""
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    if announced:
        _announced(callback, clock)
    assert coordinator._announce_task is None
    seen = []

    for _ in intervals:
        coordinator._device_unreachable("device unreachable", error)
        seen.append(coordinator.update_interval.total_seconds())
    coordinator._mark_device_answered()

    assert seen == intervals
    assert coordinator.update_interval.total_seconds() == 30


@pytest.mark.parametrize("family", ["soundbar", "monitor", "washer"])
async def test_a_dark_device_outside_the_tv_family_keeps_the_summary_interval(
    hass: HomeAssistant, ssdp, family: str
) -> None:
    """Only a TV's power-on announcement is measured; anything else keeps
    finding its power-on by polling."""
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator._announces = True
    coordinator.device_type_name = family

    for _ in range(4):
        coordinator._pace_dark_polls(TimeoutError("timed out"))
        coordinator._failed_cycles += 1

    assert coordinator.update_interval.total_seconds() == 30


SWITCH = "/sec/tv/switch/binary"
GRACE = LocalThingsCoordinator._ANNOUNCE_GRACE_S
MISSED = "without its SSDP announcement"


class _Clock:
    """The coordinator module's monotonic clock, moved on by `offset` seconds."""

    def __init__(self) -> None:
        self.offset = 0.0

    def monotonic(self) -> float:
        return time.monotonic() + self.offset


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(coordinator_module, "time", types.SimpleNamespace(monotonic=fake.monotonic))
    return fake


async def _cycle(coordinator, session, on: bool) -> float:
    """One poll of a TV whose switch reads `on` (off = network standby)."""
    session.resources[SWITCH] = {"value": on}
    await coordinator.async_refresh()
    return coordinator.update_interval.total_seconds()


async def _dark(coordinator, session, n: int, kind: str = "standby") -> list[float]:
    """`n` dark polls: standby reads, or handshakes that time out (the TV fully off)."""
    seen = []
    for _ in range(n):
        if kind == "standby":
            seen.append(await _cycle(coordinator, session, False))
        else:
            coordinator._device_unreachable("device unreachable", TimeoutError("timed out"))
            seen.append(coordinator.update_interval.total_seconds())
    return seen


def _announced(callback, clock: _Clock) -> None:
    """The scanner replayed the TV at registration, longer ago than the grace."""
    callback(None, _Change.ALIVE)
    clock.offset += GRACE + 1


def _missed_records(caplog) -> list:
    return [r for r in caplog.records if MISSED in r.getMessage()]


@pytest.mark.parametrize("kind", ["standby", "timeout"])
async def test_a_tv_a_poll_finds_on_unannounced_keeps_the_summary_interval(
    hass: HomeAssistant, ssdp, clock: _Clock, caplog, kind: str
) -> None:
    """No alive within the grace of a poll finding the TV on is an announcement
    lost on the way: dark polls stay at the summary interval from then on,
    reported once."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    _announced(callback, clock)
    assert await _dark(coordinator, session, 3, kind) == [30, 60, 120]

    assert await _cycle(coordinator, session, True) == 30
    assert await _dark(coordinator, session, 1, kind) == [30]  # inside the grace
    clock.offset += GRACE + 1
    assert await _dark(coordinator, session, 3, kind) == [30] * 3
    assert len(_missed_records(caplog)) == 1

    await _cycle(coordinator, session, True)
    clock.offset += GRACE + 1
    assert await _dark(coordinator, session, 2, kind) == [30] * 2
    assert len(_missed_records(caplog)) == 1


@pytest.mark.parametrize("when", ["before", "after"])
async def test_a_tv_announced_near_its_answer_keeps_backing_off(
    hass: HomeAssistant, ssdp, clock: _Clock, caplog, when: str
) -> None:
    """An alive shortly before the answer (the look it started) or shortly
    after (a turn-on from Home Assistant answers before the TV's alive)."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    _announced(callback, clock)
    await _dark(coordinator, session, 3)

    if when == "before":
        session.resources[SWITCH] = {"value": True}
        callback(None, _Change.ALIVE)
        async with asyncio.timeout(5):
            await coordinator._announce_task
    else:
        await _cycle(coordinator, session, True)
        clock.offset += GRACE / 2
        callback(None, _Change.ALIVE)
    assert coordinator._failed_cycles == 0
    clock.offset += GRACE + 1

    assert await _dark(coordinator, session, 3) == [30, 60, 120]
    assert _missed_records(caplog) == []


async def test_a_spell_after_an_announced_one_is_judged_on_its_own(
    hass: HomeAssistant, ssdp, clock: _Clock, caplog
) -> None:
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    _announced(callback, clock)
    await _dark(coordinator, session, 3, "timeout")
    await _cycle(coordinator, session, True)
    callback(None, _Change.ALIVE)
    clock.offset += GRACE + 1

    await _dark(coordinator, session, 3, "timeout")
    await _cycle(coordinator, session, True)
    clock.offset += GRACE + 1

    assert await _dark(coordinator, session, 2, "timeout") == [30] * 2
    assert len(_missed_records(caplog)) == 1


async def test_an_alive_whose_look_finds_the_tv_not_up_yet_is_followed_soon(
    hass: HomeAssistant, ssdp, clock: _Clock, caplog
) -> None:
    """The alive can come before the TV answers: the next poll is due in 30 s,
    not at the cap, and its answer counts as announced."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    _announced(callback, clock)
    assert await _dark(coordinator, session, 3) == [30, 60, 120]

    callback(None, _Change.ALIVE)  # its look reads standby
    async with asyncio.timeout(5):
        await coordinator._announce_task
    assert coordinator._failed_cycles == 4
    assert coordinator.update_interval.total_seconds() == 30
    clock.offset += 31
    assert await _dark(coordinator, session, 1) == [30]  # still within a minute of the alive
    clock.offset += 20
    await _cycle(coordinator, session, True)
    clock.offset += GRACE + 1

    assert await _dark(coordinator, session, 3) == [30, 60, 120]
    assert _missed_records(caplog) == []


async def test_an_alive_after_the_tv_went_dark_again_still_counts(
    hass: HomeAssistant, ssdp, clock: _Clock
) -> None:
    """A dark poll inside the grace decides nothing yet."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    _announced(callback, clock)
    await _dark(coordinator, session, 2)
    await _cycle(coordinator, session, True)
    assert await _dark(coordinator, session, 1) == [30]
    clock.offset += GRACE / 2

    callback(None, _Change.ALIVE)  # its look reads standby
    async with asyncio.timeout(5):
        await coordinator._announce_task
    clock.offset += GRACE + 1

    assert await _dark(coordinator, session, 2) == [120, 120]


async def test_an_alive_after_the_grace_is_a_missed_announcement(
    hass: HomeAssistant, ssdp, clock: _Clock
) -> None:
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    _announced(callback, clock)
    await _dark(coordinator, session, 2)
    await _cycle(coordinator, session, True)
    clock.offset += GRACE + 1
    _announced(callback, clock)

    assert await _dark(coordinator, session, 3) == [30] * 3


async def test_a_tv_on_through_one_failed_poll_is_not_judged(
    hass: HomeAssistant, ssdp, clock: _Clock, caplog
) -> None:
    """One failed cycle is a blip of a TV that stayed on, with no alive due."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    _announced(callback, clock)
    await _dark(coordinator, session, 1, "timeout")
    await _cycle(coordinator, session, True)
    clock.offset += GRACE + 1

    assert await _dark(coordinator, session, 3, "timeout") == [30, 60, 120]
    assert _missed_records(caplog) == []


@pytest.mark.parametrize(
    "spell",
    [["dark", "dark", "refused"], ["refused", "refused", "dark", "dark"]],
    ids=["refused-last", "refused-first"],
)
async def test_a_tv_that_refused_the_session_in_the_spell_is_not_judged(
    hass: HomeAssistant, ssdp, clock: _Clock, spell: list[str]
) -> None:
    """A refused session is a TV that is on: the spell was no power-off."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    _announced(callback, clock)
    for cycle in spell:
        error = SessionError() if cycle == "refused" else TimeoutError("timed out")
        coordinator._device_unreachable("device unreachable", error)
    await _cycle(coordinator, session, True)
    clock.offset += GRACE + 1

    assert await _dark(coordinator, session, 3, "timeout") == [30, 60, 120]


async def test_a_tv_that_never_announced_reports_no_missed_announcement(
    hass: HomeAssistant, ssdp, clock: _Clock, caplog
) -> None:
    """Only a TV whose alives reached Home Assistant can miss one."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await _dark(coordinator, session, 2)
    clock.offset += GRACE + 1
    await _cycle(coordinator, session, True)
    clock.offset += GRACE + 1

    assert await _dark(coordinator, session, 2) == [30] * 2
    assert _missed_records(caplog) == []


async def test_unloading_cancels_a_look_and_the_handshake_in_flight(
    hass: HomeAssistant, ssdp
) -> None:
    """A look queued behind the session lock is cancelled, not run against a
    closed entry, and the handshake in flight is cut short."""
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    coordinator._failed_cycles = 2
    handshake = _Handshake()

    with patch.object(coordinator, "async_refresh", AsyncMock()) as look:
        async with coordinator._session_lock:
            callback(None, _Change.ALIVE)
            await asyncio.sleep(0)
            task = coordinator._announce_task
            assert task is not None
            coordinator._connecting = handshake
            closing = asyncio.ensure_future(coordinator.async_close())
            await asyncio.sleep(0)
        async with asyncio.timeout(5):
            await closing
            await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    look.assert_not_awaited()
    assert handshake.cancelled >= 1


async def test_only_a_tv_watches_for_its_announcements(hass: HomeAssistant, ssdp) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_MAC: TV_MAC, CONF_DEVICE_TYPE: "soundbar"},
        unique_id=f"localthings_{MOCK_TV_DI}",
        version=4,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.localthings.coordinator.LocalThingsCoordinator.is_tv",
        return_value=False,
    ):
        await _setup_tv(hass, entry)

    assert ssdp.registered == []
