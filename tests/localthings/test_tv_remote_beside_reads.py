"""TV key presses go out beside the background reads instead of waiting for them."""

from __future__ import annotations

import asyncio
import logging
import threading
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.localthings.const import DOMAIN
from tests.localthings.test_tv_media_player_tu7172 import FakeTvSession, _entry, _setup_tv

REMOTE_PATH = ["sec", "tv", "remotecontrol"]


def _keys(session: FakeTvSession) -> list[str]:
    return [
        body["x.com.samsung.tv.keyvalue"] for path, body in session.writes if path == REMOTE_PATH
    ]


async def _press(hass: HomeAssistant, *keys: str) -> None:
    entity_id = hass.states.async_all("remote")[0].entity_id
    await hass.services.async_call(
        "remote", "send_command", {"entity_id": entity_id, "command": list(keys)}, blocking=True
    )


class _FailingOnce(FakeTvSession):
    """A session whose first key write raises; `on_fail` runs just before."""

    def __init__(self, on_fail=None) -> None:
        super().__init__()
        self.key_attempts = 0
        self.closed = 0
        self._on_fail = on_fail

    def write(self, path, body, timeout=None):
        if list(path) == REMOTE_PATH:
            self.key_attempts += 1
            if self.key_attempts == 1:
                if self._on_fail is not None:
                    self._on_fail()
                raise TimeoutError("session operation timed out")
        return super().write(path, body, timeout)

    def close(self):
        self.closed += 1


async def _setup(hass: HomeAssistant, session: FakeTvSession | None = None):
    entry = _entry(hass)
    first = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if session is not None:
        session.resources = first.resources
        coordinator._session = session
    coordinator._RECONNECT_PAUSE_S = 0
    return coordinator, session or first


async def test_a_key_goes_out_while_a_background_read_holds_the_session(
    hass: HomeAssistant,
) -> None:
    coordinator, session = await _setup(hass)

    async with coordinator._session_lock:
        async with asyncio.timeout(5):
            await _press(hass, "KEY_CHUP")

    assert _keys(session) == ["KEY_CHUP"]


async def test_other_writes_still_wait_for_the_session(hass: HomeAssistant) -> None:
    """Only key presses skip the session lock; a state write waits for the read."""
    coordinator, session = await _setup(hass)
    player = hass.states.async_all("media_player")[0].entity_id
    before = len(session.writes)

    async with coordinator._session_lock:
        task = hass.async_create_task(
            hass.services.async_call(
                "media_player",
                "volume_set",
                {"entity_id": player, "volume_level": 0.3},
                blocking=True,
            )
        )
        await asyncio.sleep(0.1)
        assert len(session.writes) == before
    async with asyncio.timeout(5):
        await task

    assert len(session.writes) > before


async def test_keys_keep_their_order_across_calls(hass: HomeAssistant) -> None:
    """A second call's key waits for the first call's key to be answered."""
    entered = threading.Event()
    release = threading.Event()

    class _Held(FakeTvSession):
        def write(self, path, body, timeout=None):
            if body.get("x.com.samsung.tv.keyvalue") == "KEY_1":
                entered.set()
                release.wait(5)
            return super().write(path, body, timeout)

    _coordinator, session = await _setup(hass, _Held())
    first = hass.async_create_task(_press(hass, "KEY_1"))
    assert await hass.async_add_executor_job(entered.wait, 5)
    second = hass.async_create_task(_press(hass, "KEY_2"))
    await asyncio.sleep(0.1)
    assert _keys(session) == []

    release.set()
    async with asyncio.timeout(5):
        await asyncio.gather(first, second)

    assert _keys(session) == ["KEY_1", "KEY_2"]


async def test_a_failed_key_write_reconnects_and_resends_once(hass: HomeAssistant) -> None:
    """The existing rule for a write on a dead session: close it, reconnect, resend once."""
    coordinator, failing = await _setup(hass, _FailingOnce())
    fresh = FakeTvSession()
    fresh.resources = failing.resources

    with patch(
        "custom_components.localthings.coordinator.create_transport", return_value=fresh
    ) as connect:
        async with asyncio.timeout(5):
            await _press(hass, "KEY_CHUP")

    assert failing.key_attempts == 1
    assert failing.closed == 1
    connect.assert_called_once()
    assert _keys(fresh) == ["KEY_CHUP"]
    assert coordinator._session is fresh


async def test_a_session_the_poll_replaced_meanwhile_is_kept(hass: HomeAssistant) -> None:
    """The key failed on a session a poll has since replaced: it is resent on the new one,
    which is neither closed nor reconnected."""
    fresh = _FailingOnce()
    fresh.key_attempts = 1  # writes normally

    def _poll_reconnected() -> None:
        coordinator._session = fresh

    coordinator, failing = await _setup(hass, _FailingOnce(on_fail=_poll_reconnected))
    fresh.resources = failing.resources

    with patch("custom_components.localthings.coordinator.create_transport") as connect:
        async with asyncio.timeout(5):
            await _press(hass, "KEY_CHUP")

    connect.assert_not_called()
    assert fresh.closed == 0
    assert _keys(fresh) == ["KEY_CHUP"]
    assert coordinator._session is fresh


