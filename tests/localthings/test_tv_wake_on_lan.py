"""Turning a switched-off TV on with Wake-on-LAN.

A TV that is off has no OCF stack, so the power write can't reach it; the
media player and the remote send a magic packet instead, and the
coordinator then polls quickly for the TV to come back on its new port.
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import network
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import wol
from custom_components.localthings.const import CONF_MAC, DOMAIN
from custom_components.localthings.coordinator import DeviceSilent
from custom_components.localthings.registry.entities import MediaPlayerDesc
from custom_components.localthings.registry.flat import BoardInStandby
from tests.localthings.test_soundbar import ENTRY_DATA as SOUNDBAR_DATA
from tests.localthings.test_soundbar import _setup_soundbar
from tests.localthings.test_tv_media_player_tu7172 import ENTRY_DATA, MOCK_TV_DI, _setup_tv

TV_MAC = "02:00:00:00:00:01"
_POWER = (["sec", "tv", "switch", "binary"], {"value": True})


# --- the packet --------------------------------------------------------------


def test_magic_packet_is_six_ff_then_the_mac_sixteen_times() -> None:
    packet = wol.magic_packet("02-00-00-00-00-01")

    assert packet[:6] == b"\xff" * 6
    assert packet[6:] == bytes.fromhex("020000000001") * 16
    assert len(packet) == 102


@pytest.mark.parametrize("bad", ["", "02:00:00:00:00", "zz:00:00:00:00:01"])
def test_magic_packet_refuses_what_is_not_a_mac(bad) -> None:
    with pytest.raises(ValueError):
        wol.magic_packet(bad)


def test_send_repeats_to_every_target_and_skips_a_refusal(monkeypatch) -> None:
    sent: list[tuple[bytes, tuple[str, int]]] = []

    class _Socket:
        def __init__(self, *args) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            pass

        def setsockopt(self, *args) -> None:
            pass

        def sendto(self, payload, address) -> None:
            if address[0] == "10.9.9.9":
                raise OSError("no route")
            sent.append((payload, address))

    monkeypatch.setattr(wol.socket, "socket", _Socket)
    sleeps: list[float] = []

    count = wol.send_magic_packet(
        TV_MAC, ["255.255.255.255", "10.9.9.9", "192.0.2.103"], sleep=sleeps.append
    )

    assert count == 6
    assert [address for _p, address in sent] == [
        ("255.255.255.255", 9),
        ("192.0.2.103", 9),
    ] * 3
    assert sleeps == [wol.WOL_INTERVAL_S] * 2
    assert all(payload == wol.magic_packet(TV_MAC) for payload, _a in sent)


async def test_targets_fall_back_to_the_limited_broadcast(hass: HomeAssistant) -> None:
    with patch(
        "homeassistant.components.network.async_get_ipv4_broadcast_addresses",
        side_effect=RuntimeError("no network"),
    ):
        assert await wol.async_targets(hass, "192.0.2.103") == [
            "255.255.255.255",
            "192.0.2.103",
        ]


async def test_targets_include_the_default_adapter_subnet_broadcast(hass: HomeAssistant) -> None:
    """HA's real helper omits the subnet broadcast with only the default adapter enabled."""
    adapters = [
        {
            "enabled": True,
            "default": True,
            "ipv4": [{"address": "192.0.2.10", "network_prefix": 24}],
        }
    ]
    with patch.object(network, "async_get_adapters", AsyncMock(return_value=adapters)):
        assert await wol.async_targets(hass, "192.0.2.103") == [
            "192.0.2.255",
            "255.255.255.255",
            "192.0.2.103",
        ]


async def test_targets_deduplicate_enabled_subnets_and_skip_disabled_adapters(
    hass: HomeAssistant,
) -> None:
    adapters = [
        {
            "enabled": True,
            "default": True,
            "ipv4": [
                {"address": "192.0.2.10", "network_prefix": 24},
                {"address": "192.0.2.11", "network_prefix": 24},
            ],
        },
        {
            "enabled": False,
            "default": False,
            "ipv4": [{"address": "198.51.100.10", "network_prefix": 24}],
        },
    ]
    with patch.object(network, "async_get_adapters", AsyncMock(return_value=adapters)):
        assert await wol.async_targets(hass, "192.0.2.103") == [
            "192.0.2.255",
            "255.255.255.255",
            "192.0.2.103",
        ]


