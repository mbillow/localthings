"""Decoding the usage history at /file/transfer/vs/0 (issue #301).

Backed by real captures in fixtures/usage_blobs.json -- a dishwasher, a
TP1X_REF_21K fridge and a TP2X_RAC_20K air conditioner (#488), each read
live and reconciled against its own appliance's /energy/consumption/vs/0
at the time -- see that file's notes.
"""

from __future__ import annotations

import itertools
import json
import struct
from pathlib import Path

import pytest

from custom_components.localthings.registry import usagedb
from custom_components.localthings.registry.encode import from_json_safe

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _capture(name: str):
    data = json.loads((FIXTURES / "usage_blobs.json").read_text())[name]
    return from_json_safe(data["blob"]), data


def _rep(blob: bytes) -> dict:
    return {
        usagedb.ITEMS_FIELD: [{"x.com.samsung.name": "/mnt/usage.db", usagedb.BLOB_FIELD: blob}]
    }


def _synthetic(records, start=1_780_000_000, step=86400) -> bytes:
    """Build a blob from (value, third) pairs on a daily cadence."""
    return b"".join(
        struct.pack("<III", start + i * step, value, third)
        for i, (value, third) in enumerate(records)
    )


# ---------------------------------------------------------------------------
# The real captures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["dishwasher", "refrigerator_tp1x_ref_21k"])
def test_a_real_capture_decodes_to_its_recorded_totals(name):
    blob, expected = _capture(name)
    rep = _rep(blob)

    parsed = usagedb.records(rep)
    assert parsed is not None
    assert len(parsed) == expected["records"]
    assert usagedb.cumulative_energy_kwh(rep) == expected["cumulative_kwh"]


def test_the_fridge_file_is_one_ordered_record_per_day():
    blob, _ = _capture("refrigerator_tp1x_ref_21k")
    parsed = usagedb.records(_rep(blob))
    assert parsed is not None

    timestamps = [r[0] for r in parsed]
    values = [r[1] for r in parsed]
    assert timestamps == sorted(timestamps)
    assert values == sorted(values)
    # ~181 consecutive days, so every gap is about one day.
    gaps = {round((b - a) / 86400) for a, b in itertools.pairwise(timestamps)}
    assert gaps == {1}


def test_the_fridges_third_field_is_a_month_label_not_runtime():
    """It runs 3..8 across a March-to-August file. Classifying it as runtime
    would publish "8 hours" and, worse, would change how the energy field is
    scaled -- see test_energy_is_refused_where_the_scale_is_unconfirmed."""
    blob, _ = _capture("refrigerator_tp1x_ref_21k")
    parsed = usagedb.records(_rep(blob))
    assert parsed is not None
    assert {r[2] for r in parsed} == {3, 4, 5, 6, 7, 8}
    assert usagedb.cumulative_runtime_hours(_rep(blob)) is None


def test_a_washer_shaped_zero_third_field_is_not_runtime():
    """Zero on all 281 of #301's washer records."""
    blob = _synthetic([(100, 0), (120, 0), (140, 0)])
    assert usagedb.cumulative_runtime_hours(_rep(blob)) is None


# ---------------------------------------------------------------------------
# Runtime hours (#329's ARTIK051_PRAC_20K)
#
# NOT fixture-backed: no raw capture of this board exists here, only the
# reported field description and the three per-head totals. Per this
# project's fixture-integrity rule these are hand-built reps matching those
# quoted shapes, kept clearly distinct from the real captures above. Replace
# with a fixture if a dump ever surfaces.
# ---------------------------------------------------------------------------


