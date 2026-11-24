"""A TV announcing itself over SSDP is looked at at once (announce.py)."""

from __future__ import annotations

import asyncio
import sys
import types
from enum import Enum
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.announce import async_watch_alive
from custom_components.localthings.const import CONF_HOST, CONF_MAC, DOMAIN
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
    ("announced", "intervals"),
    [(True, [30, 60, 120, 240, 300, 300]), (False, [30] * 6)],
    ids=["announces", "silent"],
)
async def test_a_dark_tv_is_polled_less_often_once_it_announces_itself(
    hass: HomeAssistant, ssdp, announced: bool, intervals: list[int]
) -> None:
    """Its power-on announcement finds it; the polls back off to a cap and
    return to the summary interval when it answers."""
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [(callback, _match)] = ssdp.registered
    if announced:
        callback(None, _Change.ALIVE)  # replayed at registration: the TV is on
    assert coordinator._announce_task is None
    seen = []

    for _ in intervals:
        coordinator._device_unreachable("device unreachable", TimeoutError("timed out"))
        seen.append(coordinator.update_interval.total_seconds())
    coordinator._mark_device_answered()

    assert seen == intervals
    assert coordinator.update_interval.total_seconds() == 30


async def test_a_dark_appliance_keeps_the_summary_interval(hass: HomeAssistant, ssdp) -> None:
    """Only a flat board backs off: an appliance's dark polls also keep its
    last state, which the slower cadence would leave stale for longer."""
    entry = _entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator._announces = True
    coordinator._flat = False

    for _ in range(4):
        coordinator._pace_dark_polls()
        coordinator._failed_cycles += 1

    assert coordinator.update_interval.total_seconds() == 30