@pytest.mark.parametrize(
    ("address", "prefix"), [("127.0.0.1", 8), ("192.0.2.10", 31), ("192.0.2.10", 32)]
)
async def test_targets_do_not_add_subnet_broadcasts_for_loopback_or_point_to_point(
    hass: HomeAssistant, address: str, prefix: int
) -> None:
    adapters = [
        {
            "enabled": True,
            "default": True,
            "ipv4": [{"address": address, "network_prefix": prefix}],
        }
    ]
    with patch.object(network, "async_get_adapters", AsyncMock(return_value=adapters)):
        assert await wol.async_targets(hass, "192.0.2.103") == [
            "255.255.255.255",
            "192.0.2.103",
        ]


# --- turn-on -----------------------------------------------------------------


def _tv_entry(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, **extra},
        unique_id=f"localthings_{MOCK_TV_DI}",
        version=4,
    )
    entry.add_to_hass(hass)
    return entry


def _media_bound(coordinator):
    return next(b for b in coordinator.bound if isinstance(b.desc, MediaPlayerDesc))


def _switch_off(coordinator) -> None:
    """What a night off leaves behind: no session, failed cycles counted."""
    coordinator._session = None
    coordinator._failed_cycles = 40
    coordinator._next_rediscovery_ts = 10**9


@pytest.fixture
def packets():
    """Record magic packets instead of sending them."""
    sent: list[tuple[str, list[str]]] = []

    def _send(mac, targets, **kwargs):
        sent.append((mac, list(targets)))
        return len(targets)

    with (
        patch.object(wol, "send_magic_packet", _send),
        patch.object(wol, "async_targets", AsyncMock(return_value=["255.255.255.255"])),
    ):
        yield sent


@pytest.fixture
def no_follow():
    """Hold the post-wake loop open until the test releases it."""
    release = asyncio.Event()

    async def _follow(self, *_args) -> None:
        await release.wait()

    with patch(
        "custom_components.localthings.av.AVControl._async_follow_wake",
        _follow,
    ):
        yield release
    release.set()


async def test_an_answering_tv_that_is_on_needs_nothing(hass: HomeAssistant, packets) -> None:
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    await coordinator.av.async_power_on(_media_bound(coordinator))

    assert _POWER not in session.writes
    assert packets == []


async def test_a_tv_that_is_off_is_woken_once(hass: HomeAssistant, packets, no_follow) -> None:
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    _switch_off(coordinator)
    player = next(iter(hass.data["media_player"].entities))

    await player.async_turn_on()
    remote_id = hass.states.async_all("remote")[0].entity_id
    await hass.services.async_call("remote", "turn_on", {"entity_id": remote_id}, blocking=True)

    assert packets == [(TV_MAC, ["255.255.255.255"])]
    assert _POWER not in session.writes
    no_follow.set()
    await hass.async_block_till_done()


@pytest.mark.parametrize("outcome", ["success", "failure", "close"])
async def test_concurrent_wake_requests_share_startup(
    hass: HomeAssistant, packets, no_follow, outcome: str
) -> None:
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    _switch_off(coordinator)
    started, finish = asyncio.Event(), asyncio.Event()
    attempts = 0
    send_packet = wol.send_magic_packet

    async def targets(*args):
        started.set()
        await finish.wait()
        return ["255.255.255.255"]

    def send(*args):
        nonlocal attempts
        attempts += 1
        if outcome == "failure" and attempts == 1:
            raise OSError("no route")
        return send_packet(*args)

    with (
        patch.object(wol, "async_targets", AsyncMock(side_effect=targets)) as resolve,
        patch.object(wol, "send_magic_packet", send),
    ):
        first = asyncio.create_task(coordinator.av.async_power_on(_media_bound(coordinator)))
        try:
            await started.wait()
            second = asyncio.create_task(coordinator.av.async_power_on(_media_bound(coordinator)))
            await asyncio.sleep(0)
            resolve.assert_awaited_once()
            assert not second.done()
            if outcome == "close":
                coordinator.av.close()
        finally:
            finish.set()
        if outcome == "failure":
            with pytest.raises(HomeAssistantError):
                await first
        else:
            await first
        await second

    if outcome == "close":
        assert not packets
        assert coordinator.av._wake_task is None
    else:
        assert packets == [(TV_MAC, ["255.255.255.255"])]
        task = coordinator.av._wake_task
        assert task is not None
        coordinator.av.close()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()


