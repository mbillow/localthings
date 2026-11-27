"""TV writes the family measured beside reads skip the session lock; the rest wait for it."""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.localthings.registry.by_type import _REGISTRY_BY_KEY
from custom_components.localthings.registry.entities import MediaPlayerDesc
from tests.localthings.test_tv_remote_beside_reads import _keys, _press, _setup

AUDIO = ["sec", "tv", "audio"]


def _player(hass: HomeAssistant) -> str:
    return hass.states.async_all("media_player")[0].entity_id


def _picture(hass: HomeAssistant) -> str:
    return next(
        s.entity_id for s in hass.states.async_all("select") if "picture_mode" in s.entity_id
    )


def _call(hass, domain, service, data) -> asyncio.Task:
    """Start a blocking service call as a task."""
    return hass.async_create_task(hass.services.async_call(domain, service, data, blocking=True))


@pytest.mark.parametrize(
    ("domain", "service", "data", "path"),
    [
        ("media_player", "volume_mute", {"is_volume_muted": True}, AUDIO),
        ("media_player", "volume_set", {"volume_level": 0.3}, AUDIO),
        ("media_player", "select_source", {"source": "HDMI 1"}, ["sec", "tv", "inputsource"]),
        ("select", "select_option", None, ["sec", "tv", "mode", "picture"]),
    ],
)
async def test_a_measured_write_returns_while_a_read_holds_the_session(
    hass: HomeAssistant, domain, service, data, path
) -> None:
    """It goes out beside the read, and its confirming refresh runs after the
    call returns, once the read lets go."""
    coordinator, session = await _setup(hass)
    if domain == "select":
        entity_id = _picture(hass)
        state = hass.states.get(entity_id)
        assert state is not None
        options = state.attributes["options"]
        current = state.state
        data = {"option": next(o for o in options if o != current)}
    else:
        entity_id = _player(hass)

    async with coordinator._session_lock:
        reads_held = len(session.reads)
        async with asyncio.timeout(2):
            await hass.services.async_call(
                domain, service, {"entity_id": entity_id, **data}, blocking=True
            )
        assert [p for p, _ in session.writes][-1] == path
        assert len(session.reads) == reads_held
    await hass.async_block_till_done()

    assert len(session.reads) > reads_held


@pytest.mark.parametrize(
    ("service", "data"),
    [("select_sound_mode", {"sound_mode": "Game"}), ("turn_off", {})],
)
async def test_other_writes_wait_for_the_read(hass: HomeAssistant, service, data) -> None:
    coordinator, session = await _setup(hass)
    before = len(session.writes)

    async with coordinator._session_lock:
        task = _call(hass, "media_player", service, {"entity_id": _player(hass), **data})
        await asyncio.sleep(0.1)
        assert len(session.writes) == before
    async with asyncio.timeout(5):
        await task

    assert len(session.writes) > before


@pytest.mark.parametrize("kind", ["resend_unsafe", "prepared"])
async def test_a_write_that_must_not_be_resent_or_is_prepared_waits(
    hass: HomeAssistant, kind: str
) -> None:
    """Either needs the locked path: a liveness check first, or a payload
    worked out once the session is ready."""
    coordinator, session = await _setup(hass)
    bound = next(b for b in coordinator.bound if isinstance(b.desc, MediaPlayerDesc))
    kwargs = {}
    if kind == "resend_unsafe":
        bound = dataclasses.replace(
            bound, desc=dataclasses.replace(bound.desc, resend_unsafe_fn=lambda _p: True)
        )
    else:
        kwargs["prepare_payload"] = lambda: ("mute", True)
    before = len(session.writes)

    async with coordinator._session_lock:
        task = hass.async_create_task(
            coordinator.async_send_command(bound, ("mute", True), refresh=False, **kwargs)
        )
        await asyncio.sleep(0.1)
        assert len(session.writes) == before
    async with asyncio.timeout(5):
        await task

    assert session.writes[-1] == (AUDIO, {"mute": True})


