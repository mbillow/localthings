"""Unit tests for LocalThingsSensor's hysteresis gate (finish_time churn).

The threshold is min(option, max(resolution, 5% of what remains)) (#772), so
these tests place the finish relative to now.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from custom_components.localthings import finish
from custom_components.localthings.const import CONF_FINISH_TIME_HYSTERESIS_MINUTES
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.sensor import LocalThingsSensor

_FINISH_TIME_DESC = next(e for e in OPERATIONAL_STATE.entities if e.key == "finish_time")


class _FakeConfigEntry:
    def __init__(self, options):
        self.options = options


class _FakeCoordinator:
    """Just enough surface for LocalThingsEntity/LocalThingsSensor."""

    def __init__(self, threshold_minutes):
        self.device_key = "TEST-SERIAL"
        self.config_entry = _FakeConfigEntry(
            {
                CONF_FINISH_TIME_HYSTERESIS_MINUTES: threshold_minutes,
            }
        )
        self.data: dict = {}
        self.rep: dict = {}

    def canonical_resources(self, subdevice):
        return {OPERATIONAL_STATE.href: self.rep}


def _in(minutes: float, seconds: float = 0) -> datetime:
    return datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=minutes, seconds=seconds)


def _sensor(threshold_minutes=3):
    coordinator = _FakeCoordinator(threshold_minutes)
    href = OPERATIONAL_STATE.href
    assert href is not None
    bound = BoundEntity(
        href=href,
        capability=OPERATIONAL_STATE,
        desc=_FINISH_TIME_DESC,
    )
    sensor = LocalThingsSensor(cast(LocalThingsCoordinator, coordinator), bound)
    return sensor, coordinator


def test_small_change_is_suppressed():
    sensor, coordinator = _sensor(threshold_minutes=3)
    base = _in(120)

    coordinator.data = {"finish_time": base}
    assert sensor.native_value == base

    coordinator.data = {"finish_time": base + timedelta(minutes=1)}
    assert sensor.native_value == base, "a 1-minute wobble should be held back"


def test_change_past_threshold_is_reported():
    sensor, coordinator = _sensor(threshold_minutes=3)
    base = _in(120)

    coordinator.data = {"finish_time": base}
    assert sensor.native_value == base

    new = base + timedelta(minutes=5)
    coordinator.data = {"finish_time": new}
    assert sensor.native_value == new


def test_zero_threshold_disables_hysteresis():
    sensor, coordinator = _sensor(threshold_minutes=0)
    base = _in(120)

    coordinator.data = {"finish_time": base}
    assert sensor.native_value == base

    new = base + timedelta(seconds=1)
    coordinator.data = {"finish_time": new}
    assert sensor.native_value == new


def test_cycle_end_passes_through_immediately():
    """A cycle ending (finish_time -> None) must never be held back."""
    sensor, coordinator = _sensor(threshold_minutes=3)
    base = _in(120)

    coordinator.data = {"finish_time": base}
    assert sensor.native_value == base

    coordinator.data = {"finish_time": None}
    assert sensor.native_value is None


def test_non_hysteresis_sensor_is_unaffected():
    """A SensorDesc without hysteresis=True reads straight through, unchanged."""
    machine_state_desc = next(e for e in OPERATIONAL_STATE.entities if e.key == "machine_state")
    coordinator = _FakeCoordinator(threshold_minutes=3)
    href = OPERATIONAL_STATE.href
    assert href is not None
    bound = BoundEntity(
        href=href,
        capability=OPERATIONAL_STATE,
        desc=machine_state_desc,
    )
    sensor = LocalThingsSensor(cast(LocalThingsCoordinator, coordinator), bound)

    coordinator.data = {"machine_state": "active"}
    assert sensor.native_value == "active"
    coordinator.data = {"machine_state": "idle"}
    assert sensor.native_value == "idle"


def test_a_short_cycle_gets_a_deadband_of_seconds():
    """About 5% of three minutes is 9 s: a 10 s panel change shows, a 1 s
    wobble between reads doesn't."""
    sensor, coordinator = _sensor(threshold_minutes=3)
    coordinator.rep = {finish.FIELD: {"finish": _in(3), "seconds": True}}
    base = _in(3)
    coordinator.data = {"finish_time": base}
    assert sensor.native_value == base

    coordinator.data = {"finish_time": base - timedelta(seconds=1)}
    assert sensor.native_value == base
    coordinator.data = {"finish_time": base + timedelta(seconds=10)}
    assert sensor.native_value == base + timedelta(seconds=10)


def test_a_minute_only_board_never_drops_below_a_minute():
    sensor, coordinator = _sensor(threshold_minutes=3)
    base = _in(3)
    coordinator.data = {"finish_time": base}
    assert sensor.native_value == base

    coordinator.data = {"finish_time": base + timedelta(seconds=30)}
    assert sensor.native_value == base
    coordinator.data = {"finish_time": base + timedelta(minutes=1)}
    assert sensor.native_value == base + timedelta(minutes=1)


def test_the_option_caps_a_long_cycle():
    """5% of two hours is 6 minutes; the 3-minute option is the ceiling."""
    sensor, coordinator = _sensor(threshold_minutes=3)
    base = _in(120)
    coordinator.data = {"finish_time": base}
    assert sensor.native_value == base

    coordinator.data = {"finish_time": base + timedelta(minutes=3)}
    assert sensor.native_value == base + timedelta(minutes=3)
