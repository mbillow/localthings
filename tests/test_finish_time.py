"""finish_time, timed from when each remainingTime arrives (issue #772).

The running-cook values are an MW7300B's (TP1X_DA-KS-MICROWAVE-01041),
captured by the #772 author: remainingTime carries seconds but
/operational/state/vs/0 is only pushed about every 10 s. The minute-only
range follows the NX60T8311SS notes in #457, and the washer a two-hour cycle
polled every 30 s.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from custom_components.localthings import finish
from custom_components.localthings import sensor as sensor_module
from custom_components.localthings.const import CONF_FINISH_TIME_HYSTERESIS_MINUTES
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.capabilities import oven
from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.sensor import LocalThingsSensor

HREF = "/operational/state/vs/0"
T0 = datetime(2026, 11, 7, 22, 36, 36, 200000, tzinfo=UTC)


def _rep(remaining, state="Run"):
    return {"x.com.samsung.da.state": state, "x.com.samsung.da.remainingTime": remaining}


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


# --- the anchor store ---------------------------------------------------------


def _finish(anchors):
    field = anchors.field(HREF)
    assert field is not None
    return field["finish"]


def test_finish_is_arrival_plus_remaining():
    anchors = finish.FinishAnchors()
    assert anchors.observe(HREF, _rep("00:02:53"), "observe", _at(0))
    assert anchors.field(HREF) == {"finish": _at(173), "seconds": True}


def test_an_unchanged_reading_keeps_its_anchor():
    """#772's failure: another resource updated 6 s after the last read, and
    now() + that old remainingTime moved the finish 6 s late."""
    anchors = finish.FinishAnchors()
    anchors.observe(HREF, _rep("00:02:47"), "observe", _at(12))
    assert not anchors.observe(HREF, _rep("00:02:47"), "poll", _at(18))
    assert _finish(anchors) == _at(179)


def test_a_sweep_moving_the_finish_later_is_ignored():
    """The /device/0 sweep's value runs 2-3 s behind a push."""
    anchors = finish.FinishAnchors()
    anchors.observe(HREF, _rep("00:02:50"), "observe", _at(10))
    assert not anchors.observe(HREF, _rep("00:02:21"), "sweep", _at(41.5))
    assert _finish(anchors) == _at(180)


def test_a_sweep_seeing_a_real_revision_is_taken():
    """More than one step plus the sweep's lag later is the appliance adding
    time, not a stale reading -- seen first by a sweep when nothing pushes it."""
    anchors = finish.FinishAnchors()
    anchors.observe(HREF, _rep("00:20:59"), "observe", _at(0))
    assert anchors.observe(HREF, _rep("00:24:59"), "sweep", _at(30))
    assert _finish(anchors) == _at(30 + 24 * 60 + 59)


def test_a_rejected_sweep_does_not_block_the_push_carrying_it():
    anchors = finish.FinishAnchors()
    anchors.observe(HREF, _rep("00:02:50"), "observe", _at(10))
    anchors.observe(HREF, _rep("00:02:40"), "sweep", _at(22))  # 2 s behind
    assert anchors.observe(HREF, _rep("00:02:40"), "observe", _at(25))
    assert _finish(anchors) == _at(185)


def test_a_stalled_countdown_re_anchors():
    """An oven preheating holds remainingTime while it runs; the finish moves
    on with the clock, as now() + remaining always did."""
    anchors = finish.FinishAnchors()
    anchors.observe(HREF, _rep("00:30:00"), "observe", _at(0))
    assert not anchors.observe(HREF, _rep("00:30:00"), "observe", _at(60))
    assert anchors.observe(HREF, _rep("00:30:00"), "observe", _at(120))
    assert _finish(anchors) == _at(120 + 1800)


def test_ocf_state_names_are_understood():
    anchors = finish.FinishAnchors()
    anchors.observe(HREF, _rep("00:02:00", "active"), "observe", _at(60))
    anchors.observe(HREF, _rep("00:01:50", "pause"), "observe", _at(70))
    assert anchors.observe(HREF, _rep("00:01:50", "active"), "observe", _at(100))
    assert _finish(anchors) == _at(210)
    assert anchors.observe(HREF, _rep("00:00:00", "idle"), "observe", _at(300))
    assert anchors.field(HREF) is None


def test_a_sweep_moving_the_finish_earlier_is_taken():
    anchors = finish.FinishAnchors()
    anchors.observe(HREF, _rep("00:02:50"), "observe", _at(10))
    assert anchors.observe(HREF, _rep("00:02:00"), "sweep", _at(41.5))
    assert _finish(anchors) == _at(161.5)


