"""Decoders and entities for `/ehsfsv/vs/0`, the EHS heat pump's Field
Setting Values (FSV) -- installer-configurable settings that shape how the
unit behaves (water-law curves, DHW limits, heating/DHW priority, outlet
temperature limits, ...).

Resource shape (verified against a real TP1X_DA_AC_EHS_01001_0000 dump):

    {"items": [{"setting": "<26 hex chars>"}, ...]}

20 items on our fixture. Each `setting` decodes to a fixed 13-byte record:

    offset  field
    0-1     FSV code, uint16 BE -- its decimal rendering IS the Samsung FSV
            number an installer manual uses (0x0407 -> 1031 -> "FSV 1031")
    2       0x01 on all 20 records (unknown; possibly a present/enabled flag)
    3       scale divisor -- 1 or 10
    4       decimal places -- 0 or 1, redundant with byte 3
    5-6     minimum, int16 BE, signed, divided by scale
    7-8     maximum, int16 BE, signed, divided by scale
    9-10    current value, int16 BE, signed, divided by scale
    11-12   0x0101 on all 20 records (unknown; possibly writable flags)

VALIDATED: all 20 records in the fixture decode to real FSV codes whose
min/max/value agree with Samsung's published FSV reference. min/max/value
are SIGNED -- FSV #4012's minimum is bytes 0xFF6A = -150 -> -15.0, not some
large unsigned value; a naive unsigned read would be wrong for every FSV
whose range spans zero (#2011, #4012). Cross-checked independently below
against /temperatures/dhw/vs/0: FSV #1051's value equals that resource's
`maximum` and FSV #1052's value equals its `minimum` (both 62.0/40.0 on our
fixture) -- the strongest available evidence that bytes 9-10 really are the
value field and that this table's byte alignment is correct, since that
comparison is against an independently-read resource, not just "this looks
plausible".

Only a curated subset of the ~100 possible FSV codes is bound as sensors --
the ones that materially change how the heat pump behaves and that a user
would plausibly want on a dashboard or in an automation (water-law curves,
DHW limits, heating/DHW priority, zone/outlet limits). See FSV_SENSORS
below for the full list and rationale per group.

Different EHS models report different FSV subsets: our fixture's 20 records
do NOT include #2041, #3021 or #3023, all three of which are in the
curated table anyway (a different model may report them) -- `_fsv`'s
exists_fn is therefore mandatory, not optional polish, and is what keeps a
unit that doesn't report a code from getting a permanently-unavailable
entity.

The record's own min/max fields ARE decoded here (`_decode_setting`) so
tests can assert on them, but are NOT bound to any entity in this phase --
there is no read-only HA concept for "this sensor's own bounds". A future
phase that adds a write contract (NumberDesc/SelectDesc) must read its
native_min/native_max from these device-reported fields rather than a
hardcoded constant, exactly as ehs.ZONE_TARGET_TEMPERATURE already does for
`/temperatures/indoor/vs/0`'s minimum/maximum/increment fields.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from ..capability import Capability
from ..entities import SensorDesc


class FsvRecord(NamedTuple):
    code: int
    minimum: float
    maximum: float
    value: float


def _i16(b: bytes, offset: int) -> int:
    return int.from_bytes(b[offset : offset + 2], "big", signed=True)


def _decode_setting(setting) -> FsvRecord | None:
    """Decode one `items[]` entry's `setting` hex string into its FSV
    code/min/max/value. Defensive throughout -- a non-string, non-hex, or
    wrong-length `setting` returns None rather than raising."""
    if not isinstance(setting, str):
        return None
    try:
        b = bytes.fromhex(setting)
    except ValueError:
        return None
    if len(b) != 13:
        return None
    code = int.from_bytes(b[0:2], "big", signed=False)
    scale = b[3] or 1  # defensive: a 0 scale would divide-by-zero below
    return FsvRecord(
        code=code,
        minimum=_i16(b, 5) / scale,
        maximum=_i16(b, 7) / scale,
        value=_i16(b, 9) / scale,
    )


def _find_fsv(rep, code: int) -> FsvRecord | None:
    """Scan `items[]` for the record matching `code`. Defensive throughout:
    a missing/non-dict rep, a missing/non-list `items`, a non-dict entry,
    or a record that fails to decode are all skipped rather than raising."""
    if not isinstance(rep, dict):
        return None
    items = rep.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        record = _decode_setting(item.get("setting"))
        if record is not None and record.code == code:
            return record
    return None


# ---------------------------------------------------------------------------
# rep_fn/exists_fn plumbing: one factory per FSV code, mirroring ehs_cycle's
# _indoor/_outdoor factories.
# ---------------------------------------------------------------------------


def _fsv_value_fn(code: int):
    def rep_fn(rep):
        record = _find_fsv(rep, code)
        return None if record is None else record.value

    return rep_fn


def _fsv_exists_fn(code: int):
    def exists_fn(rep, resources):
        return _find_fsv(rep, code) is not None

    return exists_fn


def _fsv(code: int, key: str, *, temperature: bool = False, **kw) -> SensorDesc:
    """One FSV setting as a read-only diagnostic sensor.

    rep_fn scans `items[]` for `code` and returns that record's current
    value; exists_fn hides the entity on units that don't report `code` --
    mandatory here since different EHS models report different FSV subsets
    (see module docstring).

    `temperature=True` adds device_class='temperature' and a °C unit for a
    temperature-valued setting. This resource carries no unit field of its
    own, and unit_fn only ever receives this capability's own rep -- there
    is no cross-resource read available the way exists_fn's (rep,
    resources) signature would allow, so a live cross-check against e.g.
    /ehscycle/vs/0's own `unit` field (which does say "Celsius") isn't
    possible here. The rest of the EHS family reports Celsius throughout,
    so °C is the best available default; a Fahrenheit-configured unit is an
    open question this resource alone cannot answer.

    `temperature=False` (the default) leaves the entity unitless with no
    device_class -- for the enum-valued FSVs (#2041, #3011, #4011, #4021,
    #4061), whose numeric codes have real meanings but no confirmed label
    strings from Samsung, so a device_class='enum' with invented options
    would be a read-side unit/label guess (see adding-device-support skill
    §5). These ship as plain numeric sensors; promoting them to
    device_class='enum' with translated options is left to a future phase
    once real label strings are confirmed.

    entity_category is always 'diagnostic': every FSV sensor here is
    read-only in this phase. Promote to 'config' (and NumberDesc/SelectDesc)
    only once a write contract is confirmed on live hardware -- there is no
    dump evidence of the write shape yet.

    state_class is deliberately never set: these are installer
    configuration values that change only when someone reconfigures the
    unit, not a continuously-produced measurement that benefits from HA's
    statistics -- contrast ehs.ZONE_TEMPERATURE, an actual live reading,
    which does set state_class='measurement'.

    enabled_default=False: this adds up to 18 new diagnostic entities and
    they must not be dumped on every existing EHS user at upgrade.
    """
    extra: dict[str, Any] = {}
    if temperature:
        extra["device_class"] = "temperature"
        extra["unit"] = "°C"
    return SensorDesc(
        key=key,
        rep_fn=_fsv_value_fn(code),
        exists_fn=_fsv_exists_fn(code),
        entity_category="diagnostic",
        enabled_default=False,
        **extra,
        **kw,
    )


# ---------------------------------------------------------------------------
# Curated FSV table. Codes not present in our own fixture (#2041, #3021,
# #3023) are included anyway -- their exists_fn simply reports False on
# this unit until a dump from a model that sets them turns up.
# ---------------------------------------------------------------------------

FSV_SENSORS = (
    # --- Water law / weather compensation -----------------------------
    # The headline feature of this phase -- nothing else in HA surfaces
    # these locally. #2011/#2012 are the outdoor-temperature axis anchors
    # for the water-law curve; #2021/#2022 and #2031/#2032 are the two
    # curves' (WL1/WL2) target flow temperatures at those anchors.
    _fsv(2011, "fsv_outdoor_curve_cold_point", temperature=True),
    _fsv(2012, "fsv_outdoor_curve_warm_point", temperature=True),
    _fsv(2021, "fsv_water_law_1_cold_target", temperature=True),
    _fsv(2022, "fsv_water_law_1_warm_target", temperature=True),
    _fsv(2031, "fsv_water_law_2_cold_target", temperature=True),
    _fsv(2032, "fsv_water_law_2_warm_target", temperature=True),
    _fsv(2041, "fsv_water_law_selection"),  # enum, unitless -- not in our fixture
    # --- DHW ------------------------------------------------------------
    # #1051/#1052's values cross-check exactly against
    # /temperatures/dhw/vs/0's maximum/minimum (see module docstring) --
    # the strongest evidence available that these are the tank setpoint
    # limits, not some other pair of temperature-shaped FSVs.
    _fsv(1051, "fsv_dhw_temperature_maximum", temperature=True),
    _fsv(1052, "fsv_dhw_temperature_minimum", temperature=True),
    _fsv(3011, "fsv_dhw_application_mode"),  # enum, unitless
    _fsv(3021, "fsv_dhw_max_hp_temperature", temperature=True),  # not in our fixture
    _fsv(3023, "fsv_dhw_hp_on_hysteresis", temperature=True),  # not in our fixture
    # --- Heating priority -------------------------------------------------
    _fsv(4011, "fsv_heating_dhw_priority"),  # enum, unitless
    _fsv(4012, "fsv_heating_dhw_changeover_temperature", temperature=True),
    _fsv(4021, "fsv_backup_heater_application"),  # enum, unitless
    # --- Zone / outlet limits ----------------------------------------------
    _fsv(1031, "fsv_heating_outlet_temperature_maximum", temperature=True),
    _fsv(1032, "fsv_heating_outlet_temperature_minimum", temperature=True),
    _fsv(4061, "fsv_zone_control_application"),  # enum, unitless
)

EHS_FSV = Capability(
    href="/ehsfsv/vs/0",
    # Installer settings change almost never -- only when someone re-runs
    # commissioning -- unlike /ehscycle/vs/0's ~5-minute telemetry cadence
    # ('warm'). 'cold' matches how rarely this resource's values actually
    # move.
    poll_tier="cold",
    entities=FSV_SENSORS,
)
