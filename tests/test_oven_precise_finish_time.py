"""The oven family's to-the-second finish time on /operational/state/vs/0.

The running-cook values are an MW7300B's (TP1X_DA-KS-MICROWAVE-01041),
where remainingTime carries seconds but /operational/state/vs/0 is only
pushed about every 10 s.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import oven
from custom_components.localthings.registry.capabilities.oven import CookFinishTracker
from custom_components.localthings.registry.discovery import BoundEntity, discover
from custom_components.localthings.sensor import (
    LocalThingsSensor,
    LocalThingsTrackedSensor,
    _sensor_for,
)
from tests.conftest import _load_device

T0 = datetime(2026, 11, 7, 22, 11, 35, 100000, tzinfo=UTC)


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _rep(state="Run", remaining="00:03:00"):
    return {
        "x.com.samsung.da.state": state,
        "x.com.samsung.da.operationTime": "00:03:00",
        "x.com.samsung.da.remainingTime": remaining,
        "x.com.samsung.da.progressPercentage": "201",
    }


def _desc():
    return next(d for d in oven.OVEN_OPERATIONAL_STATE.entities if d.key == "precise_finish_time")


# --- the tracker --------------------------------------------------------------


def test_running_cook_finishes_to_the_second():
    tracker = CookFinishTracker()
    assert tracker.update(_rep(), _at(0)) == datetime(2026, 11, 7, 22, 14, 35, tzinfo=UTC)


def test_unchanged_remaining_time_keeps_its_anchor():
    """The 3-minute cook's failure: another resource updated 6 s after the
    last /operational/state read, and now() + that old remainingTime moved
    the finish 6 s late. An unchanged value now keeps its first result."""
    tracker = CookFinishTracker()
    first = tracker.update(_rep(remaining="00:02:47"), _at(12))
    assert tracker.update(_rep(remaining="00:02:47"), _at(18)) == first


def test_one_second_wobble_between_reads_is_held():
    tracker = CookFinishTracker()
    first = tracker.update(_rep(remaining="00:02:50"), _at(10))
    # Read 10.4 s later, the countdown has moved 11 s: finish 1 s earlier.
    assert tracker.update(_rep(remaining="00:02:39"), _at(20.4)) == first


def test_time_changed_on_the_panel_is_reported():
    tracker = CookFinishTracker()
    first = tracker.update(_rep(remaining="00:02:00"), _at(60))
    assert first is not None
    assert tracker.update(_rep(remaining="00:03:00"), _at(61)) == first + timedelta(seconds=61)


def test_old_sweep_value_moving_the_finish_later_is_held():
    """The 32 s /device/0 sweep reads a remainingTime 2-3 s older than the
    push does, which only ever puts the finish later."""
    tracker = CookFinishTracker()
    first = tracker.update(_rep(remaining="00:02:50"), _at(10))
    assert first is not None
    # Read 3 s and 5 s later, the sweep still has the older countdown.
    assert tracker.update(_rep(remaining="00:02:50"), _at(13)) == first
    assert tracker.update(_rep(remaining="00:02:49"), _at(15)) == first


def test_earlier_finish_of_two_seconds_is_taken():
    tracker = CookFinishTracker()
    first = tracker.update(_rep(remaining="00:02:50"), _at(10))
    assert first is not None
    assert tracker.update(_rep(remaining="00:02:48"), _at(10)) == first - timedelta(seconds=2)


def test_ten_seconds_added_on_the_panel_is_taken():
    tracker = CookFinishTracker()
    first = tracker.update(_rep(remaining="00:02:00"), _at(60))
    assert first is not None
    assert tracker.update(_rep(remaining="00:02:10"), _at(60)) == first + timedelta(seconds=10)


def test_short_pause_still_moves_the_finish():
    tracker = CookFinishTracker()
    running = tracker.update(_rep(remaining="00:02:00"), _at(60))
    assert running is not None
    tracker.update(_rep(state="Pause", remaining="00:01:55"), _at(65))
    resumed = tracker.update(_rep(remaining="00:01:55"), _at(68))
    assert resumed == running + timedelta(seconds=3)


def test_replay_of_the_mw7300b_three_minute_cook_holds_its_first_value():
    """23:36:36-23:39:36 on 2026-11-07, every /operational/state/vs/0 value
    the debug log and history show reaching the sensor. It changed 8 times
    on the symmetric deadband."""
    t0 = datetime(2026, 11, 7, 22, 36, 36, 200000, tzinfo=UTC)
    reads = [
        (0.0, "00:03:00"),  # start, push
        (9.8, "00:02:53"),  # sweep, 2 s old
        (10.1, "00:02:50"),  # push
        (41.5, "00:02:21"),  # sweep
        (50.4, "00:02:10"),  # push
        (105.5, "00:01:18"),  # sweep, 3 s old
        (109.1, "00:01:12"),  # direct read
        (110.9, "00:01:09"),  # push
    ]
    tracker = CookFinishTracker()
    states = []
    for offset, remaining in reads:
        states.append(tracker.update(_rep(remaining=remaining), t0 + timedelta(seconds=offset)))
    states.append(
        tracker.update(_rep(state="Ready", remaining="00:00:00"), t0 + timedelta(seconds=180))
    )
    # The finish computed at the start is the moment the end was reported,
    # so nothing in between, nor the end itself, changes the state.
    assert set(states) == {datetime(2026, 11, 7, 22, 39, 36, tzinfo=UTC)}


def test_pause_freezes_and_resume_reanchors():
    tracker = CookFinishTracker()
    running = tracker.update(_rep(remaining="00:02:00"), _at(60))
    assert running is not None
    paused = _rep(state="Pause", remaining="00:01:50")
    assert tracker.update(paused, _at(70)) == running
    assert tracker.update(paused, _at(100)) == running
    # Resumed 30 s later with the countdown where it stopped.
    resumed = tracker.update(_rep(remaining="00:01:50"), _at(101))
    assert resumed == running + timedelta(seconds=31)


@pytest.mark.parametrize("ended", ["Ready", "End", "Stop"])
def test_end_is_the_moment_it_was_reported_and_is_kept(ended):
    tracker = CookFinishTracker()
    tracker.update(_rep(remaining="00:00:05"), _at(175))
    end = tracker.update(_rep(state=ended, remaining="00:00:00"), _at(180.06))
    assert end == datetime(2026, 11, 7, 22, 14, 35, tzinfo=UTC)
    assert tracker.update(_rep(state="Ready", remaining="00:00:00"), _at(600)) == end


def test_cancel_from_pause_is_an_end():
    tracker = CookFinishTracker()
    tracker.update(_rep(remaining="00:02:00"), _at(60))
    tracker.update(_rep(state="Pause", remaining="00:01:50"), _at(70))
    assert tracker.update(_rep(state="Ready"), _at(90)) == _at(90).replace(microsecond=0)


def test_idle_start_keeps_the_restored_value():
    restored = datetime(2026, 11, 7, 21, 0, 0, tzinfo=UTC)
    tracker = CookFinishTracker(restored)
    assert tracker.update(_rep(state="Ready", remaining="00:00:00"), _at(0)) == restored


def test_idle_start_without_history_is_unknown():
    assert CookFinishTracker().update(_rep(state="Ready"), _at(0)) is None


def test_start_mid_cook_replaces_the_restored_value():
    tracker = CookFinishTracker(datetime(2026, 11, 7, 21, 0, 0, tzinfo=UTC))
    assert tracker.update(_rep(), _at(0)) == datetime(2026, 11, 7, 22, 14, 35, tzinfo=UTC)


def test_unparseable_or_zero_countdown_keeps_the_value():
    tracker = CookFinishTracker()
    finish = tracker.update(_rep(remaining="00:00:05"), _at(175))
    assert tracker.update(_rep(remaining="00:00:00"), _at(180)) == finish
    assert tracker.update(_rep(remaining=""), _at(180.5)) == finish


# --- descriptor and snapshot --------------------------------------------------


def test_descriptor_is_a_disabled_tracked_timestamp_named_like_the_dawit_one():
    desc = _desc()
    assert desc.enabled_default is False
    assert desc.device_class == "timestamp"
    assert desc.translation_key == "cook_finish_time"
    assert desc.tracker_factory is CookFinishTracker
    assert desc.hysteresis is False


def test_absent_without_a_remaining_time_field():
    assert not _desc().exists_fn({"x.com.samsung.da.state": "Ready"}, {})


def test_mw7300b_snapshot_carries_the_key():
    resources = _load_device("microwave_mw7300b")
    resources["/operational/state/vs/0"] = {**resources["/operational/state/vs/0"], **_rep()}
    reg = resolve(resources)
    assert reg is not None
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert isinstance(state["precise_finish_time"], datetime)


# --- the entity ---------------------------------------------------------------


class _FakeCoordinator:
    def __init__(self, rep):
        self.device_key = "TEST-SERIAL"
        self.config_entry = None
        self.data: dict = {}
        self.rep = rep

    def canonical_resources(self, subdevice):
        return {oven.OVEN_OPERATIONAL_STATE.href: self.rep}


def _bound():
    href = oven.OVEN_OPERATIONAL_STATE.href
    assert href is not None
    return BoundEntity(href=href, capability=oven.OVEN_OPERATIONAL_STATE, desc=_desc())


def _entity(rep):
    coordinator = _FakeCoordinator(rep)
    entity = _sensor_for(cast(LocalThingsCoordinator, coordinator), _bound())
    assert isinstance(entity, LocalThingsTrackedSensor)
    entity.async_write_ha_state = lambda: None  # ty: ignore[invalid-assignment]
    return entity, coordinator


def test_untracked_descriptors_stay_plain_sensors():
    finish = next(d for d in oven.OVEN_OPERATIONAL_STATE.entities if d.key == "finish_time")
    bound = BoundEntity(
        href="/operational/state/vs/0", capability=oven.OVEN_OPERATIONAL_STATE, desc=finish
    )
    entity = _sensor_for(cast(LocalThingsCoordinator, _FakeCoordinator({})), bound)
    assert type(entity) is LocalThingsSensor


def test_entity_follows_the_cook_and_keeps_the_end():
    entity, coordinator = _entity(_rep(remaining="00:00:30"))
    entity._handle_coordinator_update()
    running = entity.native_value
    assert isinstance(running, datetime)
    coordinator.rep = _rep(state="Ready", remaining="00:00:00")
    entity._handle_coordinator_update()
    ended = entity.native_value
    assert isinstance(ended, datetime) and ended != running
    entity._handle_coordinator_update()
    assert entity.native_value == ended


def test_entity_restores_the_last_end_while_idle(monkeypatch):
    restored = datetime(2026, 11, 7, 21, 0, 0, tzinfo=UTC)

    class _Last:
        native_value = restored

    async def _last_data(self):
        return _Last()

    async def _noop(self):
        return None

    monkeypatch.setattr(LocalThingsTrackedSensor, "async_get_last_sensor_data", _last_data)
    monkeypatch.setattr(LocalThingsSensor, "async_added_to_hass", _noop)
    entity, _ = _entity(_rep(state="Ready", remaining="00:00:00"))
    loop = asyncio.new_event_loop()  # asyncio.run() would unset the shared loop
    try:
        loop.run_until_complete(entity.async_added_to_hass())
    finally:
        loop.close()
    assert entity.native_value == restored
