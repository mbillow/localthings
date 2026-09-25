"""Capabilities shared across multiple appliance families.

Each capability is keyed on the stable OCF resource href (not rt), verified
against live device dumps:
  /kidslock/vs/0            -> x.com.samsung.da.kidsLock
  /remotectrl/vs/0          -> x.com.samsung.da.remoteControlEnabled
  /power/vs/0               -> x.com.samsung.da.power
  /alarms/vs/0              -> x.com.samsung.da.items
  /energy/consumption/vs/0  -> x.com.samsung.da.instantaneousPower / cumulativePower
  /water/consumption/vs/0   -> x.com.samsung.da.cumulativeWater
  /filter/waterfilter/vs/0  -> x.com.samsung.da.filterUsage / filterStatus
"""

from datetime import UTC, datetime

from .. import usagedb
from ..batch import is_stub_rep
from ..capability import Capability
from ..entities import (
    BinarySensorDesc,
    ButtonDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def clamp_power(v):
    n = _num(v)
    return 0.0 if (n is not None and n < 0) else n


def wh_to_kwh(v):
    n = _num(v)
    return round(n / 1000.0, 2) if n is not None else None


def lifetime_wh_to_kwh(v):
    """wh_to_kwh for an appliance's lifetime energy counter, where 0 reads as
    unknown: a TP2X_RAC_20K (issue #488) reports "0" for a minute or two
    after its connection drops, and HA books a total_increasing drop to 0 as
    a meter reset, counting the whole lifetime total again on recovery."""
    n = _num(v)
    return round(n / 1000.0, 2) if n else None


def parse_iso_utc(raw):
    """ISO datetime defaulting to UTC when the string carries no timezone of
    its own. A few boards ship a 'Z'/offset suffix already (fromisoformat
    parses that natively since Python 3.11) -- only fill in UTC when parsing
    left the result naive."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def epoch_to_utc(value):
    """Unix epoch seconds -> aware UTC datetime, for boards that report a
    bare epoch rather than the ISO string parse_iso_utc handles."""
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def filter_usage_percent(rep):
    """Filter usage as a percentage. `filterUsage` is already 0-100 on every
    family confirmed so far, including ARTIK051_PRAC (issue #330): its own
    fixture and three live heads all show `filterStatus == 'wash'` at
    `filterUsage == '100'` regardless of `filterCapacity` (60/224/500 across
    other families' fixtures), which only holds if `filterUsage` is already
    a percent -- dividing by capacity again would read that filter as fresh
    at 20%."""
    return int_or_none(rep.get("x.com.samsung.da.filterUsage"))


def filter_usage_hours(rep):
    """Elapsed filter hours, derived from the percentage and capacity rather
    than read off `filterUsage` directly -- `filterUsage` is a percent, not
    an hour count (issue #330). Returns None when capacity is missing/zero."""
    pct = filter_usage_percent(rep)
    cap = _num(rep.get("x.com.samsung.da.filterCapacity"))
    if pct is None or not cap:
        return None
    return round(pct / 100 * cap, 1)


def normalize_temp_unit(raw, default="°F"):
    """'C'/'Celsius' -> '°C', 'F'/'Fahrenheit' -> '°F'. Falls back to
    `default` for any other/missing value. Shared by fridge.py and oven.py,
    which both read a per-device unit off a `/temperature*` resource
    instead of assuming one (issue #7)."""
    raw = (raw or "").strip().upper()
    if raw.startswith("C"):
        return "°C"
    if raw.startswith("F"):
        return "°F"
    return default


def _ml_to_l(v):
    n = _num(v)
    return round(n / 1000.0, 1) if n is not None else None


def _active_alarm_codes(items):
    """Join active alarm codes; skip retained rows Samsung leaves as
    Deleted, and any code ending in '_OFF'.

    Laundry boards keep a Deleted ErrorCode row in /alarms/vs/0 after the
    condition clears. Samsung also pre-populates this array with one row
    per alarm *type* the board supports, each carrying its own
    '<Name>_OFF' placeholder when that alarm isn't firing -- confirmed
    across independent families. A firing alarm instead reports a plain,
    unsuffixed code (FilterAlarm, DoorA_Opened, ...); issue #166 shows both
    in one dump. Generalizes what range hood used to special-case as just
    the literal 'ErrorCode_OFF' string.
    """
    if not items or not isinstance(items, list):
        return "none"
    codes = [
        i.get("x.com.samsung.da.code")
        for i in items
        if i.get("x.com.samsung.da.code")
        and str(i.get("x.com.samsung.da.state", "")).lower() != "deleted"
        and not str(i.get("x.com.samsung.da.code", "")).lower().endswith("_off")
    ]
    return ", ".join(codes) if codes else "none"


def hex_pairs(codes):
    """'1C1D21...' -> ['1C', '1D', '21', ...]."""
    return [codes[i : i + 2] for i in range(0, len(codes) - 1, 2)]


def option_value(options, prefix):
    """Find `<prefix>_<value>` in an options[] array and return <value>.

    Lives here rather than in laundry.py, which is where it grew, because
    cloudcourse.py needs it too and laundry.py imports *that* -- so the
    reverse import would be a module cycle. The coordinator already imports
    from this module, so nothing about the dependency direction is unusual;
    it is specifically the laundry/cloudcourse pair that can't reach each
    other. Anchored at position 0 so 'Course_' never matches
    'CloudCourse_'/'OneTimeCloudCourse_'.
    """
    for o in options or []:
        if isinstance(o, str) and o.startswith(prefix + "_"):
            return o.split("_", 1)[1]
    return None


def merge_options_field(cached, new_tokens):
    """Merge freshly-written `<Prefix>_<Value>` tokens into a cached
    x.com.samsung.da.options[]-style array the same way the device itself
    merges them: match by prefix, replace if present, append if not.

    Confirmed on hardware (issue #54) that a write only needs to carry the
    changed token(s), not the whole array -- see laundry.option_write /
    oven._option_write for the write side. coordinator.async_send_command
    uses this read-side counterpart to keep the optimistic cache entry
    complete during the write-settle window."""
    merged = list(cached or [])
    for token in new_tokens or ():
        if not isinstance(token, str) or "_" not in token:
            continue
        prefix = token.split("_", 1)[0]
        replaced = False
        for i, o in enumerate(merged):
            if isinstance(o, str) and o.startswith(prefix + "_"):
                merged[i] = token
                replaced = True
        if not replaced:
            merged.append(token)
    return merged


def merge_items_field(cached, new_items):
    """Merge a partial x.com.samsung.da.items[]-style write (matched by
    x.com.samsung.da.id) into a cached items array -- the items[]
    counterpart of merge_options_field above.

    Confirmed on hardware that a write only needs to carry the item with
    the changed id plus the field(s) being changed (see
    airconditioner._climate_write's vendor temperature write). Fields
    within the matched item are merged, not replaced outright, so a
    setpoint-only write doesn't wipe current/minimum/maximum/unit from the
    optimistic cache entry. An id with no match in `cached` is appended."""
    merged = [dict(i) if isinstance(i, dict) else i for i in (cached or [])]
    for new_item in new_items or ():
        if not isinstance(new_item, dict):
            continue
        item_id = new_item.get("x.com.samsung.da.id")
        for i, existing in enumerate(merged):
            if isinstance(existing, dict) and existing.get("x.com.samsung.da.id") == item_id:
                merged[i] = {**existing, **new_item}
                break
        else:
            merged.append(new_item)
    return merged


# /wm/setinfo/vs/0 -- laundry-family firmware capability flags. Present on
# washers, dryers, and dishwashers; absent on fridge/oven/AC. Static for
# the life of a board, so reading it from the /device/0 seed is enough.
_SETINFO_HREF = "/wm/setinfo/vs/0"
_POWER_ON_OFF_FIELD = "x.com.samsung.da.isModelSettingPowerOnOff"
_WITHOUT_SC_FIELD = "x.com.samsung.da.isModelSettingWithoutSC"


def model_allows_power_on_off(resources: dict) -> bool:
    """True unless firmware explicitly declares remote power on/off
    unsupported. isModelSettingPowerOnOff is "false" on many laundry
    boards: /power/0 and /power/vs/0 still report state, but CoAP writes
    are ignored. Absent setinfo (non-laundry families) keeps the writable
    switch."""
    setinfo = resources.get(_SETINFO_HREF)
    if setinfo is None:
        return True
    flag = setinfo.get(_POWER_ON_OFF_FIELD)
    if flag is None:
        return True
    return str(flag).lower() != "false"


def model_setting_without_sc(resources: dict) -> bool:
    """True when firmware declares settings writable without Smart
    Control. isModelSettingWithoutSC is "true" on washers/dryers that
    accept temperature/spin/cycle-option writes while remote control is
    off; cycle start/pause/stop still need Smart Control on those
    boards."""
    setinfo = resources.get(_SETINFO_HREF) or {}
    return str(setinfo.get(_WITHOUT_SC_FIELD, "")).lower() == "true"


def _power_switch_exists(rep, resources):
    return model_allows_power_on_off(resources)


def _power_sensor_exists(rep, resources):
    return not model_allows_power_on_off(resources)


def diagnosis_status(value):
    """'Ready' -> the catalog's 'ready'; anything else is left raw.

    Shared by dishwasher and dryer, which report the same field.
    """
    return "ready" if value == "Ready" else value


def sensor_item_value(items, sensor_type, index=0):
    """Pull one reading out of a `/sensors/vs/0`-style items[] list -- each
    item is `{type, value: [...]}`; `index` picks which slot to read
    (index 0 is the raw measurement on every family seen so far). Shared
    by range_hood.AIR_QUALITY, air_purifier.AIR_QUALITY, and
    air_monitor.SENSORS, which all read the same resource shape.

    value[] is 2-element on the fields that carry a magnitude
    (Dust/FineDust/SuperFineDust/CO2) and 1-element on Odor/CleanLevel.
    That asymmetry is what index 1 means: it is the device's own graded
    air-quality level for that reading -- the same kind of value Odor and
    CleanLevel already *are*, which is why those two have no second slot.
    It reads 0-2 against index 0's observed 0-31, tracks index 0 within a
    device, and CleanLevel equals the highest per-field grade on 9 of the
    11 fixtures reporting this resource (the range hood and one RAC report
    a higher CleanLevel than any dust grade, so they fold in something
    else).

    Index 1 is deliberately left unbound rather than exposed as an entity:
    its floor is not portable. ARTIK051_TVTL grades good air as 0, while
    AVT-WW-TP1 / A-VTWW-TP2 / TP1X / ASM-KR-TP1 / AHD-WW-TP1 all grade it
    as 1, so a shared descriptor would need a per-family offset to mean
    anything, and CleanLevel already carries the aggregate. The grade is
    still load-bearing as *evidence*: it is what confirms the three dust
    fields are three different scales rather than one repeated reading --
    see air_purifier._AIR_QUALITY_SENSORS and
    tests/test_air_quality_grade_column.py.
    """
    for item in items or ():
        if not isinstance(item, dict):
            continue
        if item.get("x.com.samsung.da.type") != sensor_type:
            continue
        values = item.get("x.com.samsung.da.value") or []
        if index < len(values):
            try:
                return int(values[index])
            except (TypeError, ValueError):
                return None
    return None


def has_sensor_type(type_):
    """True when /sensors/vs/0's items[] lists an item of this type.

    This only proves the type is *listed*, not that the reading is real:
    issue #166 (ARTIK051_PRAC_20K) lists all five types with permanent-zero
    values on units the reporter confirmed don't have the hardware. So
    entities gated on this stay disabled by default rather than
    existence-gated further, to avoid silently dropping real readings on
    hardware not yet seen.

    is_stub_rep(rep) keeps the stub carve-out (see entity._is_included /
    ENERGY_METER, issue #127): an explicit exists_fn otherwise bypasses it
    and would drop the entity when /device/0 returns a not-yet-fetched stub.
    """

    def fn(rep, resources):
        return is_stub_rep(rep) or any(
            isinstance(i, dict) and i.get("x.com.samsung.da.type") == type_
            for i in (rep.get("x.com.samsung.da.items") or [])
        )

    return fn


# OCF-native / vendor '-vs' fallback pairs for power, kids-lock, remote
# control: each exists as both a standard OCF resource (/power/0,
# oic.r.switch.binary, plain boolean 'value') and a Samsung vendor
# resource (/power/vs/0, x.com.samsung.da.power), since Samsung advertises
# both while its firmware migrates onto the OCF standard model. Prefer the
# OCF-standard href when present; the '-vs' href binds only when it's
# absent, via match_fn. Older firmware has only the '-vs' resource. See
# the adding-device-support skill's "OCF-standard vs vendor" section.
# Every device registry lists both caps of each pair.

POWER_GENERIC = Capability(
    href="/power/0",
    poll_tier="warm",
    entities=(
        # Writable when firmware allows remote power; otherwise a read-only
        # binary_sensor with the same key keeps HA state without a dead switch.
        SwitchDesc(
            key="power_switch",
            field="value",
            value_fn=lambda v: bool(v),
            exists_fn=_power_switch_exists,
            write_fn=lambda p, rep, href=None: (["power", "0"], {"value": p == "On"}),
        ),
        BinarySensorDesc(
            key="power_switch",
            field="value",
            device_class="power",
            value_fn=lambda v: bool(v),
            exists_fn=_power_sensor_exists,
        ),
    ),
)

POWER_VS_FALLBACK = Capability(
    href="/power/vs/0",
    match_fn=lambda rep, resources: "/power/0" not in resources,
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="power_switch",
            field="x.com.samsung.da.power",
            value_fn=lambda v: v == "On",
            exists_fn=_power_switch_exists,
            write_fn=lambda p, rep, href=None: (
                ["power", "vs", "0"],
                {"x.com.samsung.da.power": "On" if p == "On" else "Off"},
            ),
        ),
        BinarySensorDesc(
            key="power_switch",
            field="x.com.samsung.da.power",
            device_class="power",
            value_fn=lambda v: v == "On",
            exists_fn=_power_sensor_exists,
        ),
    ),
)

KIDS_LOCK_GENERIC = Capability(
    href="/kidslock/0",
    entities=(
        # Read-only like KIDS_LOCK_VS_FALLBACK (issues #181/#183): HA's
        # switch platform never honored SwitchDesc's device_class='lock'
        # ('outlet'/'switch' only), leaving a plain switch whose 'On' meant
        # different things on different boards. As a BinarySensorDesc with
        # device_class='lock', both surfaces read with the same polarity
        # ('On' = open/unlocked, per HA convention); value_fn here inverts
        # the wire value to match (value=False on /kidslock/0 means kids
        # lock is NOT active).
        BinarySensorDesc(
            key="child_lock", field="value", device_class="lock", value_fn=lambda v: not bool(v)
        ),
    ),
)

KIDS_LOCK_VS_FALLBACK = Capability(
    href="/kidslock/vs/0",
    match_fn=lambda rep, resources: "/kidslock/0" not in resources,
    entities=(
        # Read-only, not a SwitchDesc (issues #181/#183): the old write
        # side wrote 'Enable', a value no dump ever reports back (every one
        # is 'Ready' or 'Run'), and #181's reporter confirmed writing the
        # correct value ('Run') still 4.05s -- genuinely read-only on this
        # hardware. Polarity matches KIDS_LOCK_GENERIC ('On' = unlocked).
        BinarySensorDesc(
            key="child_lock",
            field="x.com.samsung.da.kidsLock",
            device_class="lock",
            value_fn=lambda v: v == "Ready",
        ),
    ),
)


def remote_control_enabled(resources: dict) -> bool:
    """Single source of truth for the /remotectrl on/off signal, mirroring
    REMOTE_CONTROL_GENERIC/_VS_FALLBACK's href/field precedence. Used both
    to render the read-only Smart Control binary_sensor and, from
    coordinator.async_send_command, to block writes when remote control is
    off. True (assume enabled) when neither href is present -- most device
    types don't report this capability at all."""
    generic = resources.get("/remotectrl/0")
    if generic is not None:
        return bool(generic.get("value"))
    fallback = resources.get("/remotectrl/vs/0")
    if fallback is not None:
        return str(fallback.get("x.com.samsung.da.remoteControlEnabled")).lower() == "true"
    return True


def remote_control_required_for_write(resources: dict, bound_href: str) -> bool:
    """Whether a write to bound_href should be gated on Smart Control.

    When isModelSettingWithoutSC is true, laundry firmware accepts settings
    writes (wash temp, spin, course options, buzzer, ...) with remote
    control off, but cycle start/pause/stop on /operational/state still
    need Smart Control. Absent that flag, keep the historical blanket gate.
    """
    if not model_setting_without_sc(resources):
        return True
    href = bound_href or ""
    return href.startswith("/operational/state")


REMOTE_CONTROL_GENERIC = Capability(
    href="/remotectrl/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="remote_control",
            field="value",
            device_class="connectivity",
            value_fn=lambda v: bool(v),
        ),
    ),
)

