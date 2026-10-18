"""Unit tests for LocalThingsSensor's sticky gate (issue #345).

Modeled on test_sensor_hysteresis.py: a _FakeCoordinator with just enough
surface for LocalThingsEntity/LocalThingsSensor, driven directly rather
than through a full coordinator/HA setup. Unlike that file, `data` here
is derived from the real `flatten()` over the same `resources` dict
`resource()` serves -- a single source of truth, so a test can't hide a
production desync bug by hand-computing the two independently.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest

from custom_components.localthings import sensor as sensor_module
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.sensor import LocalThingsSensor


class _Clock:
    """The sticky window's monotonic clock, advanced by hand so a window
    test neither waits it out nor depends on how fast the machine is."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(sensor_module, "time", SimpleNamespace(monotonic=fake.monotonic))
    return fake


_PROGRESS_DESC = next(e for e in OPERATIONAL_STATE.entities if e.key == "progress")
_PROGRESS_PERCENTAGE_DESC = next(
    e for e in OPERATIONAL_STATE.entities if e.key == "progress_percentage"
)
_MACHINE_STATE_DESC = next(e for e in OPERATIONAL_STATE.entities if e.key == "machine_state")

_HREF = "/operational/state/vs/0"
_ALL_BOUND = [
    BoundEntity(href=_HREF, capability=OPERATIONAL_STATE, desc=desc)
    for desc in OPERATIONAL_STATE.entities
]


class _FakeConfigEntry:
    def __init__(self):
        self.options: dict = {}


class _FakeCoordinator:
    """Just enough surface for LocalThingsEntity/LocalThingsSensor.

    `resources` is the one source of truth (standing in for the real
    coordinator's live cache); `data` and `resource()` are both derived
    from it exactly as the real coordinator derives `.data` from
    `flatten(self.bound, self._cache.snapshot())` and `.resource()` from
    the same snapshot -- so a test can't drift them apart in a way
    production couldn't.
    """

    def __init__(self):
        self.device_key = "TEST-SERIAL"
        self.config_entry = _FakeConfigEntry()
        self.resources: dict[str, dict] = {}

    def resource(self, href: str) -> dict:
        return self.resources.get(href) or {}

    @property
    def data(self) -> dict:
        return flatten(_ALL_BOUND, self.resources)


def _sensor(desc):
    coordinator = _FakeCoordinator()
    bound = BoundEntity(href=_HREF, capability=OPERATIONAL_STATE, desc=desc)
    sensor = LocalThingsSensor(cast(LocalThingsCoordinator, coordinator), bound)
    return sensor, coordinator


def _apply(coordinator, **fields):
    """Merge `fields` onto the href's existing rep, the same shallow
    {**cached, **rep} merge ObserveManager.apply() does in production
    (issue #27) -- so a field this call doesn't mention (e.g. a stale
    `progress` the device didn't repeat) stays exactly as a real partial
    update would leave it, rather than being silently wiped."""
    href_fields = {f"x.com.samsung.da.{k}": v for k, v in fields.items()}
    coordinator.resources[_HREF] = {**coordinator.resources.get(_HREF, {}), **href_fields}


def _replace(coordinator, **fields):
    """A full rep replacement -- for a device reporting fresh from
    scratch (e.g. a full poll GET), unlike _apply's partial-update merge."""
    coordinator.resources[_HREF] = {f"x.com.samsung.da.{k}": v for k, v in fields.items()}


def test_holds_finish_after_state_leaves_active():
    """The #345 regression this guards: progress used to fall straight to
    'Idle' the instant machine_state left active, even right after
    reporting Finish."""
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "finish"

    _replace(coordinator, state="Ready")  # device has moved on
    assert sensor.native_value == "finish"


def test_holds_finish_even_when_state_already_idle_at_first_observation():
    """issue #345's actual reported failure mode: state can already read
    idle by the time `progress: Finish` is ever observed (the report's
    washer skips a Run+Finish moment the same-family dryer still shows) --
    the hold must still arm from that single rep."""
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Ready", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "finish"

    # Still held on a later poll, even once the device stops repeating it.
    _replace(coordinator, state="Ready")
    assert sensor.native_value == "finish"


def test_progress_percentage_holds_100_regardless_of_the_raw_field_at_finish():
    """The hold pins 100 explicitly (sticky_value_fn), not whatever the
    raw field happened to hold at the finish moment -- a device that
    never populates progressPercentage at all, or (issue #9's failure
    mode) leaves a stale non-100 value there, must still show 100 while
    held, not None/unknown or the stale figure."""
    sensor, coordinator = _sensor(_PROGRESS_PERCENTAGE_DESC)

    _replace(coordinator, state="Run", progress="Finish")  # no progressPercentage at all
    assert sensor.native_value == 100

    _replace(coordinator, state="Ready")
    assert sensor.native_value == 100


