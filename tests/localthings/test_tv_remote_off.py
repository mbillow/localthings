"""TV keys to a TV that is off fail at once, unless a turn-on is on its way."""

from __future__ import annotations

import asyncio
import threading

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.localthings.const import DOMAIN
from tests.localthings.test_tv_announce import _entry
from tests.localthings.test_tv_media_player_tu7172 import _setup_tv
from tests.localthings.test_tv_remote_beside_reads import _keys, _press

SWITCH = "/sec/tv/switch/binary"


async def _setup(hass: HomeAssistant):
    """A TV with a MAC to wake: its remote stays callable while the TV is off."""
    entry = _entry(hass)
    session = await _setup_tv(hass, entry)
    return hass.data[DOMAIN][entry.entry_id], session


async def _standby(coordinator, session) -> None:
    """One poll that reads the TV's switch off (network standby)."""
    session.resources[SWITCH] = {"value": False}
    await coordinator.async_refresh()


@pytest.mark.parametrize("kind", ["standby", "no_handshake", "two_failed_polls"])
async def test_a_key_to_a_tv_that_is_off_fails_at_once(hass: HomeAssistant, kind: str) -> None:
    coordinator, session = await _setup(hass)
    if kind == "standby":
        await _standby(coordinator, session)
    elif kind == "no_handshake":
        coordinator._session = None
        coordinator._handshake_failed = True
    else:
        for _ in range(2):
            coordinator._device_unreachable("poll failed", TimeoutError("timed out"))

    with pytest.raises(HomeAssistantError) as err:
        async with asyncio.timeout(1):
            await _press(hass, "KEY_VOLUP")

    assert err.value.translation_key == "tv_off"
    assert _keys(session) == []


async def test_one_failed_poll_of_a_tv_that_answers_does_not_refuse_keys(
    hass: HomeAssistant,
) -> None:
    coordinator, session = await _setup(hass)
    coordinator._device_unreachable("poll failed", TimeoutError("timed out"))

    await _press(hass, "KEY_VOLUP")

    assert _keys(session) == ["KEY_VOLUP"]


async def test_a_key_to_a_tv_that_is_on_goes_out(hass: HomeAssistant) -> None:
    coordinator, session = await _setup(hass)
    await _standby(coordinator, session)
    session.resources[SWITCH] = {"value": True}
    await coordinator.async_refresh()

    await _press(hass, "KEY_VOLUP")

    assert _keys(session) == ["KEY_VOLUP"]


@pytest.mark.parametrize("task", ["wake", "announce"])
async def test_a_key_during_a_turn_on_waits_for_the_tv_to_answer(
    hass: HomeAssistant, task: str
) -> None:
    coordinator, session = await _setup(hass)
    await _standby(coordinator, session)
    release = asyncio.Event()

    async def _turn_on() -> None:
        await release.wait()
        session.resources[SWITCH] = {"value": True}
        await coordinator.async_refresh()

    in_flight = asyncio.get_running_loop().create_task(_turn_on())
    if task == "wake":
        coordinator.av._wake_task = in_flight
    else:
        coordinator._announce_task = in_flight
    press = asyncio.get_running_loop().create_task(_press(hass, "KEY_VOLUP"))
    await asyncio.sleep(0.1)
    assert not press.done()
    assert _keys(session) == []

    release.set()
    async with asyncio.timeout(5):
        await press

    assert _keys(session) == ["KEY_VOLUP"]


async def test_a_turn_on_that_leaves_the_tv_off_fails_the_key(hass: HomeAssistant) -> None:
    coordinator, session = await _setup(hass)
    await _standby(coordinator, session)
    release = asyncio.Event()

    async def _no_answer() -> None:
        await release.wait()
        await coordinator.async_refresh()  # still reads standby

    coordinator.av._wake_task = asyncio.get_running_loop().create_task(_no_answer())
    press = asyncio.get_running_loop().create_task(_press(hass, "KEY_VOLUP"))
    await asyncio.sleep(0.1)
    assert not press.done()
    release.set()

    with pytest.raises(HomeAssistantError) as err:
        async with asyncio.timeout(5):
            await press

    assert err.value.translation_key == "tv_off"
    assert _keys(session) == []


async def test_a_key_waits_for_a_turn_on_no_longer_than_its_bound(
    hass: HomeAssistant, monkeypatch
) -> None:
    coordinator, session = await _setup(hass)
    await _standby(coordinator, session)
    monkeypatch.setattr(coordinator.av, "_KEY_WAKE_WAIT_S", 0.2)
    hang = asyncio.Event()
    coordinator.av._wake_task = asyncio.get_running_loop().create_task(hang.wait())

    try:
        with pytest.raises(HomeAssistantError) as err:
            async with asyncio.timeout(2):
                await _press(hass, "KEY_VOLUP")
    finally:
        hang.set()
        await coordinator.av._wake_task

    assert err.value.translation_key == "tv_off"
    assert _keys(session) == []


async def test_only_the_tv_family_refuses_keys_when_dark(hass: HomeAssistant, monkeypatch) -> None:
    coordinator, session = await _setup(hass)
    await _standby(coordinator, session)
    monkeypatch.setattr(coordinator, "is_tv", lambda: False)

    await coordinator.av.async_check_keys()


async def test_a_key_during_the_poll_after_a_standby_read_is_refused(hass: HomeAssistant) -> None:
    """The last poll's standby read holds until the running poll ends."""
    coordinator, session = await _setup(hass)
    await _standby(coordinator, session)
    entered, release = threading.Event(), threading.Event()
    read = session.read

    def _held_read(path, timeout=None):
        if "/" + "/".join(path) == SWITCH:
            entered.set()
            release.wait(5)
        return read(path, timeout)

    session.read = _held_read
    poll = asyncio.get_running_loop().create_task(coordinator.async_refresh())
    try:
        assert await hass.async_add_executor_job(entered.wait, 5)
        with pytest.raises(HomeAssistantError) as err:
            async with asyncio.timeout(1):
                await _press(hass, "KEY_VOLUP")
    finally:
        release.set()
        await poll

    assert err.value.translation_key == "tv_off"
    assert _keys(session) == []