REMOTE_CONTROL_VS_FALLBACK = Capability(
    href="/remotectrl/vs/0",
    match_fn=lambda rep, resources: "/remotectrl/0" not in resources,
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="remote_control",
            field="x.com.samsung.da.remoteControlEnabled",
            device_class="connectivity",
            value_fn=lambda v: str(v).lower() == "true",
        ),
    ),
)

ALARMS = Capability(
    href="/alarms/vs/0",
    poll_tier="hot",
    entities=(
        SensorDesc(
            key="alarm_code",
            field="x.com.samsung.da.items",
            icon="mdi:alert",
            entity_category="diagnostic",
            value_fn=_active_alarm_codes,
        ),
    ),
)

# instantaneousPower is a dead field on DA_WM_-class laundry dumps and
# dishwashers: the literal sentinel '-500', unchanged across off/idle/
# running. clamp_power would floor it to a misleading "0 W". Gate
# power_watts out when the sentinel is seen, but only then, so a device
# reporting a real value (e.g. a fridge's 93 W) still shows it (issue #6).
_DEAD_INSTANTANEOUS_POWER = "-500"

HREF_ENERGY_CONSUMPTION = "/energy/consumption/vs/0"

ENERGY_METER = Capability(
    href=HREF_ENERGY_CONSUMPTION,
    entities=(
        # is_stub_rep(rep) keeps the stub carve-out (see
        # entity._is_included): an explicit exists_fn otherwise bypasses
        # it and would drop the entity when /device/0 returns a
        # not-yet-fetched stub. A genuinely empty {} rep is NOT a stub, so
        # it still falls through to the normal field/sentinel checks.
        SensorDesc(
            key="power_watts",
            field="x.com.samsung.da.instantaneousPower",
            device_class="power",
            state_class="measurement",
            unit="W",
            value_fn=clamp_power,
            exists_fn=lambda rep, resources: (
                is_stub_rep(rep)
                or (
                    rep.get("x.com.samsung.da.instantaneousPower")
                    not in (None, _DEAD_INSTANTANEOUS_POWER)
                )
            ),
        ),
        SensorDesc(
            key="energy_kwh",
            field="x.com.samsung.da.cumulativePower",
            device_class="energy",
            state_class="total_increasing",
            unit="kWh",
            value_fn=lifetime_wh_to_kwh,
            exists_fn=lambda rep, resources: (
                is_stub_rep(rep) or "x.com.samsung.da.cumulativePower" in rep
            ),
        ),
        # cumulativeConsumption is a second, independently-varying running
        # total some fridges (issue #26) report alongside cumulativePower.
        SensorDesc(
            key="power_energy_kwh",
            field="x.com.samsung.da.cumulativeConsumption",
            device_class="energy",
            state_class="total_increasing",
            unit="kWh",
            value_fn=wh_to_kwh,
            exists_fn=lambda rep, resources: (
                is_stub_rep(rep) or "x.com.samsung.da.cumulativeConsumption" in rep
            ),
        ),
        # AI Energy Mode's lifetime savings estimate vs. an unoptimized
        # baseline -- present on some models (issue #21/#27), absent on
        # others (issue #20/#26).
        SensorDesc(
            key="energy_saved_kwh",
            field="x.com.samsung.da.cumulativeSavedPower",
            device_class="energy",
            state_class="total_increasing",
            unit="kWh",
            value_fn=wh_to_kwh,
            exists_fn=lambda rep, resources: (
                is_stub_rep(rep) or "x.com.samsung.da.cumulativeSavedPower" in rep
            ),
        ),
        # Monthly billing-cycle totals -- completed prior month and
        # in-progress current month. Not ever-increasing, so no state_class.
        SensorDesc(
            key="energy_last_month_kwh",
            field="x.com.samsung.da.monthlyConsumption",
            device_class="energy",
            unit="kWh",
            value_fn=wh_to_kwh,
            exists_fn=lambda rep, resources: (
                is_stub_rep(rep) or "x.com.samsung.da.monthlyConsumption" in rep
            ),
        ),
        SensorDesc(
            key="energy_this_month_kwh",
            field="x.com.samsung.da.thismonthlyConsumption",
            device_class="energy",
            unit="kWh",
            value_fn=wh_to_kwh,
            exists_fn=lambda rep, resources: (
                is_stub_rep(rep) or "x.com.samsung.da.thismonthlyConsumption" in rep
            ),
        ),
    ),
)