async def test_the_registry_mac_wakes_an_entry_that_stores_none(
    hass: HomeAssistant, packets, no_follow
) -> None:
    entry = _tv_entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.device_key)},
        connections={(dr.CONNECTION_NETWORK_MAC, TV_MAC)},
    )
    _switch_off(coordinator)

    await coordinator.av.async_power_on(_media_bound(coordinator))

    assert packets == [(TV_MAC, ["255.255.255.255"])]


async def test_a_tv_with_no_mac_keeps_the_write_and_its_error(hass: HomeAssistant, packets) -> None:
    entry = _tv_entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    _switch_off(coordinator)

    with (
        patch.object(
            coordinator, "async_send_command", AsyncMock(side_effect=HomeAssistantError("off"))
        ),
        pytest.raises(HomeAssistantError),
    ):
        await coordinator.av.async_power_on(_media_bound(coordinator))
    assert packets == []


async def test_a_tv_switched_off_since_the_last_poll_is_woken(
    hass: HomeAssistant, packets, no_follow
) -> None:
    """The session still looks live, but the switch it last read says off."""
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    _switch_off(coordinator)

    await coordinator.av.async_power_on(_media_bound(coordinator))

    assert packets == [(TV_MAC, ["255.255.255.255"])]
    assert _POWER not in session.writes


async def test_a_tv_without_a_mac_cannot_be_turned_on(hass: HomeAssistant, packets) -> None:
    """OCF can't wake a TV, so turn-on isn't offered or sent (#834)."""
    entry = _tv_entry(hass)
    session = await _setup_tv(hass, entry)
    player = next(iter(hass.data["media_player"].entities))

    assert not player.supported_features & MediaPlayerEntityFeature.TURN_ON
    with pytest.raises(ServiceValidationError):
        await player.async_turn_on()
    assert _POWER not in session.writes
    assert packets == []


