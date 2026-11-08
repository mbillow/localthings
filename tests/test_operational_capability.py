"""Unit tests for operational state capabilities."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.localthings.registry.capabilities.operational import (
    OPERATIONAL_STATE,
    _just_finished,
    _new_cycle_running,
)
from custom_components.localthings.registry.entities import NumberDesc, SensorDesc, TimeDesc


def test_machine_state_maps_samsung_to_ocf():
    ms = next(e for e in OPERATIONAL_STATE.entities if e.key == "machine_state")
    assert ms.value_fn("Run") == "active"
    assert ms.value_fn("Pause") == "pause"
    assert ms.value_fn("Ready") == "idle"


class TestJustFinished:
    """`_just_finished` is progress/progress_percentage's sticky_fn arm
    condition (issue #345) -- see sensor.py's _apply_sticky."""

    def test_true_when_progress_is_finish_while_active(self):
        assert _just_finished(
            {"x.com.samsung.da.state": "Run", "x.com.samsung.da.progress": "Finish"}
        )

    def test_true_even_once_state_has_already_left_active(self):
        """The exact issue #345 scenario this exists for: a washer's
        `state` can already read idle by the time `progress` is observed
        at 'Finish' -- unlike _is_active, this must still arm."""
        assert _just_finished(
            {"x.com.samsung.da.state": "Ready", "x.com.samsung.da.progress": "Finish"}
        )

    def test_false_while_active_but_not_yet_finished(self):
        assert not _just_finished(
            {"x.com.samsung.da.state": "Run", "x.com.samsung.da.progress": "Spin"}
        )

    def test_false_when_progress_absent(self):
        assert not _just_finished({"x.com.samsung.da.state": "Run"})


class TestNewCycleRunning:
    """`_new_cycle_running` is progress/progress_percentage's
    sticky_bypass_fn -- the early-release condition for the #345 hold.
    See sensor.py's _apply_sticky."""

    def test_true_for_a_concrete_non_finish_code_while_active(self):
        assert _new_cycle_running(
            {"x.com.samsung.da.state": "Run", "x.com.samsung.da.progress": "Wash"}
        )

    def test_false_for_a_running_stage_reported_after_state_left_active(self):
        """Issue #358, the whole reason for the `state` gate: the reporting
        dryer replays a running stage ('Drying', its first supportedProgress
        entry) for a few seconds after Finish while winding down. That is
        the finished cycle's tail, not a new cycle -- releasing the hold on
        it is what produced 'Drying, Cooling, Finish, Drying, Idle'."""
        assert not _new_cycle_running(
            {"x.com.samsung.da.state": "Ready", "x.com.samsung.da.progress": "Drying"}
        )

    def test_false_while_paused(self):
        """Paused is not evidence a new cycle is running, and the tail
        above can't be told apart from it. rep_fn shows 'Idle' whenever
        state isn't active anyway, so there is no live value being
        withheld here -- only a hold that expires on its own instead of
        being released early."""
        assert not _new_cycle_running(
            {"x.com.samsung.da.state": "Pause", "x.com.samsung.da.progress": "Wash"}
        )

    def test_false_for_finish(self):
        assert not _new_cycle_running(
            {"x.com.samsung.da.state": "Run", "x.com.samsung.da.progress": "Finish"}
        )

    def test_false_when_absent_or_none(self):
        assert not _new_cycle_running({})
        assert not _new_cycle_running(
            {"x.com.samsung.da.state": "Run", "x.com.samsung.da.progress": "None"}
        )


def test_progress_is_a_translatable_enum():
    desc = next(
        e for e in OPERATIONAL_STATE.entities if e.key == "progress" and isinstance(e, SensorDesc)
    )
    assert desc.device_class == "enum"
    assert desc.options is not None
    assert "rinse" in desc.options
    assert "Rinse" not in desc.options
    assert desc.rep_fn is not None
    assert (
        desc.rep_fn({"x.com.samsung.da.state": "Run", "x.com.samsung.da.progress": "Rinse"})
        == "rinse"
    )


@pytest.mark.parametrize(
    ("raw", "state"), [("Laundrysensing", "laundrysensing"), ("Aidrying", "aidrying")]
)
def test_laundry_sensing_and_ai_drying_are_translated_states(raw, state):
    """Both reported by a washer (#765), AI drying during its AI Drying
    program. The raw casing is assumed; the state lowercases it either way."""
    desc = next(
        e for e in OPERATIONAL_STATE.entities if e.key == "progress" and isinstance(e, SensorDesc)
    )
    assert desc.options is not None and desc.rep_fn is not None
    assert desc.rep_fn({"x.com.samsung.da.state": "Run", "x.com.samsung.da.progress": raw}) == state
    assert state in desc.options


class TestProgressPercentage:
    """issue #9: device firmware leaves progressPercentage stale (e.g. '1')
    after a cycle ends instead of resetting it, so it must be gated on
    active state the same way `progress`/`cycle_active`/`finish_time` are."""

    def test_zeroed_when_not_active(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "progress_percentage")
        assert desc.rep_fn is not None
        rep = {"x.com.samsung.da.state": "Ready", "x.com.samsung.da.progressPercentage": "1"}
        assert desc.rep_fn(rep) == 0

    def test_passes_through_when_active(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "progress_percentage")
        assert desc.rep_fn is not None
        rep = {"x.com.samsung.da.state": "Run", "x.com.samsung.da.progressPercentage": "42"}
        assert desc.rep_fn(rep) == 42


class TestCompletionMinutes:
    """Unit tests for completion_minutes entity."""

    def test_completion_minutes_parsing(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "completion_minutes")
        assert desc.rep_fn is not None

        # 1 hour 25 mins 30 secs -> 85 mins + 1 sec ceiling = 86 mins
        rep = {"x.com.samsung.da.remainingTime": "01:25:30"}
        assert desc.rep_fn(rep) == 86

        # Exact minutes: 1 hour 30 mins 00 secs -> 90 mins
        rep_exact = {"x.com.samsung.da.remainingTime": "01:30:00"}
        assert desc.rep_fn(rep_exact) == 90

    def test_completion_minutes_fallback_key(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "completion_minutes")
        assert desc.rep_fn is not None
        rep = {"remainingTime": "00:45:00"}
        assert desc.rep_fn(rep) == 45

    def test_completion_minutes_stale_finish_gated(self):
        """Firmware freezes remainingTime at '00:01:00' when progress reaches 'Finish'.
        Should return 0 to prevent stuck values."""
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "completion_minutes")
        assert desc.rep_fn is not None
        rep = {
            "x.com.samsung.da.progress": "Finish",
            "x.com.samsung.da.remainingTime": "00:01:00",
        }
        assert desc.rep_fn(rep) == 0

    def test_completion_minutes_missing_or_invalid(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "completion_minutes")
        assert desc.rep_fn is not None

        rep = {}
        assert desc.rep_fn(rep) is None