WATER_METER = Capability(
    href="/water/consumption/vs/0",
    entities=(
        SensorDesc(
            key="water_liters",
            field="x.com.samsung.da.cumulativeWater",
            device_class="water",
            state_class="total_increasing",
            unit="L",
            icon="mdi:water",
            value_fn=_ml_to_l,
        ),
    ),
)

_RESETTABLE = {"replaceable", "washable"}


def _filter_reset_supported(rep, _resources):
    """Whether this filter resource claims a reset that exists.

    filterResetType names the reset kinds the device supports, and the corpus
    also carries ['notresetable'] -- a bare presence check reads that as a
    yes. is_stub_rep keeps the stub carve-out an explicit exists_fn would
    otherwise bypass (same reasoning as ENERGY_METER above).
    """
    return is_stub_rep(rep) or bool(
        _RESETTABLE & set(rep.get("x.com.samsung.da.filterResetType") or ())
    )


def filter_reset_button(key: str, href: str) -> ButtonDesc:
    """Reset button for an `x.com.samsung.da.filter.*` resource.

    `filterReset` set to the string "On" (case-sensitive; 'on'/'ON' fault
    5.00) resets the usage counter. Confirmed on two unrelated families --
    a TP1X_REF_21K's water filter and a TP1X_DA_AC_RAC_01001's air filter
    (#449) -- which is what makes it a property of the resource type rather
    than of one board. The field is a trigger no board reports back, so it
    can't be gated on itself.

    See docs/investigations/filter-reset.md, including why the hepa, deodor
    and hood resources here are extrapolation rather than measurement.
    """
    segs = [s for s in href.strip("/").split("/") if s]

    def write(payload, _rep, _href=None):
        # Three parameters exactly, with segs closed over rather than bound as
        # a fourth: the coordinator tries write_fn(payload, rep, href,
        # resources) first, so a fourth parameter catches the resources
        # snapshot instead (#461 -- the button posted to every href at once).
        return list(segs), {"x.com.samsung.da.filterReset": payload}

    return ButtonDesc(
        key=key,
        field="",
        payload="On",
        icon="mdi:restart",
        entity_category="config",
        exists_fn=_filter_reset_supported,
        write_fn=write,
    )