def test_real_data_flows_through_unheld_while_active():
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Run", progress="Spin")
    assert sensor.native_value == "spin"

    _replace(coordinator, state="Run", progress="Rinse")
    assert sensor.native_value == "rinse"


def test_never_finished_stays_idle():
    """A device that never actually reached Finish (e.g. Stop pressed
    mid-cycle) must not be held at some prior mid-cycle value."""
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Run", progress="Spin")
    assert sensor.native_value == "spin"

    _replace(coordinator, state="Ready")
    assert sensor.native_value == "idle"


def test_a_new_cycle_starting_overrides_the_hold():
    """Starting a second cycle must show its own real progress
    immediately, not the held Finish from the previous one."""
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "finish"

    _replace(coordinator, state="Ready")
    assert sensor.native_value == "finish"  # still held

    _replace(coordinator, state="Run", progress="Wash")
    assert sensor.native_value == "wash"


def test_a_running_stage_after_finish_does_not_break_the_hold(clock):
    """Issue #358: the reporting dryer resets `progress` to its course's
    first stage in the same moment `state` goes idle -- observed twice,
    identically, as Cooling -> +60s Finish -> +24s 'Drying' -> +4s
    settled, with the reporter's machine_state history flipping to idle on
    the exact second progress reads 'Drying'. The hold must survive that,
    so the cycle still reads Drying, Cooling, Finish, Idle rather than the
    reported Drying, Cooling, Finish, Drying, Idle."""
    desc = replace(_PROGRESS_DESC, sticky_seconds=0.2)
    sensor, coordinator = _sensor(desc)

    _replace(coordinator, state="Run", progress="Drying", progressPercentage="40")
    assert sensor.native_value == "drying"

    _replace(coordinator, state="Run", progress="Cooling", progressPercentage="95")
    assert sensor.native_value == "cooling"

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "finish"

    # The tail: a running stage again, state already idle.
    _replace(coordinator, state="Ready", progress="Drying", progressPercentage="100")
    assert sensor.native_value == "finish"

    # ...then the device settles, still inside the window.
    _replace(coordinator, state="Ready", progress="None")
    assert sensor.native_value == "finish"

    clock.sleep(0.25)
    assert sensor.native_value == "idle"


def test_progress_percentage_survives_the_same_tail(clock):
    """#358's tail hits progress_percentage through the identical bypass;
    it must stay pinned at 100 rather than being released back to a raw
    mid-cycle figure."""
    desc = replace(_PROGRESS_PERCENTAGE_DESC, sticky_seconds=0.2)
    sensor, coordinator = _sensor(desc)

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert sensor.native_value == 100

    _replace(coordinator, state="Ready", progress="Drying", progressPercentage="40")
    assert sensor.native_value == 100

    clock.sleep(0.25)
    assert sensor.native_value == 0


def test_a_paused_new_cycle_is_left_to_the_window_rather_than_released(clock):
    """'Paused' isn't positive evidence of a new cycle, and #358's tail is
    indistinguishable from it. Nothing live is withheld by waiting --
    rep_fn shows 'Idle' while paused with or without a hold -- so the
    stale Finish just expires on schedule instead of being cut short."""
    desc = replace(_PROGRESS_DESC, sticky_seconds=0.05)
    sensor, coordinator = _sensor(desc)

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "finish"

    _replace(coordinator, state="Pause", progress="Wash")
    assert sensor.native_value == "finish"  # held out, not released

    clock.sleep(0.1)
    assert sensor.native_value == "idle"  # what a paused appliance always shows

    _replace(coordinator, state="Run", progress="Wash")
    assert sensor.native_value == "wash"


def test_a_flapping_finish_cannot_ratchet_an_open_window_forward(clock):
    """Edge-triggering stops a *stuck* Finish from extending the hold; a
    progress that flaps out of and back into Finish must not restart it
    either, or the bound stops being a bound."""
    desc = replace(_PROGRESS_DESC, sticky_seconds=0.3)
    sensor, coordinator = _sensor(desc)

    _replace(coordinator, state="Ready", progress="Finish")
    assert sensor.native_value == "finish"

    for _ in range(3):
        clock.sleep(0.05)
        _replace(coordinator, state="Ready", progress="None")
        assert sensor.native_value == "finish"
        _replace(coordinator, state="Ready", progress="Finish")
        assert sensor.native_value == "finish"

    # 0.15s of flapping so far -- the window still ends 0.3s after the
    # first Finish, not 0.3s after the most recent re-entry.
    clock.sleep(0.2)
    _replace(coordinator, state="Ready", progress="Finish")
    assert sensor.native_value == "idle"