def _prac_like(last_tenths_of_hours: int, days: int = 12) -> bytes:
    """A PRAC-shaped file: <ts><energy in Wh><runtime in tenths of an hour>,
    the runtime moving in half-hour steps as both AC reports describe."""
    step = 5 * (last_tenths_of_hours // (5 * days) or 1)
    start = last_tenths_of_hours - step * (days - 1)
    return _synthetic([(4_888_612 + i * 1200, start + i * step) for i in range(days)])


@pytest.mark.parametrize(
    "tenths,hours",
    [(62700, 6270.0), (7995, 799.5), (16600, 1660.0)],
    ids=["office", "bedroom", "basias"],
)
def test_runtime_decodes_to_each_heads_own_total(tenths, hours):
    """#329's three heads of one multi-split, whose runtime differs per head
    while the energy field beside it is the shared outdoor unit's."""
    assert usagedb.cumulative_runtime_hours(_rep(_prac_like(tenths))) == hours


def test_runtime_requires_the_half_hour_quantisation():
    """ "All 360 deltas across both units divisible by 5 in tenths" (#301),
    "every delta on every unit is divisible by 5 tenths" (#329). A counter
    that moves in ones is a month label, not hours."""
    blob = _synthetic([(100, 20), (120, 21), (140, 22)])
    assert usagedb.cumulative_runtime_hours(_rep(blob)) is None


def test_runtime_survives_a_head_that_sat_idle():
    """A multi-split head idle for the whole window has a flat but real
    running total. Refusing it would make the sensor vanish exactly when the
    appliance is off -- and would hand the file back to the energy path,
    whose scale is not known on that board."""
    blob = _synthetic([(100, 500), (120, 500), (140, 500)])
    assert usagedb.cumulative_runtime_hours(_rep(blob)) == 50.0
    assert usagedb.cumulative_energy_kwh(_rep(blob)) is None


def test_runtime_refuses_a_backwards_counter():
    """A month label wrapping 12 -> 1 at a year boundary, among other things."""
    blob = _synthetic([(100, 600), (120, 605), (140, 5)])
    assert usagedb.cumulative_runtime_hours(_rep(blob)) is None


def test_energy_is_refused_where_the_scale_is_unconfirmed():
    """The reason the classifier is strict. Every family whose third field is
    zero or a month label stores tenths of a kWh -- confirmed against each
    one's own cumulativePower. The one family that pairs energy with runtime,
    #329's ARTIK051_PRAC_20K, stores plain Wh: its fieldA *equals*
    cumulativePower rather than a hundredth of it. Reading 5118585 Wh with
    the other scale reports 511858.5 kWh instead of 5118.6."""
    rep = _rep(_prac_like(62700))
    assert usagedb.cumulative_runtime_hours(rep) == 6270.0
    assert usagedb.cumulative_energy_kwh(rep) is None


def test_a_real_runtime_capture_keeps_energy_in_plain_wh():
    """Issue #488's TP2X_RAC_20K: the same energy-plus-runtime shape as #329,
    on a second board family, and its second field again equals the live
    cumulativePower outright -- so energy stays refused."""
    blob, expected = _capture("airconditioner_tp2x_rac_20k")
    rep = _rep(blob)
    parsed = usagedb.records(rep)
    assert parsed is not None
    assert len(parsed) == expected["records"]
    assert parsed[-1][1] == expected["cumulative_wh"]
    assert usagedb.cumulative_runtime_hours(rep) == expected["runtime_hours"]
    assert usagedb.cumulative_energy_kwh(rep) is None


# ---------------------------------------------------------------------------
# Refusing what it cannot read
# ---------------------------------------------------------------------------


def test_a_uint64_leading_record_is_refused():
    """The ARTIK051_KRAC_18K shape (#301): <uint64 date><uint32 value>, where
    the value is cumulative *runtime hours*, not energy. Reading it as this
    module's layout would put hours in a kWh sensor.

    Keying on the leading field alone does not catch it, which is the trap
    worth pinning: when the date is a plain Unix timestamp the low half is a
    perfectly plausible leading uint32 and the high half reads as a value of
    zero. It is the zero that gives it away."""
    blob = struct.pack("<QI", 1_690_156_800, 14545)  # 2023-07-24, 1454.5 h
    assert len(blob) == usagedb.RECORD_SIZE
    assert struct.unpack("<III", blob) == (1_690_156_800, 0, 14545)

    assert usagedb.records(_rep(blob)) is None
    assert usagedb.cumulative_energy_kwh(_rep(blob)) is None


def test_a_multi_record_uint64_leading_file_is_refused():
    """The same trap across a whole file: every high half is zero, so the
    counter reads as flat rather than merely small."""
    blob = b"".join(struct.pack("<QI", 1_690_156_800 + i * 86400, 14545 + i * 5) for i in range(10))
    assert usagedb.records(_rep(blob)) is None


def test_a_counter_that_never_moves_is_refused():
    """Nothing to publish, and indistinguishable from the misread above."""
    assert usagedb.records(_rep(_synthetic([(500, 0), (500, 0), (500, 0)]))) is None


def test_a_zero_counter_is_refused():
    assert usagedb.records(_rep(_synthetic([(0, 0)]))) is None


def test_sqlite_is_refused():
    """#301 reports genuine SQLite on some washers behind the same href."""
    assert usagedb.records(_rep(b"SQLite format 3\x00" + b"\x00" * 100)) is None


@pytest.mark.parametrize(
    "blob",
    [
        b"",
        b"\x00" * 11,  # shorter than one record
        b"\x00" * 18,  # not a whole number of records
    ],
    ids=["empty", "too-short", "ragged"],
)
def test_a_payload_that_is_not_whole_records_is_refused(blob):
    assert usagedb.records(_rep(blob)) is None


def test_a_backwards_counter_is_refused():
    """Cumulative means cumulative. A decrease is a misread field, and would
    make a total_increasing sensor report a phantom meter reset."""
    assert usagedb.records(_rep(_synthetic([(100, 0), (90, 0)]))) is None


def test_records_out_of_time_order_are_refused():
    blob = struct.pack("<III", 1_780_086_400, 10, 0) + struct.pack("<III", 1_780_000_000, 20, 0)
    assert usagedb.records(_rep(blob)) is None


def test_a_long_gap_between_records_is_kept():
    """One record per day the appliance actually *ran* -- #301 notes a
    25th-to-29th gap on an AC -- so a seasonally-used unit legitimately skips
    months. An earlier bound here threw the whole file away for that, taking
    the appliance's entities with it."""
    blob = struct.pack("<III", 1_780_000_000, 10, 0) + struct.pack("<III", 1_850_000_000, 20, 0)
    assert usagedb.cumulative_energy_kwh(_rep(blob)) == 2.0


def test_energy_is_refused_on_a_third_field_that_matches_no_known_family():
    """The scale gate is a positive identification, not "isn't runtime": a
    third field that is neither all-zero nor a month label could be either
    scale, and guessing costs a factor of 100."""
    blob = _synthetic([(100, 40), (120, 41), (140, 42)])
    assert usagedb.cumulative_energy_kwh(_rep(blob)) is None
    assert usagedb.cumulative_runtime_hours(_rep(blob)) is None


@pytest.mark.parametrize(
    "rep",
    [
        {},
        {usagedb.ITEMS_FIELD: []},
        {usagedb.ITEMS_FIELD: "not-a-list"},
        {usagedb.ITEMS_FIELD: [{"x.com.samsung.name": "/mnt/usage.db"}]},
        {usagedb.ITEMS_FIELD: [{usagedb.BLOB_FIELD: "not-bytes"}]},
    ],
    ids=["empty", "no-items", "items-not-a-list", "no-blob", "blob-not-bytes"],
)
def test_a_rep_without_a_usable_blob_is_refused(rep):
    assert usagedb.records(rep) is None
    assert usagedb.cumulative_energy_kwh(rep) is None


def test_a_repeated_reading_is_accepted():
    """22 of the washer's 281 records repeat the previous cumulative value
    (#301). Flat is not backwards."""
    parsed = usagedb.records(_rep(_synthetic([(100, 0), (100, 0), (110, 0)])))
    assert parsed is not None
    assert [r[1] for r in parsed] == [100, 100, 110]