async def test_a_key_with_no_open_session_connects_without_a_failure(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """No session to send beside the reads on: the locked path opens one, no reconnect."""
    coordinator, session = await _setup(hass)
    coordinator._session = None
    caplog.clear()

    with patch("custom_components.localthings.coordinator.create_transport", return_value=session):
        async with asyncio.timeout(5):
            await _press(hass, "KEY_CHUP")

    assert _keys(session) == ["KEY_CHUP"]
    assert not [
        r
        for r in caplog.records
        if r.name.startswith("custom_components.localthings") and r.levelno >= logging.WARNING
    ]


async def test_a_failed_key_closes_its_session_only_after_the_read_lets_go(
    hass: HomeAssistant,
) -> None:
    coordinator, failing = await _setup(hass, _FailingOnce())
    fresh = FakeTvSession()
    fresh.resources = failing.resources

    with patch("custom_components.localthings.coordinator.create_transport", return_value=fresh):
        async with coordinator._session_lock:
            task = hass.async_create_task(_press(hass, "KEY_CHUP"))
            await asyncio.sleep(0.1)
            assert failing.key_attempts == 1
            assert failing.closed == 0
            assert not task.done()
        async with asyncio.timeout(5):
            await task

    assert failing.closed == 1
    assert _keys(fresh) == ["KEY_CHUP"]


async def test_a_key_after_a_failed_one_waits_for_its_resend(hass: HomeAssistant) -> None:
    """The failed key's resend goes out first, then the next key."""
    failed = threading.Event()
    _coordinator, failing = await _setup(hass, _FailingOnce(on_fail=failed.set))
    fresh = FakeTvSession()
    fresh.resources = failing.resources

    with patch("custom_components.localthings.coordinator.create_transport", return_value=fresh):
        async with _coordinator._session_lock:
            first = hass.async_create_task(_press(hass, "KEY_1"))
            assert await hass.async_add_executor_job(failed.wait, 5)
            second = hass.async_create_task(_press(hass, "KEY_2"))
            await asyncio.sleep(0.1)
            assert _keys(failing) == []
            assert _keys(fresh) == []
        async with asyncio.timeout(5):
            await asyncio.gather(first, second)

    assert _keys(fresh) == ["KEY_1", "KEY_2"]


async def test_a_refused_key_is_reported_on_a_kept_session(hass: HomeAssistant) -> None:
    """A refusal is the TV's answer, not a dead session: no reconnect."""

    class _Refusing(_FailingOnce):
        def write(self, path, body, timeout=None):
            if list(path) == REMOTE_PATH:
                self.key_attempts += 1
                return 0x80, None
            return super().write(path, body, timeout)

    _coordinator, refusing = await _setup(hass, _Refusing())

    with (
        patch("custom_components.localthings.coordinator.create_transport") as connect,
        pytest.raises(HomeAssistantError) as err,
    ):
        async with asyncio.timeout(5):
            await _press(hass, "KEY_CHUP")

    assert err.value.translation_key == "command_refused"
    assert refusing.key_attempts == 1
    assert refusing.closed == 0
    connect.assert_not_called()


async def test_a_key_failing_again_after_the_reconnect_is_reported(hass: HomeAssistant) -> None:
    class _Dead(_FailingOnce):
        def write(self, path, body, timeout=None):
            if list(path) == REMOTE_PATH:
                self.key_attempts += 1
                raise TimeoutError("session operation timed out")
            return super().write(path, body, timeout)

    _coordinator, dead = await _setup(hass, _Dead())
    again = _Dead()
    again.resources = dead.resources

    with (
        patch("custom_components.localthings.coordinator.create_transport", return_value=again),
        pytest.raises(HomeAssistantError) as err,
    ):
        async with asyncio.timeout(5):
            await _press(hass, "KEY_CHUP")

    assert err.value.translation_key == "command_failed"
    assert (dead.key_attempts, again.key_attempts, dead.closed) == (1, 1, 1)


async def test_a_held_key_presses_and_releases_beside_a_read(hass: HomeAssistant) -> None:
    coordinator, session = await _setup(hass)
    entity_id = hass.states.async_all("remote")[0].entity_id

    async with coordinator._session_lock:
        async with asyncio.timeout(5):
            await hass.services.async_call(
                "remote",
                "send_command",
                {"entity_id": entity_id, "command": ["KEY_VOLUP"], "hold_secs": 0.05},
                blocking=True,
            )

    statuses = [b["x.com.samsung.tv.keystatus"] for p, b in session.writes if p == REMOTE_PATH]
    assert statuses == ["press", "release"]