WATER_FILTER = Capability(
    href="/filter/waterfilter/vs/0",
    match_fn=lambda rep, _: rep.get("x.com.samsung.da.filterStatus", "").lower() != "notused",
    entities=(
        SensorDesc(
            key="filter_usage",
            field="x.com.samsung.da.filterUsage",
            unit="%",
            state_class="measurement",
            icon="mdi:filter",
        ),
        SensorDesc(
            key="filter_status",
            field="x.com.samsung.da.filterStatus",
            icon="mdi:filter-check",
            device_class="enum",
            options=("normal", "wash", "replace"),
            value_fn=lambda value: value.lower() if isinstance(value, str) else value,
        ),
        filter_reset_button("filter_reset", "/filter/waterfilter/vs/0"),
    ),
)

# A separate blockage notice some filter appliances report alongside the
# usage counter (issue #475's AMF microfiber-filter unit). filterStatusNotice
# is one of supportedFilterStatusNotice ("Normal"/"Blockage"); the fields are
# bare, not `x.com.samsung.da.`-prefixed. Modeled as a problem binary_sensor
# rather than folded into WATER_FILTER's filter_status enum -- it is a
# distinct resource on its own href with its own vocabulary, and a device can
# report either without the other.
FILTER_STATUS = Capability(
    href="/filterstatus/vs/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="filter_blockage",
            field="filterStatusNotice",
            device_class="problem",
            entity_category="diagnostic",
            icon="mdi:filter-remove",
            # Case-insensitive, matching WATER_FILTER.filter_status's own
            # .lower() -- a firmware casing variant ("blockage"/"BLOCKAGE")
            # must not silently read as no-problem.
            value_fn=lambda v: v.lower() == "blockage" if isinstance(v, str) else None,
        ),
    ),
)