def test_a_finish_after_the_window_closes_does_not_re_arm_it(clock):
    """Expiry doesn't re-open the door: with no new cycle in between, a
    second Finish is the same Finish. Re-arming on it would strobe the
    entity Finish -> Idle -> Finish once per window, re-firing exactly the
    announcements #345 is about -- so it takes a bypass (a cycle actually
    running) to make the hold available again.

    Distinct from the flap test above: there the window is still open, and
    the last read before expiry leaves the sticky condition *matching*.
    Here it has already closed, and the flap ends on a non-matching read,
    which is the state a spent-on-arm-only guard would let re-arm."""
    desc = replace(_PROGRESS_DESC, sticky_seconds=0.05)
    sensor, coordinator = _sensor(desc)

    _replace(coordinator, state="Ready", progress="Finish")
    assert sensor.native_value == "finish"

    clock.sleep(0.1)
    assert sensor.native_value == "idle"

    _replace(coordinator, state="Ready", progress="None")
    assert sensor.native_value == "idle"
    _replace(coordinator, state="Ready", progress="Finish")
    assert sensor.native_value == "idle"

    # A real cycle in between is what makes it available again.
    _replace(coordinator, state="Run", progress="Drying")
    assert sensor.native_value == "drying"
    _replace(coordinator, state="Run", progress="Finish")
    assert sensor.native_value == "finish"
    _replace(coordinator, state="Ready", progress="None")
    assert sensor.native_value == "finish"


def test_hold_expires_after_sticky_seconds(clock):
    # A fresh desc (frozen dataclass -- replace(), not mutation, so the
    # module-level _PROGRESS_DESC other tests share stays untouched) with
    # a short real window -- consistent with this codebase's own
    # settle-guard tests (test_observe.py's mark_write_pending(...,
    # settle_s=0.05)) -- rather than a zero window, which exercises "held
    # disabled" rather than "an armed hold actually expires".
    desc = replace(_PROGRESS_DESC, sticky_seconds=0.05)
    sensor, coordinator = _sensor(desc)

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "finish"

    _replace(coordinator, state="Ready")
    assert sensor.native_value == "finish"  # still within the window

    clock.sleep(0.1)
    assert sensor.native_value == "idle"


def test_a_progress_stuck_at_finish_does_not_hold_open_the_window_forever(clock):
    """Edge-triggering: sticky_fn keeps matching on every poll if the
    device's own `progress` field never resets on its own (the same class
    of quirk _completion_minutes' remainingTime-freeze workaround already
    documents) -- the hold must still expire on schedule from its first
    sighting, not have its deadline pushed out by every subsequent poll
    that still (correctly, from the device's perspective) reports Finish
    while machine_state has already gone idle."""
    desc = replace(_PROGRESS_DESC, sticky_seconds=0.05)
    sensor, coordinator = _sensor(desc)

    _replace(coordinator, state="Ready", progress="Finish")
    assert sensor.native_value == "finish"

    clock.sleep(0.03)
    # Device still (incorrectly) reports Finish on every subsequent poll --
    # must not restart the window.
    _replace(coordinator, state="Ready", progress="Finish")
    assert sensor.native_value == "finish"

    clock.sleep(0.03)  # 0.06s total since the first sighting -- past 0.05s
    _replace(coordinator, state="Ready", progress="Finish")
    assert sensor.native_value == "idle"


def test_non_sticky_sensor_is_unaffected():
    """machine_state has no sticky_fn -- reads straight through, unchanged."""
    sensor, coordinator = _sensor(_MACHINE_STATE_DESC)
    _replace(coordinator, state="Run")
    assert sensor.native_value == "active"
    _replace(coordinator, state="Ready")
    assert sensor.native_value == "idle"


def test_cycle_active_and_machine_state_are_never_held():
    """The whole point of scoping the hold to progress/progress_percentage
    only: cycle_active (Running) and machine_state must keep reflecting
    the device's real-time state throughout, never claiming the appliance
    is still running once it isn't."""
    progress_sensor, coordinator = _sensor(_PROGRESS_DESC)
    machine_state_bound = BoundEntity(
        href=_HREF, capability=OPERATIONAL_STATE, desc=_MACHINE_STATE_DESC
    )
    machine_state_sensor = LocalThingsSensor(
        cast(LocalThingsCoordinator, coordinator), machine_state_bound
    )

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert progress_sensor.native_value == "finish"
    assert machine_state_sensor.native_value == "active"

    _replace(coordinator, state="Ready")
    assert progress_sensor.native_value == "finish"  # held
    assert machine_state_sensor.native_value == "idle"  # real-time, unaffected


def test_a_partial_update_that_omits_progress_does_not_erase_the_hold():
    """A device's partial poll/notify that doesn't repeat `progress` at
    all (issue #27's documented shape) merges onto the cache rather than
    replacing it -- confirms the hold reads the merged rep, still showing
    'Finish' from the earlier full rep, not a wiped/absent field."""
    sensor, coordinator = _sensor(_PROGRESS_DESC)

    _replace(coordinator, state="Run", progress="Finish", progressPercentage="100")
    assert sensor.native_value == "finish"

    _apply(coordinator, state="Ready")  # partial merge, doesn't restate progress
    assert coordinator.resources[_HREF]["x.com.samsung.da.progress"] == "Finish"
    assert sensor.native_value == "finish"
