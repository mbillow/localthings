"""Decode the usage history the appliance keeps at `/file/transfer/vs/0`.

Issue #301. The resource serves a file -- named `/mnt/usage.db` whatever it
actually is, a firmware default that matches neither name `/file/list/vs/0`
reports -- as a wrapper of items each carrying a raw `blob`. Despite the
name it is not SQLite on any board measured so far, but a flat array of
fixed 12-byte records.

Two record shapes are known, and telling them apart matters for correctness
rather than tidiness, because **their second field means different things**:

    uint32-leading   <uint32 LE timestamp><uint32 LE cumulative energy,
                      tenths of a kWh><uint32 LE family-specific>
    uint64-leading   the ARTIK051_KRAC_18K shape (#301), whose value field
                      is cumulative *runtime hours*, not energy

Publishing one as the other would put hours in a kWh sensor, so the
`uint32`-leading shape has to be positively identified rather than assumed:
`records` returns nothing unless the whole file reads as a plausible,
ordered, daily-ish series whose counter actually moves. #329 proposed keying
on the leading field, which is necessary but *not* sufficient -- a
`uint64`-leading record whose date is a plain Unix timestamp puts that
timestamp in the low half and zeros in the high half, so it reads as a valid
leading `uint32` followed by a value of 0. That is why a counter which is
zero, or which never moves across the file, is refused too: on the shape
this module can read those mean "nothing to report", and on the shape it
cannot they are the tell.

The third field carries a different quantity on every family measured: zero
on a `DA_WM_TP1_21_COMMON` washer and on a dishwasher, the firmware's
monthly billing bucket on a `TP1X_REF_21K` fridge, and cumulative runtime
hours on an `ARTIK051_PRAC_20K`. Four families, one byte layout, three
meanings, and nothing in the bytes to say which -- so it is classified, not
assumed, and the classification decides both what is published from it and
whether the *second* field's scale is known (see
`_energy_scale_is_confirmed`). See
docs/investigations/file-transfer-usage-db.md.
"""

from __future__ import annotations

import itertools
import struct

ITEMS_FIELD = "x.com.samsung.items"
BLOB_FIELD = "x.com.samsung.blob"

RECORD_SIZE = 12
_RECORD = struct.Struct("<III")

# A timestamp outside this window is the tell that the leading field is not a
# timestamp at all. Lower bound predates every capture on record; upper bound
# is comfortably inside what a uint32 can hold (2106).
_TS_MIN = 1_420_070_400  # 2015-01-01
_TS_MAX = 4_102_444_800  # 2100-01-01

# There is deliberately no bound on the gap between records. One record is
# written per day the appliance actually *ran* -- #301 notes a 25th-to-29th
# gap on an AC -- so a seasonally-used one legitimately skips months, and any
# bound short enough to catch a misparse would throw that appliance's whole
# file away. The timestamp window above plus strict ordering is what
# separates a real series from a misread field.


def _blob(rep: dict) -> bytes | None:
    """The first item's raw blob, or None if this rep carries none."""
    items = rep.get(ITEMS_FIELD)
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if not isinstance(first, dict):
        return None
    blob = first.get(BLOB_FIELD)
    return blob if isinstance(blob, bytes) else None


def records(rep: dict) -> list[tuple[int, int, int]] | None:
    """Every record in `rep`'s blob, or None if it is not the uint32-leading
    shape this module can read.

    None means "not understood", never "empty": an unrecognized payload has
    to produce no entity at all rather than a plausible-looking wrong number.
    """
    blob = _blob(rep)
    if not blob or len(blob) < RECORD_SIZE or len(blob) % RECORD_SIZE:
        return None

    out: list[tuple[int, int, int]] = []
    for offset in range(0, len(blob), RECORD_SIZE):
        timestamp, value, third = _RECORD.unpack_from(blob, offset)
        if not _TS_MIN <= timestamp <= _TS_MAX:
            return None
        if out:
            previous_ts, previous_value, _ = out[-1]
            # Strictly increasing in time and never decreasing in a
            # cumulative counter.
            if timestamp <= previous_ts:
                return None
            if value < previous_value:
                return None
        out.append((timestamp, value, third))

    # A cumulative energy counter that is zero, or that never moved across
    # the whole window, has nothing to publish either way -- and is what a
    # misread uint64-leading file looks like (see the module docstring).
    if out[-1][1] <= 0:
        return None
    if len(out) > 1 and out[-1][1] <= out[0][1]:
        return None
    return out


# A month label is 1..12 and steps by one. A runtime counter is far larger
# and moves in half hours -- "all 360 deltas across both units divisible by 5
# in tenths" (#301), "every delta on every unit is divisible by 5 tenths"
# (#329). The +1 of a month rollover is what that test rejects.
_MAX_MONTH = 12
_RUNTIME_QUANTUM = 5  # tenths of an hour


