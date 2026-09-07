"""Operational-state capability: machine state, progress, remaining-time.

Shared by dryer/dishwasher/oven/washer families.
"""

import math
from datetime import UTC, datetime, timedelta

from ...catalog import translated_states
from ..capability import Capability
from ..entities import BinarySensorDesc, ButtonDesc, NumberDesc, SensorDesc

_SAMSUNG_STATE_TO_OCF = {
    "Ready": "idle",
    "Run": "active",
    "Running": "active",
    "Pause": "pause",
    "Paused": "pause",
    "End": "idle",
    "Stop": "idle",
}


def _to_ocf(v):
    return _SAMSUNG_STATE_TO_OCF.get(v, v) if v is not None else None


def _progress(v):
    return "idle" if v in (None, "None") else str(v).lower()


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _state_is_active(rep):
    return _SAMSUNG_STATE_TO_OCF.get(rep.get("x.com.samsung.da.state")) == "active"


def _is_active(rep):
    """Check if appliance is actively running and cycle is not finished."""
    return _state_is_active(rep) and rep.get("x.com.samsung.da.progress") != "Finish"


def _just_finished(rep):
    """progress/progress_percentage's sticky_fn (issue #345, see sensor.py's
    _apply_sticky): arms their grace window the moment `progress` reads
    'Finish'.

    Deliberately not also requiring `state == 'active'` in the same rep,
    unlike _is_active above: #345 reports a washer whose `state` can
    already read idle by the time `progress` is observed at 'Finish' --
    the same-family dryer's firmware apparently keeps `state` at 'active'
    longer, per the report -- so requiring both together risked the arm
    condition never actually firing on the one device this fixes.
    _apply_sticky's edge-triggering (only a fresh False->True transition
    (re)arms) is what keeps a `progress` stuck at 'Finish' indefinitely
    (the same class of quirk `_completion_minutes` below already works
    around) from holding this open forever instead."""
    return rep.get("x.com.samsung.da.progress") == "Finish"


def _new_cycle_running(rep):
    """progress/progress_percentage's sticky_bypass_fn: drop the #345 hold
    early once a new cycle is genuinely running.

    Gated on `state == 'active'`, unlike _just_finished's arm condition
    above: issue #358's dryer resets `progress` to its course's first
    stage ('Drying') in the same moment `state` goes idle, ~4s before
    settling to 'None' -- confirmed by the reporter's machine_state
    history, which flips to idle on the exact second progress reads
    'Drying', in both captured cycles. A bypass keyed on the progress
    code alone read that as a new cycle and republished it. Releasing
    late costs nothing -- an unreleased hold still expires on its own --
    so this side takes the stronger signal."""
    v = rep.get("x.com.samsung.da.progress")
    return _state_is_active(rep) and v is not None and v not in ("None", "Finish")


def _remaining_seconds(raw):
    """'HH:MM:SS' (or 'MM:SS') -> total seconds, or None."""
    if not isinstance(raw, str):
        return None
    try:
        parts = [int(p) for p in raw.split(":")]
    except (ValueError, TypeError):
        return None

    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = 0, *parts
    else:
        return None

    return h * 3600 + m * 60 + s


def _delay_hours(v):
    """delayStartTime is a duration until the cycle starts, not a
    wall-clock time -- "01:00" means "1 hour from when you press start",
    not "1 AM"."""
    total_seconds = _remaining_seconds(v)
    if total_seconds is None:
        return 0.0 if not v else None
    return total_seconds / 3600.0


def _format_delay(hours):
    total_minutes = round(max(float(hours), 0) * 60)
    h, m = divmod(total_minutes, 60)
    return f"{h:02d}:{m:02d}:00"


def _delay_field(rep):
    """The delay key this device actually uses, for reads and writes alike.

    Dishwashers report `delayStartTime`, laundry reports `delayEndTime`, and
    no dump carries both -- so the preference order only decides the
    hypothetical overlap. It prefers delayStartTime because that is the field
    whose meaning matches this entity: a delay until the cycle *starts*.

    The two are not synonyms, which #427 asked about and this file used to
    assert they were. `delayEndTime` counts down to the cycle's *end*: the
    WF80H dump caught mid-`Delaywash` reports `delayEndTime` and
    `remainingTime` as the same `09:26:00`, and remainingTime runs to
    completion. That also explains #308, where a delay set to 1 h came back
    as ~9 h -- an appliance cannot finish sooner than its cycle takes, so a
    delay-until-end write clamps up to the cycle length.

    Both still hold a duration rather than a wall-clock time (see
    _delay_hours). See docs/investigations/laundry-delay-end.md.
    """
    return (
        "x.com.samsung.da.delayStartTime"
        if "x.com.samsung.da.delayStartTime" in rep
        else "x.com.samsung.da.delayEndTime"
    )


def _finish_time(rep):
    if not _is_active(rep):
        return None
    total_s = _remaining_seconds(rep.get("x.com.samsung.da.remainingTime"))
    if not total_s:
        return None
    # Round to whole minutes -- remainingTime itself only has minute
    # resolution, but datetime.now()'s fresh seconds/microseconds would
    # otherwise change the result on nearly every poll, flooding the
    # recorder with values that look identical once the UI rounds them.
    finish = datetime.now(UTC) + timedelta(seconds=total_s)
    return finish.replace(second=0, microsecond=0)