# AI energy-saving level -- '0' is off, supportedAiLevel lists the
# additional level(s) offered ('1' meaning just "on" on most hardware,
# multi-level on some). Verified cross-family: fridge (issue #21) and
# washer (issue #40). Most hardware's supportedAiLevel is a single-entry
# list, so a select there would offer only one real choice against an
# implicit "off" -- shown as a switch instead; '0' is never in
# supportedAiLevel but is the observed off value, so the select
# synthesizes it back in as an explicit option. No translation_key:
# aiLevel's values are plain digit strings, and select.py already renders
# an untranslated numeric string as-is.


def _ai_energy_supported_levels(rep):
    """supportedAiLevel as a list -- a stray scalar must not be
    len()-checked as if it were one."""
    sl = rep.get("supportedAiLevel")
    return list(sl) if isinstance(sl, (list, tuple)) else []


def _ai_energy_level_options(resources):
    rep = resources.get("/energy/ailevel/vs/0") or {}
    return ["0", *_ai_energy_supported_levels(rep)]


def _ai_energy_level_write(p, rep, href=None):
    return ["energy", "ailevel", "vs", "0"], {"aiLevel": p}


def _ai_energy_level_switch_write(p, rep, href=None):
    levels = _ai_energy_supported_levels(rep)
    on_level = levels[0] if levels else "1"
    return ["energy", "ailevel", "vs", "0"], {"aiLevel": on_level if p == "On" else "0"}