class TestFinishTime:
    """issue: remainingTime only has minute resolution, but datetime.now()
    always carries fresh seconds/microseconds -- an unrounded finish_time
    changed on nearly every poll even when remainingTime hadn't, flooding
    the recorder history/logbook with values that looked identical once
    the UI rounded them down to the minute for display."""

    def test_seconds_and_microseconds_are_zeroed(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "finish_time")
        assert desc.rep_fn is not None
        rep = {
            "x.com.samsung.da.state": "Run",
            "x.com.samsung.da.remainingTime": "00:29:00",
        }
        result = desc.rep_fn(rep)
        assert result.second == 0
        assert result.microsecond == 0

    def test_stable_across_polls_within_same_minute(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "finish_time")
        assert desc.rep_fn is not None
        rep = {
            "x.com.samsung.da.state": "Run",
            "x.com.samsung.da.remainingTime": "00:29:00",
        }
        first = desc.rep_fn(rep)
        second = desc.rep_fn(rep)
        assert first == second


class TestDelayFieldFallback:
    def test_reads_delay_end_time_when_delay_start_time_absent(self):
        from custom_components.localthings.registry.capabilities.operational import (
            OPERATIONAL_STATE,
        )

        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "delay_start_hours")
        assert desc.rep_fn is not None
        rep = {"x.com.samsung.da.delayEndTime": "02:30:00"}
        assert desc.rep_fn(rep) == 2.5

    def test_prefers_delay_start_time_when_both_present(self):
        from custom_components.localthings.registry.capabilities.operational import (
            OPERATIONAL_STATE,
        )

        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "delay_start_hours")
        assert desc.rep_fn is not None
        rep = {
            "x.com.samsung.da.delayStartTime": "01:00:00",
            "x.com.samsung.da.delayEndTime": "02:00:00",
        }
        assert desc.rep_fn(rep) == 1.0

    def test_write_targets_delay_end_time_when_that_is_what_device_reports(self):
        from custom_components.localthings.registry.capabilities.operational import (
            OPERATIONAL_STATE,
        )

        desc = next(
            e
            for e in OPERATIONAL_STATE.entities
            if e.key == "delay_start_hours" and isinstance(e, NumberDesc)
        )
        assert desc.write_fn is not None
        rep = {"x.com.samsung.da.delayEndTime": "00:00:00"}
        result = desc.write_fn(1.5, rep)
        assert result is not None
        path, body = result
        assert path == ["operational", "state", "vs", "0"]
        assert body == {"x.com.samsung.da.delayEndTime": "01:30:00"}

    @pytest.mark.parametrize(
        "rep,expected",
        [
            ({"x.com.samsung.da.delayStartTime": "01:00:00"}, "x.com.samsung.da.delayStartTime"),
            ({"x.com.samsung.da.delayEndTime": "02:00:00"}, "x.com.samsung.da.delayEndTime"),
            (
                {
                    "x.com.samsung.da.delayStartTime": "01:00:00",
                    "x.com.samsung.da.delayEndTime": "02:00:00",
                },
                "x.com.samsung.da.delayStartTime",
            ),
        ],
    )
    def test_read_and_write_agree_on_one_field(self, rep, expected):
        """The number showed delayStartTime while writing delayEndTime, so a
        device reporting both would display a value the write never touched --
        the set would look like it did nothing. No dump carries both (#427), so
        this was unreachable rather than broken, but the two keys mean
        different things and must not be mixed within one entity."""
        from custom_components.localthings.registry.capabilities.operational import (
            OPERATIONAL_STATE,
        )

        desc = next(
            e
            for e in OPERATIONAL_STATE.entities
            if e.key == "delay_start_hours" and isinstance(e, NumberDesc)
        )
        assert desc.rep_fn is not None and desc.write_fn is not None
        result = desc.write_fn(1.5, rep)
        assert result is not None
        _path, body = result
        assert list(body) == [expected]
        # And the value the entity displays comes from that same key.
        assert desc.rep_fn(rep) == _delay_hours_of(rep[expected])

    def test_write_targets_delay_start_time_when_that_is_what_device_reports(self):
        from custom_components.localthings.registry.capabilities.operational import (
            OPERATIONAL_STATE,
        )

        desc = next(
            e
            for e in OPERATIONAL_STATE.entities
            if e.key == "delay_start_hours" and isinstance(e, NumberDesc)
        )
        assert desc.write_fn is not None
        rep = {"x.com.samsung.da.delayStartTime": "00:00:00"}
        result = desc.write_fn(1.5, rep)
        assert result is not None
        _path, body = result
        assert body == {"x.com.samsung.da.delayStartTime": "01:30:00"}