def _is_runtime_series(values: list[int]) -> bool:
    """True when the third field is a cumulative runtime counter.

    A counter that never moves still qualifies: a multi-split head that sat
    idle for the whole window has a flat but perfectly real runtime total,
    and dropping its entity on that basis would make the sensor disappear
    exactly when the appliance is off.
    """
    if values[-1] <= _MAX_MONTH:
        return False
    deltas = [b - a for a, b in itertools.pairwise(values)]
    if any(delta < 0 for delta in deltas):
        return False
    return all(delta % _RUNTIME_QUANTUM == 0 for delta in deltas)


def _energy_scale_is_confirmed(values: list[int]) -> bool:
    """True when the third field identifies this file as one of the families
    whose second field is known to hold tenths of a kWh.

    A *positive* identification, and it has to be. The obvious shape --
    "publish energy unless the third field looks like runtime" -- is unsafe,
    because failing to recognise a runtime counter is not the same as
    recognising a tenths-of-a-kWh one. A brand-new multi-split head whose
    runtime is still under 1.2 h reads as a month label, and an idle head's
    flat counter reads as neither; both would then have the PRAC's plain-Wh
    energy field divided by 10 and reported 100x high into a
    `total_increasing` sensor.

    So only the two measured shapes are accepted:

    - every value zero -- a `DA_WM_TP1_21_COMMON` washer (#301) and a
      dishwasher, both confirmed against their own `cumulativePower`;
    - a month label: every value in 1..12 *and* at least one consecutive
      repeat, since a label spans a month of daily records while a counter
      climbing one unit a day does not. The repeat is what separates a
      `TP1X_REF_21K` fridge from a new head reading 5, 6, 7.
    """
    if not any(values):
        return True
    if not all(1 <= value <= _MAX_MONTH for value in values):
        return False
    return any(a == b for a, b in itertools.pairwise(values))


def cumulative_energy_kwh(rep: dict) -> float | None:
    """The newest record's cumulative energy, in kWh.

    The file is a once-a-day rollup, so this steps once a day rather than
    tracking the meter continuously -- see common.FILE_TRANSFER for why that
    is still worth an entity on an appliance that reports no meter at all.

    Published only where the third field positively identifies a family whose
    second field is known to hold tenths of a kWh, because the *scale* is
    family-dependent. A washer (#301), a dishwasher and a fridge all store
    tenths of a kWh, each confirmed against its own `cumulativePower`. The
    one family that pairs energy with runtime, the `ARTIK051_PRAC_20K` of
    #329, stores plain Wh -- its `fieldA` equals `cumulativePower` outright
    rather than a hundredth of it. Nothing in the bytes distinguishes
    5118585 Wh from 5118585 tenths of a kWh, so reading that board with this
    scale reports 511858.5 kWh instead of 5118.6.

    See `_energy_scale_is_confirmed` for why this is a positive test rather
    than "not runtime".
    """
    parsed = records(rep)
    if not parsed:
        return None
    if not _energy_scale_is_confirmed([r[2] for r in parsed]):
        return None
    return parsed[-1][1] / 10


def cumulative_energy_kwh_from_wh(rep: dict) -> float | None:
    """The newest record's cumulative energy, in kWh, for an air conditioner,
    which stores plain Wh: the `A-CAWW-TP2-20-COMMON` System AC (#741), whose
    all-zero third field would otherwise pass for a washer's and read 100x
    high, as well as the PRAC (#329) and TP2X_RAC (#488).

    Only the all-zero shape is published. A runtime-carrying head's energy
    is the shared outdoor unit's (#329), so one sensor per head would count
    it once per head.
    """
    parsed = records(rep)
    if not parsed or any(r[2] for r in parsed):
        return None
    return parsed[-1][1] / 1000


def cumulative_runtime_hours(rep: dict) -> float | None:
    """The newest record's cumulative running time, in hours.

    The one number on a multi-head system that is genuinely per-unit: #329
    read three heads of one multi-split and found this field differing per
    head (6270.0 h, 799.5 h, 1660.0 h) while the energy field beside it was
    the shared outdoor unit's, identical on all three.

    Only the `uint32`-leading shape is read. The `ARTIK051_KRAC_18K` of #301
    keeps runtime too, but in the `uint64`-leading layout `records` refuses,
    and no capture of one exists to write a decoder against.
    """
    parsed = records(rep)
    if not parsed:
        return None
    values = [r[2] for r in parsed]
    if not _is_runtime_series(values):
        return None
    return values[-1] / 10