def test_a_push_moving_the_finish_later_is_taken():
    """Time added on the panel."""
    anchors = finish.FinishAnchors()
    anchors.observe(HREF, _rep("00:02:00"), "observe", _at(60))
    assert anchors.observe(HREF, _rep("00:02:30"), "observe", _at(61))
    assert _finish(anchors) == _at(211)


def test_a_value_seen_earlier_still_re_anchors():
    """Adding time on the panel can bring back an earlier reading, so only
    the previous one is compared."""
    anchors = finish.FinishAnchors()
    anchors.observe(HREF, _rep("00:02:00"), "observe", _at(0))
    anchors.observe(HREF, _rep("00:01:50"), "observe", _at(10))
    assert anchors.observe(HREF, _rep("00:02:00"), "observe", _at(11))
    assert _finish(anchors) == _at(131)


def test_pause_freezes_and_resume_re_anchors():
    anchors = finish.FinishAnchors()
    anchors.observe(HREF, _rep("00:02:00"), "observe", _at(60))
    assert not anchors.observe(HREF, _rep("00:01:50", "Pause"), "observe", _at(70))
    assert _finish(anchors) == _at(180)
    # Resumed 30 s later with the countdown where it stopped -- even off a
    # sweep, since the countdown stood still for the whole pause.
    assert anchors.observe(HREF, _rep("00:01:50"), "sweep", _at(101))
    assert _finish(anchors) == _at(211)


def test_a_pause_that_outlasts_the_finish_moves_it_on():
    """A frozen finish in the past would fire timestamp automations during
    the pause; once it passes, resuming now is the earliest finish."""
    anchors = finish.FinishAnchors()
    anchors.observe(HREF, _rep("00:01:00"), "observe", _at(0))
    anchors.observe(HREF, _rep("00:00:50", "Pause"), "observe", _at(10))
    assert _finish(anchors) == _at(60)
    assert anchors.observe(HREF, _rep("00:00:50", "Pause"), "observe", _at(90))
    assert _finish(anchors) == _at(140)


@pytest.mark.parametrize("ended", ["Ready", "End", "Stop"])
def test_an_ended_cycle_clears_the_anchor(ended):
    anchors = finish.FinishAnchors()
    anchors.observe(HREF, _rep("00:00:05"), "observe", _at(175))
    assert anchors.observe(HREF, _rep("00:00:00", ended), "observe", _at(180))
    assert anchors.field(HREF) is None


def test_a_rep_without_remaining_time_is_not_tracked():
    anchors = finish.FinishAnchors()
    assert not anchors.observe(HREF, {"x.com.samsung.da.state": "Run"}, "observe", _at(0))
    assert anchors.fields() == {}


# --- precision ----------------------------------------------------------------


@pytest.mark.parametrize("remaining", ["00:14:59", "01:20:00"])
def test_a_minute_only_reading_rounds_to_the_nearest_minute(remaining):
    rep = {**_rep(remaining), finish.FIELD: {"finish": _at(40), "seconds": False}}
    # 22:37:16.2 is nearer 22:37 than 22:38: rounding down ran ahead (#771),
    # rounding up would run behind.
    assert finish.finish_at(rep) == datetime(2026, 11, 7, 22, 37, tzinfo=UTC)


def test_a_board_reporting_seconds_shows_them():
    rep = {**_rep("00:02:53"), finish.FIELD: {"finish": _at(173), "seconds": True}}
    assert finish.finish_at(rep) == datetime(2026, 11, 7, 22, 39, 29, tzinfo=UTC)


def test_without_an_anchor_the_dump_path_still_computes_one():
    assert finish.finish_at(_rep("00:02:53"), now=_at(0)) == datetime(
        2026, 11, 7, 22, 39, 29, tzinfo=UTC
    )
    assert finish.finish_at(_rep("00:00:00"), now=_at(0)) is None


def test_the_finish_is_held_while_paused():
    frozen = {finish.FIELD: {"finish": _at(180), "seconds": True}}
    assert oven._finish_time({**_rep("00:01:50", "Pause"), **frozen}) == _at(180).replace(
        microsecond=0
    )
    # A pause with nothing merged (no reading seen yet) stays unknown.
    assert oven._finish_time(_rep("00:01:50", "Pause")) is None


# --- replays through the coordinator's store and the sensor's deadband --------


class _Entry:
    def __init__(self):
        self.options = {CONF_FINISH_TIME_HYSTERESIS_MINUTES: 3}