def _delay_hours_of(raw):
    """HH:MM:SS -> fractional hours, mirroring _delay_hours for assertions."""
    h, m, s = (int(x) for x in raw.split(":"))
    return (h * 3600 + m * 60 + s) / 3600.0


class TestDelayFinishAt:
    """The finish-time picker added for #427, where a WD80T634DBE/S7 held a
    written `03:17:00` exactly and, given 5 h, reported `remainingTime` of
    05:00:00 during `Delaywash` -- minute-granular, and counting to the end."""

    def _desc(self):
        return next(
            e
            for e in OPERATIONAL_STATE.entities
            if e.key == "delay_finish_at" and isinstance(e, TimeDesc)
        )

    def test_only_on_devices_that_delay_the_end(self):
        """A dishwasher delays the *start*, so a finish picker there would be
        asking for something delayStartTime cannot express."""
        desc = self._desc()
        assert desc.exists_fn is not None
        assert desc.exists_fn({"x.com.samsung.da.delayEndTime": "00:00:00"}, {})
        assert not desc.exists_fn({"x.com.samsung.da.delayStartTime": "01:00:00"}, {})

    @pytest.mark.parametrize(
        "target,now,expected",
        [
            (time(13, 30), datetime(2026, 9, 13, 9, 30), "04:00:00"),
            (time(11, 25), datetime(2026, 9, 13, 9, 30), "01:55:00"),
            # Minute granularity all the way to the wire -- issue #427 test 1.
            (time(12, 47), datetime(2026, 9, 13, 9, 30), "03:17:00"),
            # Earlier than now can only mean tomorrow's.
            (time(6, 0), datetime(2026, 9, 13, 9, 30), "20:30:00"),
            (time(9, 30), datetime(2026, 9, 13, 9, 30), "24:00:00"),
            # Seconds on the clock don't shift the delay off a whole minute.
            (time(13, 30), datetime(2026, 9, 13, 9, 30, 47), "04:00:00"),
        ],
    )
    def test_picked_time_becomes_the_delay_that_reaches_it(self, target, now, expected):
        desc = self._desc()
        assert desc.payload_fn is not None and desc.write_fn is not None
        result = desc.write_fn(desc.payload_fn(target, now), {"x.com.samsung.da.delayEndTime": ""})
        assert result is not None
        path, body = result
        assert path == ["operational", "state", "vs", "0"]
        assert body == {"x.com.samsung.da.delayEndTime": expected}

    def test_writes_the_end_key_even_where_delay_field_would_prefer_start(self):
        """_delay_field prefers delayStartTime for the hypothetical device
        reporting both; this entity is about the finish either way."""
        desc = self._desc()
        rep = {
            "x.com.samsung.da.delayStartTime": "01:00:00",
            "x.com.samsung.da.delayEndTime": "02:00:00",
        }
        result = desc.write_fn(1.0, rep)
        assert result is not None
        _path, body = result
        assert list(body) == ["x.com.samsung.da.delayEndTime"]

    @pytest.mark.parametrize(
        "zone,now,expected,note",
        [
            # Europe/Stockholm springs forward 2027-03-28 02:00 -> 03:00, so
            # 23:00 -> 07:00 is eight hours on the clock but seven in real
            # time, which is what the appliance counts down.
            ("Europe/Stockholm", datetime(2027, 3, 27, 23, 0), "07:00:00", "spring forward"),
            # ...and falls back 2027-10-31 03:00 -> 02:00: nine real hours.
            ("Europe/Stockholm", datetime(2027, 10, 30, 23, 0), "09:00:00", "fall back"),
            # An ordinary night is unaffected.
            ("Europe/Stockholm", datetime(2027, 6, 15, 23, 0), "08:00:00", "no transition"),
        ],
    )
    def test_delay_spans_a_dst_change_in_real_time(self, zone, now, expected, note):
        """datetime subtraction skips the utcoffset adjustment when both sides
        share a tzinfo, so wall-clock arithmetic here would send the appliance
        a duration an hour off what the picker promised."""
        desc = self._desc()
        aware = now.replace(tzinfo=ZoneInfo(zone))
        result = desc.write_fn(
            desc.payload_fn(time(7, 0), aware), {"x.com.samsung.da.delayEndTime": ""}
        )
        assert result is not None
        _path, body = result
        assert body == {"x.com.samsung.da.delayEndTime": expected}, note

    def test_reads_back_as_the_instant_the_delay_points_at(self):
        desc = self._desc()
        assert desc.rep_fn is not None
        before = datetime.now(UTC)
        value = desc.rep_fn({"x.com.samsung.da.delayEndTime": "04:00:00"})
        assert value is not None
        assert value.second == 0 and value.microsecond == 0
        assert timedelta(hours=4) - timedelta(minutes=1) <= value - before <= timedelta(hours=4)

    @pytest.mark.parametrize("raw", ["00:00:00", "", None])
    def test_no_value_without_a_pending_delay(self, raw):
        """Falls to 00:00:00 once the cycle itself starts (#427), where
        finish_time takes over."""
        assert self._desc().rep_fn({"x.com.samsung.da.delayEndTime": raw}) is None


def test_device_reporting_no_delay_key_at_all_writes_the_end_key():
    """The A51_20 WW6500 washer reports neither key, so it is the one device
    the fallback actually decides. It takes delayEndTime, the key every
    laundry dump that reports one at all reports (#427); dishwashers all
    report delayStartTime and never reach this branch."""
    desc = next(
        e
        for e in OPERATIONAL_STATE.entities
        if e.key == "delay_start_hours" and isinstance(e, NumberDesc)
    )
    assert desc.write_fn is not None
    result = desc.write_fn(1.5, {"x.com.samsung.da.state": "Ready"})
    assert result is not None
    _path, body = result
    assert body == {"x.com.samsung.da.delayEndTime": "01:30:00"}