async def test_a_soundbar_is_never_woken(hass: HomeAssistant, packets) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**SOUNDBAR_DATA, CONF_MAC: TV_MAC},
        unique_id="localthings_soundbar",
        version=4,
    )
    entry.add_to_hass(hass)
    await _setup_soundbar(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    _switch_off(coordinator)

    with patch.object(coordinator, "async_send_command", AsyncMock()) as write:
        await coordinator.av.async_power_on(_media_bound(coordinator))

    write.assert_awaited_once()
    assert packets == []


async def test_after_a_wake_the_tv_is_looked_up_at_once_until_it_answers(
    hass: HomeAssistant, packets
) -> None:
    """The night's backoff must not hold the lookup back, and the loop
    ends as soon as a poll gets through."""
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    _switch_off(coordinator)
    coordinator.av._WAKE_REFRESH_S = 0
    lookups_due: list[bool] = []

    async def _refresh() -> None:
        lookups_due.append(coordinator._next_rediscovery_ts == 0.0)
        if len(lookups_due) == 3:
            coordinator._session = object()
            coordinator._failed_cycles = 0

    with patch.object(coordinator, "async_refresh", _refresh):
        await coordinator.av.async_power_on(_media_bound(coordinator))
        await coordinator.av._wake_task

    assert lookups_due == [True, True, True]
    assert coordinator.av._wake_task.done()


class _Handshake:
    """A transport whose handshake is in flight; records its cancellation."""

    def __init__(self) -> None:
        self.cancelled = 0

    def cancel_connect(self) -> None:
        self.cancelled += 1


async def test_a_wake_cancels_the_handshake_in_flight_and_looks_at_once(
    hass: HomeAssistant, packets
) -> None:
    """The handshake in flight started while the TV was off; the first look
    after the packet starts at once, not after a fixed wait."""
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    _switch_off(coordinator)
    coordinator.av._WAKE_REFRESH_S = 30.0
    handshake = _Handshake()
    coordinator._connecting = handshake
    looks: list[float] = []

    async def _refresh() -> None:
        looks.append(time.monotonic())
        coordinator._session = object()
        coordinator._failed_cycles = 0

    with patch.object(coordinator, "async_refresh", _refresh):
        sent = time.monotonic()
        await coordinator.av.async_power_on(_media_bound(coordinator))
        async with asyncio.timeout(5):
            await coordinator.av._wake_task

    assert handshake.cancelled == 1
    assert len(looks) == 1
    assert looks[0] - sent < 1.0


async def test_looks_after_a_wake_are_paced_only_when_they_fail_at_once(
    hass: HomeAssistant, packets
) -> None:
    """A look that fails at once waits out the rest of the pacing step; one
    that took longer than the step goes again straight away."""
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    _switch_off(coordinator)
    step = 0.2
    coordinator.av._WAKE_REFRESH_S = step
    looks: list[tuple[float, float]] = []
    durations = [0.0, 0.3, 0.0]

    async def _refresh() -> None:
        start = time.monotonic()
        await asyncio.sleep(durations[len(looks)])
        looks.append((start, time.monotonic()))
        if len(looks) == len(durations):
            coordinator._session = object()
            coordinator._failed_cycles = 0

    with patch.object(coordinator, "async_refresh", _refresh):
        await coordinator.av.async_power_on(_media_bound(coordinator))
        async with asyncio.timeout(5):
            await coordinator.av._wake_task

    assert len(looks) == 3
    # Failed at once: the second look starts a step after the first began.
    assert looks[1][0] - looks[0][0] >= step - 0.02
    # Took longer than a step: the third look follows at once.
    assert looks[2][0] - looks[1][1] < step / 2


# --- a TV that is off reads as off, not unavailable -----------------------------


class _DarkTransport:
    """A switched-off TV: the handshake never completes."""

    def connect(self) -> None:
        raise TimeoutError("handshake timed out")

    def close(self) -> None:
        pass


async def _load_while_off(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Bank a snapshot with the TV on, then restart with it off."""
    await _setup_tv(hass, entry)
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with patch(
        "custom_components.localthings.coordinator.create_transport",
        return_value=_DarkTransport(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        # The first refresh is a background task, which block_till_done
        # doesn't wait for; whichever finishes first, the entities show off.
        await hass.data[DOMAIN][entry.entry_id].async_refresh()
        await hass.async_block_till_done()


def _state(hass: HomeAssistant, domain: str):
    return hass.states.async_all(domain)[0]


async def test_a_tv_loaded_while_off_shows_off_and_can_be_woken(
    hass: HomeAssistant, packets, no_follow
) -> None:
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    await _load_while_off(hass, entry)

    assert _state(hass, "media_player").state == "off"
    assert _state(hass, "remote").state == "off"
    # Everything else would only show what the TV said before it went off.
    others = [s for s in hass.states.async_all() if s.domain not in ("media_player", "remote")]
    assert others
    assert all(s.state == "unavailable" for s in others)

    await hass.services.async_call(
        "media_player",
        "turn_on",
        {"entity_id": _state(hass, "media_player").entity_id},
        blocking=True,
    )
    assert packets == [(TV_MAC, ["255.255.255.255"])]


async def test_a_tv_with_no_mac_loaded_while_off_stays_unavailable(hass: HomeAssistant) -> None:
    """Nothing could turn it on, so off would be a promise it can't keep."""
    entry = _tv_entry(hass)
    await _load_while_off(hass, entry)

    assert _state(hass, "media_player").state == "unavailable"
    assert _state(hass, "remote").state == "unavailable"


async def test_a_tv_that_goes_dark_shows_off_not_its_last_state(hass: HomeAssistant) -> None:
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))
    assert player.state != "off"  # the fixture's TV is on

    coordinator._failed_cycles = 1  # the cached state is all that's left

    assert player.available
    assert player.state == "off"


async def test_a_tv_with_no_mac_that_goes_dark_is_unavailable(hass: HomeAssistant) -> None:
    """Neither off (nothing could wake it) nor its last state, nor its sensors'."""
    entry = _tv_entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._flat

    def _dark():
        coordinator._handshake_failed = True
        raise TimeoutError("handshake timed out")

    with (
        patch.object(coordinator, "_poll_once", side_effect=_dark),
        patch.object(coordinator, "_close_session"),
    ):
        await coordinator.async_refresh()

    entities = [e for platform in ("media_player", "sensor") for e in hass.data[platform].entities]
    assert entities
    assert not any(entity.available for entity in entities)