class _Coordinator:
    def __init__(self):
        self.device_key = "TEST-SERIAL"
        self.config_entry = _Entry()
        self.data: dict = {}
        self.rep: dict = {}

    def canonical_resources(self, subdevice):
        return {HREF: self.rep}

    def entity_rep(self, href):
        return self.rep


def _replay(monkeypatch, capability, readings, rep_fn, *, warm=None):
    """Feed each (offset, source, remaining[, state]) through FinishAnchors
    and the finish_time entity, returning its distinct states in order.

    `warm`, if given, is a reading from an earlier cycle on the same board,
    so its precision is already known -- what every cycle after the first
    since a restart sees."""
    desc = next(e for e in capability.entities if e.key == "finish_time")
    coordinator = _Coordinator()
    href = capability.href
    assert href is not None
    entity = LocalThingsSensor(
        cast(LocalThingsCoordinator, coordinator),
        BoundEntity(href=href, capability=capability, desc=desc),
    )
    anchors = finish.FinishAnchors()
    if warm is not None:
        anchors.observe(HREF, _rep(warm), "observe", _at(-3600))
        anchors.observe(HREF, _rep("00:00:00", "Ready"), "observe", _at(-3500))
    states: list = []
    for offset, source, remaining, *state in readings:
        now = _at(offset)
        rep = _rep(remaining, *state)
        anchors.observe(HREF, rep, source, now)
        field = anchors.field(HREF)
        coordinator.rep = {**rep, finish.FIELD: field} if field else rep
        coordinator.data = {"finish_time": rep_fn(coordinator.rep)}
        monkeypatch.setattr(sensor_module.dt_util, "utcnow", lambda now=now: now)
        value = entity.native_value
        if not states or states[-1] != value:
            states.append(value)
    return states


# Every /operational/state/vs/0 read the #772 author's debug log and history
# show reaching the sensor, 23:36:36-23:39:36 local on 2026-11-07.
MW7300B_COOK = [
    (0.0, "observe", "00:03:00"),  # start
    (9.8, "sweep", "00:02:53"),  # 2 s old
    (10.1, "observe", "00:02:50"),
    (41.5, "sweep", "00:02:21"),
    (50.4, "observe", "00:02:10"),
    (105.5, "sweep", "00:01:18"),  # 3 s old
    (109.1, "poll", "00:01:12"),  # direct read
    (110.9, "observe", "00:01:09"),
    (180.0, "observe", "00:00:00", "Ready"),
]


def test_replay_of_the_mw7300b_three_minute_cook(monkeypatch):
    """One value for the whole cook, the moment it really ended; then unknown,
    as finish_time has always gone at the end of a cycle."""
    states = _replay(
        monkeypatch, oven.OVEN_OPERATIONAL_STATE, MW7300B_COOK, oven._finish_time, warm="00:01:37"
    )
    assert states == [datetime(2026, 11, 7, 22, 39, 36, tzinfo=UTC), None]


def test_the_first_cook_after_a_restart_settles_once(monkeypatch):
    """Until a reading with seconds arrives the precision is unknown, so the
    first cook rounds to the minute and then corrects once."""
    states = _replay(monkeypatch, oven.OVEN_OPERATIONAL_STATE, MW7300B_COOK, oven._finish_time)
    assert states == [
        datetime(2026, 11, 7, 22, 40, tzinfo=UTC),
        datetime(2026, 11, 7, 22, 39, 36, tzinfo=UTC),
        None,
    ]


def test_mw7300b_pause_and_panel_add(monkeypatch):
    """The #772 author's trace against SmartThings: a 36 s pause, then +30 s
    on the panel. Each shows; nothing else does."""
    readings = [
        (0.0, "observe", "00:03:00"),
        (10.0, "observe", "00:02:50"),
        (20.0, "observe", "00:02:40", "Pause"),
        (56.0, "observe", "00:02:40"),  # resumed after 36 s
        (66.0, "observe", "00:02:30"),
        (70.0, "observe", "00:02:56"),  # +30 s on the panel
        (80.0, "observe", "00:02:46"),
        (250.0, "observe", "00:00:00", "Ready"),
    ]
    states = _replay(
        monkeypatch, oven.OVEN_OPERATIONAL_STATE, readings, oven._finish_time, warm="00:01:37"
    )
    shown = [_at(180), _at(216), _at(246)]
    assert states == [*(t.replace(microsecond=0) for t in shown), None]


