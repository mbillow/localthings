"""When a running cycle finishes, timed from when each remainingTime arrived.

`now() + remainingTime` recomputed on every flatten puts the finish late by
however old the reading is. The coordinator re-flattens whenever any resource
updates, and an MW7300B pushes /operational/state/vs/0 only about every 10 s,
so its finish crept up to 6 s late between reads (issue #772). This store
records the finish when a new remainingTime value arrives, and the coordinator
merges it onto the rep under FIELD for the finish_time descriptors.

Two readings are not taken: the /device/0 sweep's value runs 2-3 s behind a
push, so a sweep that would move the finish later is ignored; and a pause
freezes the finish until the cycle resumes, when it is re-anchored. Rounding
and the deadband belong to the reader (finish_at and the sensor's hysteresis).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

REMAINING = "x.com.samsung.da.remainingTime"
STATE = "x.com.samsung.da.state"

# Synthetic, read-time only, like cloudcourse.FIELD: never cached, written to
# the device, or part of a diagnostics dump.
FIELD = "x.localthings.finish"

_PAUSED = ("Pause", "Paused")
_IDLE = ("Ready", "End", "Stop")


def remaining_seconds(raw) -> int | None:
    """'HH:MM:SS' (or 'MM:SS') -> total seconds, or None."""
    if not isinstance(raw, str):
        return None
    try:
        parts = [int(p) for p in raw.split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        parts = [0, *parts]
    if len(parts) != 3:
        return None
    h, m, s = parts
    return h * 3600 + m * 60 + s


def _has_seconds(total_s: int) -> bool:
    """Minute-only boards park the seconds at :00 or :59 (NX60T8311SS, #457)."""
    return total_s % 60 not in (0, 59)


@dataclass
class _Anchor:
    raw: object = None
    finish: datetime | None = None
    paused: bool = False
    seconds: bool = False


class FinishAnchors:
    """Per actual href. Mutated from whichever thread applied the update, so
    every access takes the lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._anchors: dict[str, _Anchor] = {}

    def observe(self, href: str, rep: dict, source: str, now: datetime) -> bool:
        """Learn from one applied rep; True if the finish changed."""
        if REMAINING not in rep:
            return False
        raw = rep.get(REMAINING)
        state = rep.get(STATE)
        total_s = remaining_seconds(raw)
        with self._lock:
            anchor = self._anchors.setdefault(href, _Anchor())
            before = anchor.finish
            if total_s and _has_seconds(total_s):
                anchor.seconds = True
            if state in _PAUSED:
                anchor.paused = True
            elif not total_s or state in _IDLE:
                anchor.finish, anchor.paused = None, False
            elif anchor.paused or anchor.finish is None or raw != anchor.raw:
                finish = now + timedelta(seconds=total_s)
                stale = (
                    source == "sweep"
                    and not anchor.paused
                    and anchor.finish is not None
                    and finish > anchor.finish
                )
                if not stale:
                    anchor.finish, anchor.paused = finish, False
            # Compared with the previous reading only: adding time on the
            # panel can bring back a value seen earlier in the cycle.
            anchor.raw = raw
            return anchor.finish != before

    def field(self, href: str) -> dict | None:
        with self._lock:
            anchor = self._anchors.get(href)
            if anchor is None or anchor.finish is None:
                return None
            return {"finish": anchor.finish, "seconds": anchor.seconds}

    def hrefs(self) -> list[str]:
        with self._lock:
            return [href for href, anchor in self._anchors.items() if anchor.finish is not None]


def finish_at(rep: dict, now: datetime | None = None) -> datetime | None:
    """The finish at the precision the board reports: to the second where it
    reports seconds, else rounded to the *nearest* minute (rounding down ran
    ahead of the panel, #771). Falls back to now() + remainingTime when no
    anchor is merged (a dump, or before the first reading)."""
    anchor = rep.get(FIELD)
    if isinstance(anchor, dict) and isinstance(anchor.get("finish"), datetime):
        finish, seconds = anchor["finish"], bool(anchor.get("seconds"))
    else:
        total_s = remaining_seconds(rep.get(REMAINING))
        if not total_s:
            return None
        finish = (now or datetime.now(UTC)) + timedelta(seconds=total_s)
        seconds = _has_seconds(total_s)
    if seconds:
        return finish.replace(microsecond=0)
    return (finish + timedelta(seconds=30)).replace(second=0, microsecond=0)


def resolution(rep: dict) -> timedelta:
    """The smallest step this rep's finish can move by."""
    anchor = rep.get(FIELD)
    seconds = bool(anchor.get("seconds")) if isinstance(anchor, dict) else False
    return timedelta(seconds=1 if seconds else 60)