def test_only_the_tv_family_writes_beside_reads() -> None:
    families = {
        name: registry.beside_read_hrefs
        for name, registry in _REGISTRY_BY_KEY.items()
        if registry.beside_read_hrefs
    }
    assert set(families) == {"tv"}
    assert families["tv"] == {
        "/sec/tv/remotecontrol",
        "/sec/tv/audio",
        "/sec/tv/inputsource",
        "/sec/tv/mode/picture",
    }


async def test_a_tv_known_only_from_its_entry_writes_beside_reads(hass: HomeAssistant) -> None:
    """An entry loaded while the TV is dark has no live device type yet."""
    coordinator, _session = await _setup(hass)
    coordinator.device_type_name = None

    assert coordinator._beside_reads("/sec/tv/audio")
    assert not coordinator._beside_reads("/sec/tv/switch/binary")


async def test_the_live_family_wins_over_the_stored_one(hass: HomeAssistant) -> None:
    coordinator, _session = await _setup(hass)
    coordinator.device_type_name = "monitor"

    assert not coordinator._beside_reads("/sec/tv/audio")


async def test_a_refused_write_does_not_hold_up_the_keys_behind_it(hass: HomeAssistant) -> None:
    """A refusal's confirming read waits for the background read; a key sent
    meanwhile still goes out beside it."""
    coordinator, session = await _setup(hass)
    coordinator._CONFIRM_DELAY_S = 0
    write = session.write

    def _refuse_mute(path, body, timeout=None):
        if list(path) == AUDIO:
            session.writes.append((list(path), body))
            return 0x44, {"controlResponse": {"result": False}}
        return write(path, body, timeout)

    session.write = _refuse_mute
    async with coordinator._session_lock:
        mute = _call(
            hass,
            "media_player",
            "volume_mute",
            {"entity_id": _player(hass), "is_volume_muted": True},
        )
        await asyncio.sleep(0.1)
        assert not mute.done()
        async with asyncio.timeout(2):
            await _press(hass, "KEY_CHUP")
        assert _keys(session) == ["KEY_CHUP"]
        assert [p for p, _ in session.writes][-2:] == [AUDIO, ["sec", "tv", "remotecontrol"]]
    async with asyncio.timeout(5):
        [result] = await asyncio.gather(mute, return_exceptions=True)

    assert getattr(result, "translation_key", None) == "command_refused"


async def test_a_refused_write_the_tv_applied_is_confirmed_once_the_read_lets_go(
    hass: HomeAssistant,
) -> None:
    """The TV answers result=false but mutes: the confirming read shows it, so
    the call succeeds; that read waits for the background one."""
    coordinator, session = await _setup(hass)
    coordinator._CONFIRM_DELAY_S = 0
    write = session.write

    def _refuse_but_apply(path, body, timeout=None):
        if list(path) == AUDIO:
            write(path, body, timeout)
            return 0x44, {"controlResponse": {"result": False}}
        return write(path, body, timeout)

    session.write = _refuse_but_apply
    async with coordinator._session_lock:
        mute = _call(
            hass,
            "media_player",
            "volume_mute",
            {"entity_id": _player(hass), "is_volume_muted": True},
        )
        await asyncio.sleep(0.1)
        assert session.writes[-1] == (AUDIO, {"mute": True})
        assert not mute.done()
    async with asyncio.timeout(5):
        await mute


async def test_unloading_cancels_a_pending_confirm(hass: HomeAssistant) -> None:
    coordinator, _session = await _setup(hass)
    entry = coordinator._entry
    started, hang = asyncio.Event(), asyncio.Event()

    async def _held_refresh() -> None:
        started.set()
        await hang.wait()

    with patch.object(coordinator, "async_request_refresh", _held_refresh):
        await hass.services.async_call(
            "media_player",
            "volume_mute",
            {"entity_id": _player(hass), "is_volume_muted": True},
            blocking=True,
        )
        async with asyncio.timeout(2):
            await started.wait()
        confirms = [t for t in entry._background_tasks if t.get_name() == "localthings_confirm"]
        assert confirms
        async with asyncio.timeout(5):
            assert await hass.config_entries.async_unload(entry.entry_id)
        await asyncio.sleep(0)

    assert all(t.cancelled() for t in confirms)