def _completion_minutes(rep):
    """Parse remaining time into minutes directly from device payload."""
    raw = rep.get("x.com.samsung.da.remainingTime") or rep.get("remainingTime")
    total_s = _remaining_seconds(raw)
    if total_s is None:
        return None

    # Avoid the firmware bug where it freezes at 1 minute post-cycle
    if rep.get("x.com.samsung.da.progress") == "Finish":
        return 0

    return math.ceil(total_s / 60)


# Shared by dryer/dishwasher/oven/washer -- oven.py imports this directly
# rather than keeping its own copy, since both wrote the identical
# state='Ready' RMW.
STOP_BUTTON = ButtonDesc(
    key="stop",
    field="",
    payload="Ready",
    icon="mdi:stop",
    write_fn=lambda p, rep, href=None: (
        ["operational", "state", "vs", "0"],
        {"x.com.samsung.da.state": p},
    ),
)

OPERATIONAL_STATE = Capability(
    href="/operational/state/vs/0",
    poll_tier="hot",
    entities=(
        SensorDesc(
            key="machine_state",
            field="x.com.samsung.da.state",
            device_class="enum",
            options=("idle", "active", "pause"),
            translation_key="machine_state",
            value_fn=_to_ocf,
        ),
        # cycle_active is a bool derived from machine_state, gated on
        # progress too since firmware keeps state='Run' after progress
        # reaches 'Finish' (a stuck 'Running' indication otherwise). Named
        # 'Running' in the catalog, not 'Cycle active' -- this href is
        # shared with oven, and 'cycle' is laundry-specific vocabulary.
        BinarySensorDesc(
            key="cycle_active",
            device_class="running",
            rep_fn=_is_active,
        ),
        # sticky_* (issue #345): once progress reads 'Finish', keep
        # showing Finish/100 for a grace window even after machine_state
        # reverts, rather than falling to Idle/0 the instant it does --
        # see sensor.py's _apply_sticky. rep_fn below is unchanged and
        # stays the only definition of a live value -- the hold decides
        # only *whether* to freeze. A second, ungated one here is what
        # let issue #358's post-Finish tail reach the entity.
        SensorDesc(
            key="progress",
            icon="mdi:progress-wrench",
            device_class="enum",
            options=tuple(sorted(translated_states("sensor", "progress"))),
            rep_fn=lambda rep: (
                "idle"
                if not _state_is_active(rep)
                else _progress(rep.get("x.com.samsung.da.progress"))
            ),
            sticky_fn=_just_finished,
            # The catalog key, not the device's 'Finish': rep_fn is normalized
            # now, and a held value outside `options` is what HA rejects.
            sticky_value_fn=lambda rep: "finish",
            sticky_bypass_fn=_new_cycle_running,
        ),
        SensorDesc(
            key="progress_percentage",
            unit="%",
            state_class="measurement",
            rep_fn=lambda rep: (
                0
                if not _state_is_active(rep)
                else _int(rep.get("x.com.samsung.da.progressPercentage"))
            ),
            sticky_fn=_just_finished,
            sticky_value_fn=lambda rep: 100,
            sticky_bypass_fn=_new_cycle_running,
        ),
        # Only show finish time while actively running -- firmware leaves a
        # stale remainingTime after a cycle ends, frozen at '00:01:00'.
        SensorDesc(
            key="finish_time", device_class="timestamp", hysteresis=True, rep_fn=_finish_time
        ),
        SensorDesc(
            key="completion_minutes",
            icon="mdi:clock-outline",
            unit="min",
            device_class="duration",
            state_class="measurement",
            exists_fn=lambda rep, resources: _completion_minutes(rep) is not None,
            rep_fn=_completion_minutes,
        ),
        NumberDesc(
            key="delay_start_hours",
            icon="mdi:timer-plus-outline",
            device_class="duration",
            unit="h",
            native_min=0,
            native_max=24,
            step=1,
            # Same field the write targets: reading one key while writing
            # another would show a stale value after every set.
            rep_fn=lambda rep: _delay_hours(rep.get(_delay_field(rep))),
            write_fn=lambda p, rep, href=None: (
                ["operational", "state", "vs", "0"],
                {_delay_field(rep): _format_delay(p)},
            ),
        ),
        ButtonDesc(
            key="start",
            field="",
            payload="Run",
            icon="mdi:play",
            write_fn=lambda p, rep, href=None: (
                ["operational", "state", "vs", "0"],
                {"x.com.samsung.da.state": p},
            ),
        ),
        ButtonDesc(
            key="pause",
            field="",
            payload="Pause",
            icon="mdi:pause",
            write_fn=lambda p, rep, href=None: (
                ["operational", "state", "vs", "0"],
                {"x.com.samsung.da.state": p},
            ),
        ),
        STOP_BUTTON,
    ),
)