AI_ENERGY_LEVEL = Capability(
    href="/energy/ailevel/vs/0",
    poll_tier="cold",
    entities=(
        # No is_stub_rep carve-out on either side, unlike most exists_fn
        # gates in this file: entity creation runs once against whichever
        # snapshot is current at platform setup, while flatten() re-checks
        # exists_fn every poll against live data. Both descriptors share
        # key='ai_energy_level' -- a stub carve-out could let one win at
        # setup and the other win once real data lands, feeding the
        # instantiated entity a value shaped for the other platform.
        # Requiring populated data on both sides keeps the two decisions in
        # permanent agreement, at the cost of the entity not appearing
        # until a reload if the first poll stubs this cold-tier href.
        SwitchDesc(
            key="ai_energy_level",
            field="aiLevel",
            icon="mdi:leaf",
            entity_category="config",
            value_fn=lambda v: v != "0",
            exists_fn=lambda rep, resources: len(_ai_energy_supported_levels(rep)) == 1,
            write_fn=_ai_energy_level_switch_write,
        ),
        SelectDesc(
            key="ai_energy_level",
            field="aiLevel",
            icon="mdi:leaf",
            entity_category="config",
            options=_ai_energy_level_options,
            exists_fn=lambda rep, resources: len(_ai_energy_supported_levels(rep)) > 1,
            write_fn=_ai_energy_level_write,
        ),
    ),
)

