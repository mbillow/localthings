"""Operational-state capability: machine state, progress, remaining-time.

Shared by dryer/dishwasher/oven/washer families.
"""

import math
from datetime import UTC, datetime, timedelta

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


_PROGRESS_STATES = {
    "None": "idle",
    "Weightsensing": "weight_sensing",
    "Wash": "wash",
    "Rinse": "rinse",
    "Spin": "spin",
    "Finish": "finish",
    "Steaming": "steaming",
    "Airwashing": "air_washing",
    "Drying": "drying",
    "Cooling": "cooling",
    "Predrain": "pre_drain",
    "Prewash": "pre_wash",
}


def _progress(v):
    """Normalize known Samsung phase tokens for Home Assistant enum translation."""
    return _PROGRESS_STATES.get(v, v)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _is_active(rep):
    """Check if appliance is actively running and cycle is not finished."""
    return (
        _SAMSUNG_STATE_TO_OCF.get(rep.get("x.com.samsung.da.state")) == "active"
        and rep.get("x.com.samsung.da.progress") != "Finish"
    )


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
    return f"{h}:{m:02d}:00"


def _delay_field(rep):
    """Washer hardware reports the delay-until-start duration under
    'delayEndTime' instead of 'delayStartTime' (both hold a duration, not a
    wall-clock time -- see _delay_hours). Write back whichever key the
    device itself is using; default to delayStartTime for hardware that
    reports neither yet (matches prior behavior)."""
    return (
        "x.com.samsung.da.delayEndTime"
        if "x.com.samsung.da.delayEndTime" in rep
        else "x.com.samsung.da.delayStartTime"
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
            rep_fn=lambda rep: (
                _SAMSUNG_STATE_TO_OCF.get(rep.get("x.com.samsung.da.state")) == "active"
                and rep.get("x.com.samsung.da.progress") != "Finish"
            ),
        ),
        SensorDesc(
            key="progress",
            icon="mdi:progress-wrench",
            device_class="enum",
            options=tuple(_PROGRESS_STATES.values()),
            rep_fn=lambda rep: (
                "idle"
                if _SAMSUNG_STATE_TO_OCF.get(rep.get("x.com.samsung.da.state")) != "active"
                else _progress(rep.get("x.com.samsung.da.progress"))
            ),
        ),
        SensorDesc(
            key="progress_percentage",
            unit="%",
            state_class="measurement",
            rep_fn=lambda rep: (
                0
                if _SAMSUNG_STATE_TO_OCF.get(rep.get("x.com.samsung.da.state")) != "active"
                else _int(rep.get("x.com.samsung.da.progressPercentage"))
            ),
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
            rep_fn=lambda rep: _delay_hours(
                rep.get("x.com.samsung.da.delayStartTime")
                or rep.get("x.com.samsung.da.delayEndTime")
            ),
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