async def test_a_routine_reconnect_does_not_flicker_the_tv_off(hass: HomeAssistant) -> None:
    """Close, pause, handshake: no session for a moment, but nothing failed."""
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    player = next(iter(hass.data["media_player"].entities))

    coordinator._session = None
    assert not coordinator.av.reads_as_off()
    assert player.state != "off"

    coordinator._handshake_failed = True  # the TV went dark under the reconnect
    assert coordinator.av.reads_as_off()


# --- network standby -----------------------------------------------------------

_SWITCH = "/sec/tv/switch/binary"


def _others(hass: HomeAssistant) -> list:
    return [
        s
        for s in hass.states.async_all()
        if s.domain not in ("media_player", "remote") and not s.entity_id.endswith("last_response")
    ]


async def test_a_tv_in_standby_reads_off_without_an_error(hass: HomeAssistant, caplog) -> None:
    """Between its periodic wakes from standby the TV answers its switch,
    off: shown off, its other entities unavailable, the session kept, and
    no error logged for it."""
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert all(s.state != "unavailable" for s in _others(hass))
    session.resources[_SWITCH] = {"value": False}

    with patch(
        "custom_components.localthings.coordinator.create_transport", return_value=session
    ) as connect:
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    connect.assert_not_called()
    assert coordinator._session is session
    assert _state(hass, "media_player").state == "off"
    assert _state(hass, "remote").state == "off"
    assert _others(hass)
    assert all(s.state == "unavailable" for s in _others(hass))
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    session.resources[_SWITCH] = {"value": True}
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator._standby_cycle
    assert _state(hass, "media_player").state != "off"
    assert all(s.state != "unavailable" for s in _others(hass))


@pytest.mark.parametrize(
    ("error", "traced"),
    [
        (TimeoutError("timed out"), False),
        (ConnectionError("refused"), False),
        (DeviceSilent("no answer"), False),
        (BoardInStandby("reads off"), False),
        (KeyError("bug"), True),
    ],
    ids=["timeout", "refused", "silent", "standby", "unexpected"],
)
async def test_a_tv_going_dark_logs_no_traceback(
    hass: HomeAssistant, caplog, error: Exception, traced: bool
) -> None:
    """Off, asleep or refusing is routine and logs one line per cycle; only an
    unexpected error carries its traceback, even at debug."""
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    with caplog.at_level(logging.DEBUG):
        for _ in range(2):
            coordinator._device_unreachable("device unreachable", error)

    assert any(r.exc_info for r in caplog.records) is traced