def _minute_only(t0_offset, remaining_s, every):
    """An NX60T-style countdown: whole minutes with the seconds at :59,
    pushed every `every` s (#457)."""
    readings = []
    t = 0.0
    while remaining_s - t > 0:
        left = remaining_s - t
        minutes = int((left - 1) // 60)
        readings.append((t0_offset + t, "observe", f"00:{minutes:02d}:59"))
        t += every
    readings.append((t0_offset + remaining_s, "observe", "00:00:00", "Ready"))
    return readings


def test_minute_only_board_holds_one_value_at_the_nearest_minute(monkeypatch):
    """A 15-minute bake pushed every 5 s. Rounded down, the old finish was a
    minute ahead of the panel half the time and flipped at every boundary
    the push delay straddled; timed from each tick it is steady."""
    states = _replay(
        monkeypatch,
        oven.OVEN_OPERATIONAL_STATE,
        _minute_only(0, 900, 5),
        oven._finish_time,
    )
    # Ends 22:51:36.2, nearest minute 22:52.
    assert states == [datetime(2026, 11, 7, 22, 52, tzinfo=UTC), None]


def _washer_cycle():
    """A two-hour wash polled every 30 s, whole minutes, revised +2 min at
    40 min in and -1 min at 90 min in."""
    readings = []
    end = 7200
    for t in range(0, 7200, 30):
        if t == 2400:
            end += 120
        if t == 5400:
            end -= 60
        left = end - t
        if left <= 0:
            break
        h, m = divmod((left + 59) // 60, 60)
        readings.append((float(t), "poll", f"{h:02d}:{m:02d}:00"))
    readings.append((float(end), "poll", "00:00:00", "Ready"))
    return readings


def _old_states(readings):
    """What finish_time did before #772: now() + remaining rounded down, and
    a flat 3-minute deadband."""
    states: list = []
    held = None
    for offset, _source, remaining, *state in readings:
        total = finish.remaining_seconds(remaining)
        raw = (
            (_at(offset) + timedelta(seconds=total)).replace(second=0, microsecond=0)
            if not state and total
            else None
        )
        if raw is None or held is None or abs(raw - held) >= timedelta(minutes=3):
            held = raw
        if not states or states[-1] != held:
            states.append(held)
    return states


def test_a_washer_records_about_as_many_changes_as_before(monkeypatch):
    """The -1 min revision is held while it is under 5% of what remains and
    shows inside the last 20 minutes: one change more than before, ending on
    the real finish where the old value ended a minute late."""
    readings = _washer_cycle()
    rep_fn = next(e for e in OPERATIONAL_STATE.entities if e.key == "finish_time").rep_fn
    states = _replay(monkeypatch, OPERATIONAL_STATE, readings, rep_fn)
    old = _old_states(readings)
    assert len(states) <= len(old) + 1
    # Ends 00:37:36.2 the next day.
    assert states[-2:] == [datetime(2026, 11, 8, 0, 38, tzinfo=UTC), None]
    assert old[-2] == datetime(2026, 11, 8, 0, 39, tzinfo=UTC)


def test_near_the_end_a_seconds_board_keeps_a_two_second_floor(monkeypatch):
    """Under 40 s left, 5% is under 2 s; a 1 s arrival jitter stays held."""
    readings = [
        (0.0, "observe", "00:00:30"),
        (10.0, "observe", "00:00:21"),  # finish 1 s later than the first
        (20.0, "observe", "00:00:09"),  # 1 s earlier
    ]
    states = _replay(
        monkeypatch, oven.OVEN_OPERATIONAL_STATE, readings, oven._finish_time, warm="00:01:37"
    )
    assert states == [_at(30).replace(microsecond=0)]


async def test_the_coordinator_merges_the_finish_and_pushes_it(hass):
    """Arrival-timed finish reaches the entity snapshot, and a change
    schedules its own push: the cache-change push can run first."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.localthings.const import DOMAIN
    from tests.test_subdevice_discovery import ENTRY_DATA

    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="localthings_FINISH")
    entry.add_to_hass(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    pushes = []
    real = coordinator._schedule_push

    def counted() -> None:
        pushes.append(1)
        real()

    setattr(coordinator, "_schedule_push", counted)  # noqa: B010

    coordinator._observe.apply(HREF, _rep("00:02:53"), source="observe")
    assert pushes
    field = coordinator.entity_resources()[HREF][finish.FIELD]
    assert field["seconds"] is True
    assert finish.FIELD in coordinator.entity_rep(HREF)
    # Never in what the device reported.
    assert finish.FIELD not in coordinator.resource(HREF)