FIRMWARE_UPDATE = Capability(
    href="/otninformation/vs/0",
    poll_tier="cold",
    entities=(
        BinarySensorDesc(
            key="firmware_update",
            field="x.com.samsung.da.newVersionAvailable",
            device_class="update",
            entity_category="diagnostic",
            value_fn=lambda v: str(v).lower() == "true" if v is not None else None,
        ),
    ),
)

SELF_CHECK = Capability(
    href="/selfcheck/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="selfcheck_status",
            field="x.com.samsung.da.status",
            icon="mdi:stethoscope",
            entity_category="diagnostic",
        ),
        SensorDesc(
            key="selfcheck_result",
            field="x.com.samsung.da.result",
            icon="mdi:clipboard-check-outline",
            entity_category="diagnostic",
        ),
        # List of error codes from the last self-check; joined for display.
        # Not every fridge reports the field, hence the exists_fn.
        SensorDesc(
            key="selfcheck_error",
            field="x.com.samsung.da.error",
            icon="mdi:alert-circle-outline",
            entity_category="diagnostic",
            exists_fn=lambda rep, resources: is_stub_rep(rep) or "x.com.samsung.da.error" in rep,
            value_fn=lambda v: (", ".join(v) if v else None) if isinstance(v, list) else v,
        ),
        ButtonDesc(
            key="selfcheck_start",
            field="",
            payload="Start",
            icon="mdi:play-circle-outline",
            entity_category="diagnostic",
            write_fn=lambda p, rep, href=None: (
                ["selfcheck", "vs", "0"],
                {"x.com.samsung.da.status": p},
            ),
        ),
    ),
)

# Cross-family bundles, unpacked into every by_type registry's _build([...])
# call the same way ignored.IGNORED is. discover() only binds a capability
# whose href is actually present in a given device's dump, so listing one
# here for a family that doesn't expose the href is a no-op, not a phantom
# entity -- see the adding-device-support skill's coverage-discipline
# section.
#
# UNIVERSAL holds every capability with no known family that both has the
# href and needs to model it some other way.
#
# POWER is kept separate: airconditioner opts out of it entirely, since
# its climate entity already owns /power/0 and /power/vs/0 via bare
# no-entity Capability objects (airconditioner.COVERAGE), and a second
# real cap on the same href would make _build() raise (see
# by_type/airconditioner.py). Kids-lock/remote-control have no such
# conflict, so they stay in UNIVERSAL.
#
# Airconditioner also partially opts out of UNIVERSAL itself: issue #193
# needs ENERGY_METER's cumulativePower scale to differ by board
# generation, so by_type/airconditioner.py excludes just that one member
# and substitutes its own ENERGY_METER_GENERIC/ENERGY_METER_LEGACY.