@pytest.mark.parametrize(
    ("switch", "reconnects"), [(False, False), (True, True)], ids=["read-off", "read-on"]
)
async def test_a_tv_gone_silent_after_reading_off_is_not_reconnected(
    hass: HomeAssistant, switch: bool, reconnects: bool
) -> None:
    """Standby ends in dark: a switch that last read off means the silence is
    the TV, not the session, so no reconnect that could only time out. A
    switch last read on keeps the routine reconnect."""
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator._observe.apply(_SWITCH, {"value": switch}, source="poll")

    with (
        patch.object(session, "read", side_effect=TimeoutError("timed out")),
        patch(
            "custom_components.localthings.coordinator.create_transport", return_value=session
        ) as connect,
        patch.object(coordinator, "_RECONNECT_PAUSE_S", 0),
        patch.object(coordinator, "_poll_once", wraps=coordinator._poll_once) as poll,
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert connect.called is reconnects
    assert poll.call_count == (2 if reconnects else 1)
    assert coordinator._failed_cycles == 1
    assert _state(hass, "media_player").state == "off"


async def _in_standby(hass: HomeAssistant):
    """A woken-in-standby TV: its switch reads off on a session kept open."""
    entry = _tv_entry(hass, **{CONF_MAC: TV_MAC})
    session = await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.av._WAKE_REFRESH_S = 0.0
    session.resources[_SWITCH] = {"value": False}
    await coordinator.async_refresh()
    assert coordinator._standby_cycle
    assert coordinator._session is session
    return coordinator, session


async def _turn_on(hass: HomeAssistant, coordinator) -> None:
    await hass.services.async_call(
        "media_player", "turn_on", {"entity_id": _state(hass, "media_player").entity_id}
    )
    await hass.async_block_till_done()
    async with asyncio.timeout(5):
        await coordinator.av._wake_task


async def test_a_tv_awake_in_standby_is_switched_on_over_ocf(
    hass: HomeAssistant, packets, caplog
) -> None:
    """The magic packet does nothing to a TV awake in network standby; the
    switch write on the session that read it does, and that session is kept."""
    coordinator, session = await _in_standby(hass)

    with (
        patch(
            "custom_components.localthings.coordinator.create_transport", return_value=session
        ) as connect,
        caplog.at_level(logging.INFO),
    ):
        await _turn_on(hass, coordinator)

    assert len(packets) == 1
    assert session.writes == [_POWER]
    connect.assert_not_called()
    assert coordinator._failed_cycles == 0
    assert _state(hass, "media_player").state != "off"
    assert "TV answered after Wake-on-LAN" in caplog.text


@pytest.mark.parametrize(
    "first", [(0x80, None), (None, TimeoutError("timed out"))], ids=["refused", "lost"]
)
async def test_a_switch_on_that_fails_is_sent_again_on_the_next_look(
    hass: HomeAssistant, packets, first
) -> None:
    """A refused or unanswered write leaves the TV in standby: the next look
    finds it there and writes again."""
    coordinator, session = await _in_standby(hass)
    code, error = first
    real_write = session.write
    calls: list[float | None] = []

    def _write(path, body, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            if error is not None:
                raise error
            return code, None
        return real_write(path, body, timeout)

    with patch.object(session, "write", _write):
        await _turn_on(hass, coordinator)

    assert len(calls) == 2
    assert calls[0] == coordinator.av._STANDBY_WRITE_TIMEOUT_S
    assert coordinator._failed_cycles == 0
    assert len(packets) == 1


async def test_a_tv_back_in_standby_before_the_write_gets_the_packet_again(
    hass: HomeAssistant, packets
) -> None:
    """A packet sent while the TV was awake in standby was ignored: once the
    TV has gone dark it is sent again, and once only."""
    coordinator, session = await _in_standby(hass)
    real_read = session.read
    dark = False

    real_refresh = coordinator.async_refresh
    dark_looks = 0

    async def _look() -> None:
        nonlocal dark, dark_looks
        if dark and len(packets) >= 2:
            # Still dark for two more looks after the packet, then up.
            dark_looks += 1
            if dark_looks > 2:
                dark = False
                session.resources[_SWITCH] = {"value": True}
        await real_refresh()

    def _read(path, timeout=None):
        if dark:
            raise TimeoutError("timed out")
        return real_read(path, timeout)

    def _write(path, body, timeout=None):
        nonlocal dark
        dark = True
        raise TimeoutError("timed out")

    with (
        patch.object(session, "read", _read),
        patch.object(session, "write", _write),
        patch.object(coordinator, "async_refresh", _look),
        patch("custom_components.localthings.coordinator.create_transport", return_value=session),
    ):
        await _turn_on(hass, coordinator)

    assert len(packets) == 2
    assert packets[0] == packets[1]
    assert coordinator._failed_cycles == 0


@pytest.mark.parametrize(("standby", "closed"), [(True, False), (False, True)])
async def test_a_wake_keeps_only_a_session_that_just_read_standby(
    hass: HomeAssistant, packets, standby: bool, closed: bool
) -> None:
    """The follow-up drops a stale session but not one the TV answers in
    standby, which the switch write needs."""
    coordinator, session = await _in_standby(hass)
    coordinator._standby_cycle = standby
    seen: list[object] = []

    async def _look() -> None:
        seen.append(coordinator._session)
        coordinator._session = session
        coordinator._failed_cycles = 0

    with patch.object(coordinator, "async_refresh", _look):
        await _turn_on(hass, coordinator)

    assert seen == [None if closed else session]


async def test_a_registry_mac_added_later_is_seen(hass: HomeAssistant) -> None:
    """The registry lookup is cached until the device registry changes."""
    entry = _tv_entry(hass)
    await _setup_tv(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert not coordinator.av.wakeable()

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.device_key)},
        connections={(dr.CONNECTION_NETWORK_MAC, TV_MAC)},
    )
    await hass.async_block_till_done()

    assert coordinator.av.wakeable()