# The appliance's own usage history, and the list that names the files it
# keeps. Both sit outside the /device/0 batch on every dump on record, so
# poll_tier='probe' is what makes them readable at all -- see
# registry.PROBE_HREFS and issue #301.
#
# No entities yet, deliberately. The record layout is settled
# (<uint32 local timestamp><uint32 cumulative, tenths of a kWh><uint32 ...>)
# and field 2 is confirmed against the live meter on four families, but the
# third field means something different on each one -- zero on a washer, the
# firmware's monthly bucket on a fridge, cumulative runtime on an
# ARTIK051_PRAC_20K. What the file is worth per family is a census question,
# and registering these coverage-only is what collects that census: the
# probed reps reach diagnostics without surfacing as coverage gaps.
FILE_LIST = Capability(href="/file/list/vs/0", poll_tier="probe")


def _meter_reports_cumulative_power(resources) -> bool:
    """True when /energy/consumption/vs/0 is the better source for energy.

    A stub rep counts as yes: it means "not fetched yet", and
    ENERGY_METER.energy_kwh includes itself on that basis, so treating it as
    a no here would create both entities on the same key.
    """
    rep = resources.get(HREF_ENERGY_CONSUMPTION)
    if rep is None:
        return False
    if is_stub_rep(rep):
        return True
    return "x.com.samsung.da.cumulativePower" in rep


def _usage_energy_exists(rep, resources) -> bool:
    """Bind the file's energy only where the meter resource cannot supply it
    and the blob actually decodes -- an unreadable payload must produce no
    entity rather than a permanently-unknown one."""
    if _meter_reports_cumulative_power(resources):
        return False
    return usagedb.cumulative_energy_kwh(rep) is not None


FILE_TRANSFER = Capability(
    href="/file/transfer/vs/0",
    poll_tier="probe",
    entities=(
        # Deliberately the same key as ENERGY_METER's, and mutually exclusive
        # with it: the two exists_fn are exact complements, so an appliance
        # gets one `energy_kwh` from whichever source can supply it and the
        # entity_id does not depend on which. Same shape as the
        # POWER_GENERIC/POWER_VS_FALLBACK pair above.
        #
        # A fallback rather than a second opinion. Where both exist the file
        # is pure duplication -- measured on a dishwasher, whose single
        # record matched cumulativePower and cumulativeDate exactly -- and
        # two total_increasing energy sensors for one physical meter is the
        # double-count trap issue #329 warns about. Where the meter reports
        # nothing, though, this is the only copy of the number: issue #285's
        # washer lost `cumulativePower` from its rep entirely and kept
        # recording to the file.
        #
        # Steps once a day, because the file is a daily rollup rather than a
        # live counter. Lumpy in the energy dashboard, correct in total, and
        # better than the nothing these appliances report today.
        SensorDesc(
            key="energy_kwh",
            device_class="energy",
            state_class="total_increasing",
            unit="kWh",
            rep_fn=usagedb.cumulative_energy_kwh,
            exists_fn=_usage_energy_exists,
        ),
        # Runtime, unlike energy, is not a fallback: no other resource on
        # these boards reports it, so there is nothing to defer to. Issue
        # #329 is what makes it worth having -- on a multi-head system this
        # is the one number that is genuinely per-unit, differing per head
        # while the energy counter beside it is the shared outdoor unit's.
        #
        # No state_class change needed for the daily-rollup lumpiness that
        # applies to energy_kwh above: hours are not summed into a dashboard.
        SensorDesc(
            key="usage_runtime_hours",
            device_class="duration",
            state_class="total_increasing",
            unit="h",
            icon="mdi:timer-outline",
            entity_category="diagnostic",
            rep_fn=usagedb.cumulative_runtime_hours,
            exists_fn=lambda rep, resources: usagedb.cumulative_runtime_hours(rep) is not None,
        ),
    ),
)


UNIVERSAL = (
    ALARMS,
    ENERGY_METER,
    FIRMWARE_UPDATE,
    SELF_CHECK,
    AI_ENERGY_LEVEL,
    KIDS_LOCK_GENERIC,
    KIDS_LOCK_VS_FALLBACK,
    REMOTE_CONTROL_GENERIC,
    REMOTE_CONTROL_VS_FALLBACK,
    FILE_LIST,
    FILE_TRANSFER,
)

POWER = (
    POWER_GENERIC,
    POWER_VS_FALLBACK,
)
