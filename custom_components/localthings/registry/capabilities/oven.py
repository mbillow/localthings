"""Capabilities for the oven family (Samsung NV7000BS-class).

Resources verified against the live device via DTLS-CoAP. See
`local-tools/comparisons/oven-tree.md` for the full field reference.

Proven write: lamp, via /mode/vs/0 options RMW, works even with Remote
Control off. Unproven (first HA use is also the test): sound/fastPreheat/
naturalSteam (same RMW pattern), setpoint via /temperatures/vs/0 items RMW,
cook time via /operational/state/vs/0's operationTime/remainingTime, mode
select via /mode/vs/0.modes (mid-cook acceptance unknown), stop via
state='Ready'.

Cycle start is not implemented: local-OCF cycle start isn't reproducible on
this firmware -- see docs/investigations/oven-cycle-start.md for what three
boards measured and what is worth probing next. Mode writes are also
unreliable -- the oven rolls them back once a cycle is active, so
OVEN_MODE's SelectDesc is effectively read-only in practice.
"""

from datetime import UTC, datetime, timedelta

from ..batch import is_stub_rep
from ..capability import Capability
from ..entities import (
    BinarySensorDesc,
    NumberDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
)
from .common import normalize_temp_unit
from .operational import STOP_BUTTON

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SETPOINT_MIN_C = 30
SETPOINT_MAX_C = 270
SETPOINT_STEP_C = 5

# Verified against issue #44's range dump: Bake mode's modeSpec reports
# tempMinF/tempMaxF/tempIntervalF = 175/550/5. Kept separate rather than
# converted from the Celsius bounds above, which are themselves unverified.
SETPOINT_MIN_F = 175
SETPOINT_MAX_F = 550
SETPOINT_STEP_F = 5

# Mode options seen on NV7000BS-class. No dump exists so this list is
# inferred from Samsung documentation and firmware observations; the
# firmware rejects unknown modes, so a missing entry is a coverage gap, not
# a bug. Fallback only, used when a device's own /mode/vs/0 doesn't report
# supportedModes at all -- see _oven_mode_options/_oven_mode_write below.
_OVEN_MODES = (
    "NoOperation",
    "Bake",
    "Broil",
    "Convection",
    "ConvectionBake",
    "ConvectionBroil",
    "FrozenPizzaPlus",
    "SlowCook",
    "PlateWarm",
    "AirFry",
)

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


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _finish_time(rep):
    """now() + remainingTime while a cook is running, rounded to the minute.

    Same treatment as operational.py's _finish_time, for the same reason:
    a range pushes /operational/state/vs/0 every few seconds during a cook
    (progressPercentage ticks about once per 7-10 s), and its remainingTime
    only has minute resolution with the seconds parked at ':59', so the
    unrounded sum drifted by a few seconds per push and crossed the minute
    boundary back and forth. That was one logbook entry per push (NX60T8311SS,
    a 15-minute bake). The state gate covers boards that leave a stale
    remainingTime after the cook ends; this one reports 00:00:00 there."""
    if _to_ocf(rep.get("x.com.samsung.da.state")) != "active":
        return None
    remaining = rep.get("x.com.samsung.da.remainingTime")
    if not remaining:
        return None
    try:
        h, m, s = remaining.split(":")
        total_s = int(h) * 3600 + int(m) * 60 + int(s)
    except (AttributeError, ValueError):
        return None
    if total_s == 0:
        return None
    finish = datetime.now(UTC) + timedelta(seconds=total_s)
    return finish.replace(second=0, microsecond=0)


def _op_minutes(op_time):
    """Parse 'H:MM:SS' operationTime into integer minutes."""
    if not op_time:
        return None
    try:
        h, m, s = op_time.split(":")
        return int(h) * 60 + int(m) + (1 if int(s) > 0 else 0)
    except Exception:
        return None


def _setpoint(desired):
    """An idle oven reports desired='0' (NX60T8311SS/AA, #444), which HA
    renders as -18 °C on a metric install once the unit is Fahrenheit. 0 is
    below every SETPOINT_MIN_* bound, so it gets the same 0-means-unset
    treatment as _finish_time above."""
    v = _int(desired)
    return None if v == 0 else v


# Idle cavity value per unit: ranges floor `current` at Bake's tempMin (175 F
# on every Fahrenheit range fixture, 80 C on the Celsius one), wall ovens
# report 0. Neither is a measurement.
_IDLE_FLOOR = {"°F": 175, "°C": 80}


def _current_temp(rep):
    """None while the oven is idle and `current` sits at or below the idle
    floor. Both conditions matter: a cool-down after a cook (desired=0,
    current above the floor) still reads, and a KeepWarm cook at exactly
    175 F (desired=175) does too."""
    items = rep.get("x.com.samsung.da.items") or []
    if not items:
        return None
    current = _int(items[0].get("x.com.samsung.da.current"))
    if current is None:
        return None
    idle = _setpoint(items[0].get("x.com.samsung.da.desired")) is None
    if idle and current <= _IDLE_FLOOR.get(_oven_temp_unit(rep), 0):
        return None
    return current


# ---------------------------------------------------------------------------
# Options-array helpers (shared by lamp, sound, fastpreheat, naturalsteam)
# ---------------------------------------------------------------------------


def _option_value(options, prefix):
    """Find `<prefix>_<value>` in an options array and return <value>."""
    for o in options or []:
        if isinstance(o, str) and o.startswith(prefix + "_"):
            return o.split("_", 1)[1]
    return None


def _has_option(prefix):
    """exists_fn for an options-array switch: bind only when the device's
    own options[] actually carries a `<prefix>_<value>` token.

    fast_preheat/natural_steam were shipped unconditionally (no exists_fn)
    as an unverified guess -- issue #183's dump reports neither token in
    its options[] at all, so both switches were phantom controls that
    "don't appear to do anything."

    `is_stub_rep(rep) or` keeps the same stub carve-out as cooktop.py's
    identical exists_fn: a stub /device/0 seed rep has no options[] at all,
    and without this a genuinely-present token would never get a first
    chance to bind.
    """
    return lambda rep, resources: (
        is_stub_rep(rep) or _option_value(rep.get("x.com.samsung.da.options"), prefix) is not None
    )


def _option_write(prefix, new_value):
    """A one-token x.com.samsung.da.options write, mirroring
    laundry.option_write. NOT independently confirmed on an oven -- issue
    #54 only confirmed prefix-merge-on-write for a washer's /course/vs/0;
    this extrapolates the same contract here. If some oven replaces the
    field outright instead of merging, this would drop every other option
    on the next write -- revisit if a real device report surfaces that."""
    return [f"{prefix}_{new_value}"]


# ---------------------------------------------------------------------------
# Write functions
# ---------------------------------------------------------------------------


def _oven_setpoint_write(p, rep, href=None):
    """RMW write to /temperatures/vs/0 items array."""
    try:
        temp = float(p)
    except (TypeError, ValueError):
        return None
    min_v, max_v, step_v = _setpoint_bounds(rep)
    temp_i = int(round(temp / step_v) * step_v)
    if not (min_v <= temp_i <= max_v):
        return None
    items = rep.get("x.com.samsung.da.items")
    if not items:
        return None
    items = [dict(it) for it in items]
    items[0]["x.com.samsung.da.desired"] = str(temp_i)
    return ["temperatures", "vs", "0"], {"x.com.samsung.da.items": items}


def _cook_time_write(p, rep, href=None):
    """Write operationTime + remainingTime (H:MM:SS) from minutes."""
    try:
        minutes = round(float(p))
    except (TypeError, ValueError):
        return None
    if not (0 <= minutes <= 1439):
        return None
    h, m = divmod(minutes, 60)
    hms = f"{h:02d}:{m:02d}:00"
    return ["operational", "state", "vs", "0"], {
        "x.com.samsung.da.operationTime": hms,
        "x.com.samsung.da.remainingTime": hms,
    }


def _oven_mode_options(resources):
    """Live mode list from the device's own /mode/vs/0 supportedModes when
    it reports one; the NV7000BS-era _OVEN_MODES guess otherwise. Mirrors
    laundry.py's options_field pattern (buzzer_sound/finish_sound), but
    needs the callable form rather than options_field because a static
    fallback has to kick in when the device's own field is absent."""
    rep = resources.get("/mode/vs/0") or {}
    live = rep.get("x.com.samsung.da.supportedModes")
    if not live:
        return list(_OVEN_MODES)
    # Ranges (every range fixture, plus #404's) and the #277 wall oven idle
    # in NoOperation but leave it out of supportedModes; HA's select drops a
    # current option that isn't in the list, so the idle state showed as
    # Unknown. Listing it makes it selectable too, which _oven_mode_validate
    # turns into a user-facing error rather than a silent no-op.
    if "NoOperation" in live:
        return list(live)
    return ["NoOperation", *live]


def _oven_mode_validate(p, rep, resources):
    """NoOperation is what the oven reports while idle; it is in the select
    so the idle state displays, not because the oven accepts it as a mode
    (#445 review). Picking it used to fall through to _oven_mode_write's
    None and the coordinator's warning-and-return, so the user saw nothing
    happen. Every other option in the list came from supportedModes and
    passes."""
    if p == "NoOperation":
        return "oven_mode_idle_not_selectable"
    return None


def _oven_mode_write(p, rep, href=None):
    valid = rep.get("x.com.samsung.da.supportedModes") or _OVEN_MODES
    if p not in valid:
        return None
    return ["mode", "vs", "0"], {"x.com.samsung.da.modes": [p]}


def _option_switch_write(prefix):
    """Factory for a single-token on/off options-array write -- lamp, sound,
    fast_preheat, natural_steam, energy_saving, and cooktop_on_alert were all
    a byte-for-byte copy of this same shape, one per prefix."""

    def write(p, rep, href=None):
        if p not in ("On", "Off"):
            return None
        if not rep.get("x.com.samsung.da.options"):
            return None
        return ["mode", "vs", "0"], {
            "x.com.samsung.da.options": _option_write(prefix, p),
        }

    return write


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

OVEN_OPERATIONAL_STATE = Capability(
    href="/operational/state/vs/0",
    poll_tier="hot",
    entities=(
        SensorDesc(
            key="machine_state",
            field="x.com.samsung.da.state",
            icon="mdi:stove",
            device_class="enum",
            options=("idle", "active", "pause"),
            translation_key="machine_state",
            value_fn=_to_ocf,
        ),
        BinarySensorDesc(
            key="cycle_active",
            field="x.com.samsung.da.state",
            device_class="running",
            value_fn=lambda v: _SAMSUNG_STATE_TO_OCF.get(v) == "active",
        ),
        # Range firmware parks progressPercentage at 1 while Ready (every range
        # fixture, the TP2X wall oven) and still reads 1 one second into a
        # timed bake (#183's cook-started dump). Same not-active-means-0 rule
        # as operational.py's shared sensor.
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
        SensorDesc(
            key="operation_time_minutes",
            field="x.com.samsung.da.operationTime",
            unit="min",
            state_class="measurement",
            value_fn=_op_minutes,
        ),
        SensorDesc(
            key="finish_time",
            device_class="timestamp",
            hysteresis=True,
            rep_fn=_finish_time,
        ),
        NumberDesc(
            key="cook_time",
            field="x.com.samsung.da.operationTime",
            unit="min",
            native_min=0,
            native_max=1439,
            step=1.0,
            icon="mdi:timer",
            value_fn=_op_minutes,
            write_fn=_cook_time_write,
        ),
        STOP_BUTTON,
    ),
)

OVEN_CAVITY = Capability(
    href="/oven/vs/0",
    poll_tier="hot",
    entities=(
        SensorDesc(
            key="oven_state",
            field="x.com.samsung.da.state",
        ),
    ),
)


def _oven_temp_unit(rep):
    """Same shape/risk as fridge.py's TEMPERATURES_FALLBACK: this aggregate
    `/temperatures/vs/0` items[] resource carries a per-item `unit` field
    that was previously hardcoded away (issue #7). Keeps the verified '°C'
    default when the field is absent, but reads it live -- issue #44's
    range dump is the first to report 'Fahrenheit' here."""
    items = rep.get("x.com.samsung.da.items") or []
    unit = items[0].get("x.com.samsung.da.unit") if items else None
    return normalize_temp_unit(unit, default="°C")


_PROBE_ITEM_ID = "1"


def _probe_item(rep):
    """The food probe's entry in `/temperatures/vs/0`'s items[], or None.

    Keyed by reported id, not position, so a board listing them the other
    way round can't swap the two readings. That id 1 is the probe is
    inferred from issue #490's dump, not confirmed: only the cavity with a
    probe socket reports a second item, and it carries `increment` 1
    against the cavity's 5.
    """
    for item in rep.get("x.com.samsung.da.items") or []:
        if item.get("x.com.samsung.da.id") == _PROBE_ITEM_ID:
            return item
    return None


def _probe_field(rep, field):
    item = _probe_item(rep)
    return _int(item.get(field)) if item else None


def _probe_temp_unit(rep):
    item = _probe_item(rep)
    unit = item.get("x.com.samsung.da.unit") if item else None
    return normalize_temp_unit(unit, default="°C")


def _probe_connected(rep, resources):
    """True only while a probe is actually plugged in.

    The socket's state lives on a different resource -- `/mode/vs/0`'s
    options[] carries a `meatprobe_<state>` token -- so an oven with a
    socket and nothing in it reports a full items[1] reading 0, which as a
    permanent entity is worse than no entity. Gated on "reports a state and
    it isn't disconnected" rather than on the connected spelling, which no
    dump has shown yet.
    """
    if _probe_item(rep) is None:
        return False
    mode = resources.get("/mode/vs/0") or {}
    state = _option_value(mode.get("x.com.samsung.da.options"), "meatprobe")
    return state is not None and state.lower() != "disconnected"


def _bounds_for_unit(unit):
    """(min, max, step) for `unit` -- see the SETPOINT_*_C/_F constants
    above for provenance. Bounds must track the unit shown by unit_fn, or
    the HA slider's range silently mismatches its own displayed unit."""
    if unit == "°F":
        return SETPOINT_MIN_F, SETPOINT_MAX_F, SETPOINT_STEP_F
    return SETPOINT_MIN_C, SETPOINT_MAX_C, SETPOINT_STEP_C


def _setpoint_bounds(rep):
    return _bounds_for_unit(_oven_temp_unit(rep))


OVEN_SETPOINT = Capability(
    href="/temperatures/vs/0",
    poll_tier="hot",
    entities=(
        # NumberDesc first — test_oven_setpoint_write_is_read_modify_write uses entities[0]
        NumberDesc(
            key="oven_setpoint",
            field="x.com.samsung.da.items",
            device_class="temperature",
            unit_fn=_oven_temp_unit,
            native_min=float(SETPOINT_MIN_C),
            native_max=float(SETPOINT_MAX_C),
            step=float(SETPOINT_STEP_C),
            icon="mdi:thermometer-chevron-up",
            native_min_fn=lambda rep: float(_setpoint_bounds(rep)[0]),
            native_max_fn=lambda rep: float(_setpoint_bounds(rep)[1]),
            step_fn=lambda rep: float(_setpoint_bounds(rep)[2]),
            value_fn=lambda items: _setpoint(
                items[0].get("x.com.samsung.da.desired") if items else None
            ),
            write_fn=_oven_setpoint_write,
        ),
        SensorDesc(
            key="current_temp_c",
            device_class="temperature",
            state_class="measurement",
            unit_fn=_oven_temp_unit,
            rep_fn=_current_temp,
        ),
        # The food probe rides in the same items[] array as items[1], which
        # was being dropped (issue #490 -- only items[0] was ever read).
        # Read-only on purpose: nothing in the corpus shows a probe target
        # write being accepted, and this family discards even ordinary
        # setpoint writes from idle (docs/investigations/oven-cycle-start.md).
        SensorDesc(
            key="food_probe_temp",
            device_class="temperature",
            state_class="measurement",
            unit_fn=_probe_temp_unit,
            exists_fn=_probe_connected,
            rep_fn=lambda rep: _probe_field(rep, "x.com.samsung.da.current"),
        ),
        SensorDesc(
            key="food_probe_setpoint",
            device_class="temperature",
            unit_fn=_probe_temp_unit,
            entity_category="diagnostic",
            exists_fn=_probe_connected,
            rep_fn=lambda rep: _probe_field(rep, "x.com.samsung.da.desired"),
        ),
    ),
)

# OCF-standard temperature pair, as a fallback behind the vendor array
# above (issue #490): the ARTIK051 wall oven reports both, and the vendor
# array is the superset there (unit, setpoint increment, cavity and probe
# in one rep, and the only proven write contract). So these bind the hrefs
# -- which is what keeps them out of `unbound_hrefs` -- but stand down
# while it is present, as common.py's POWER_VS_FALLBACK does in reverse.
#
# Exact hrefs, not an href_prefix pattern: `discover()` only clears an
# href that an exact-href capability claims, so a declining pattern cap
# would leave the gap open. They share the vendor entities' keys, so at
# most one materializes and the unique_ids match either way.

_VENDOR_TEMPS_HREF = "/temperatures/vs/0"


def _no_vendor_temps(rep, resources):
    return _VENDOR_TEMPS_HREF not in resources


def _ocf_temp_unit(rep):
    """OCF's own `units` field ('C'/'F'), not the vendor `unit` spelling."""
    return normalize_temp_unit(rep.get("units"), default="°C")


def _ocf_setpoint_bounds(rep):
    return _bounds_for_unit(_ocf_temp_unit(rep))


def _ocf_probe_connected(rep, resources):
    """The same socket gate `_probe_connected` applies to the vendor array,
    read here off `/mode/vs/0` alone -- an empty socket reports a live 0 on
    these hrefs too, and a permanent 0 degree probe is what the gate is
    for."""
    mode = resources.get("/mode/vs/0") or {}
    state = _option_value(mode.get("x.com.samsung.da.options"), "meatprobe")
    return state is not None and state.lower() != "disconnected"


def _ocf_setpoint_write(p, rep, href=None):
    """Write the OCF resource directly -- reached only on a board with no
    vendor array to RMW, so there is no `/temperatures/vs/0` fallback to
    prefer the way fridge.py's `_temp_setpoint_write` has. Unconfirmed on
    hardware: no dump in the corpus has this pair without the vendor one.
    """
    if not href:
        return None
    return ([s for s in href.strip("/").split("/") if s], {"temperature": round(float(p))})


OVEN_TEMP_CURRENT_OCF = Capability(
    href="/temperature/current/cook/0",
    match_fn=_no_vendor_temps,
    poll_tier="hot",
    entities=(
        SensorDesc(
            key="current_temp_c",
            field="temperature",
            device_class="temperature",
            state_class="measurement",
            unit_fn=_ocf_temp_unit,
        ),
    ),
)

OVEN_TEMP_DESIRED_OCF = Capability(
    href="/temperature/desired/cook/0",
    match_fn=_no_vendor_temps,
    poll_tier="hot",
    entities=(
        NumberDesc(
            key="oven_setpoint",
            field="temperature",
            device_class="temperature",
            unit_fn=_ocf_temp_unit,
            native_min=float(SETPOINT_MIN_C),
            native_max=float(SETPOINT_MAX_C),
            step=float(SETPOINT_STEP_C),
            native_min_fn=lambda rep: float(_ocf_setpoint_bounds(rep)[0]),
            native_max_fn=lambda rep: float(_ocf_setpoint_bounds(rep)[1]),
            step_fn=lambda rep: float(_ocf_setpoint_bounds(rep)[2]),
            icon="mdi:thermometer-chevron-up",
            write_fn=_ocf_setpoint_write,
        ),
    ),
)

OVEN_PROBE_CURRENT_OCF = Capability(
    href="/temperature/current/prob/0",
    match_fn=_no_vendor_temps,
    poll_tier="hot",
    entities=(
        SensorDesc(
            key="food_probe_temp",
            field="temperature",
            device_class="temperature",
            state_class="measurement",
            unit_fn=_ocf_temp_unit,
            exists_fn=_ocf_probe_connected,
        ),
    ),
)

OVEN_PROBE_DESIRED_OCF = Capability(
    href="/temperature/desired/prob/0",
    match_fn=_no_vendor_temps,
    poll_tier="hot",
    entities=(
        SensorDesc(
            key="food_probe_setpoint",
            field="temperature",
            device_class="temperature",
            entity_category="diagnostic",
            unit_fn=_ocf_temp_unit,
            exists_fn=_ocf_probe_connected,
        ),
    ),
)

OVEN_DOOR = Capability(
    href="/doors/vs/0",
    poll_tier="hot",
    entities=(
        BinarySensorDesc(
            key="door_open",
            field="x.com.samsung.da.items",
            device_class="door",
            value_fn=lambda items: (
                items[0].get("x.com.samsung.da.openState") == "Open" if items else None
            ),
        ),
    ),
)

OVEN_CONNECTED = Capability(
    href="/connected/vs/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="cloud_connected",
            field="x.com.samsung.da.connected",
            device_class="connectivity",
            entity_category="diagnostic",
            value_fn=lambda v: v == "On",
        ),
    ),
)

# Static cavity capability metadata -- no per-cavity data varies at runtime
# on any dump seen so far. Bound with no entities purely for coverage.
OVEN_SPEC = Capability(href="/oven/spec/vs/0")

# Quick-recipe display blob (combi microwave, issue #121) -- every field
# blank on the only dump seen, no documented write contract.
OVEN_RECIPE_COOK = Capability(href="/recipe/cook/vs/0")

OVEN_MODE = Capability(
    href="/mode/vs/0",
    poll_tier="warm",
    entities=(
        # SelectDesc first — test_oven_mode_options_nonempty uses entities[0]
        SelectDesc(
            key="oven_mode",
            field="x.com.samsung.da.modes",
            icon="mdi:tune",
            options=_oven_mode_options,
            value_fn=lambda v: v[0] if v else None,
            write_fn=_oven_mode_write,
            validate_fn=_oven_mode_validate,
        ),
        # No exists_fn on the NV7000BS-class board this was proven against
        # (UpperLamp_ is always in its options[]) -- but issue #300's
        # steam-oven-class WALLOVEN board's options[] has no UpperLamp_
        # token at all, so this was a phantom, always-off, write-does-
        # nothing switch there. Same fastpreheat/NaturalSteam-class gap
        # issue #183 already fixed on the other switches below.
        SwitchDesc(
            key="lamp",
            field="x.com.samsung.da.options",
            icon="mdi:track-light",
            exists_fn=_has_option("UpperLamp"),
            value_fn=lambda opts: _option_value(opts, "UpperLamp") == "On",
            write_fn=_option_switch_write("UpperLamp"),
        ),
        SwitchDesc(
            key="sound",
            field="x.com.samsung.da.options",
            icon="mdi:volume-high",
            entity_category="config",
            value_fn=lambda opts: _option_value(opts, "Sound") == "On",
            write_fn=_option_switch_write("Sound"),
        ),
        SwitchDesc(
            key="fast_preheat",
            field="x.com.samsung.da.options",
            icon="mdi:fire",
            exists_fn=_has_option("fastpreheat"),
            value_fn=lambda opts: _option_value(opts, "fastpreheat") == "On",
            write_fn=_option_switch_write("fastpreheat"),
        ),
        SwitchDesc(
            key="natural_steam",
            field="x.com.samsung.da.options",
            icon="mdi:kettle-steam",
            exists_fn=_has_option("NaturalSteam"),
            value_fn=lambda opts: _option_value(opts, "NaturalSteam") == "On",
            write_fn=_option_switch_write("NaturalSteam"),
        ),
        # 120-hour energy-saving standby (issue #183): confirmed present in
        # this unit's options[] -- unlike fast_preheat/natural_steam above,
        # this token is real on this hardware, just previously unbound.
        SwitchDesc(
            key="energy_saving",
            field="x.com.samsung.da.options",
            icon="mdi:leaf",
            entity_category="config",
            exists_fn=_has_option("EnergySaving"),
            value_fn=lambda opts: _option_value(opts, "EnergySaving") == "On",
            write_fn=_option_switch_write("EnergySaving"),
        ),
        # Cooktop-on alert (issue #183): also confirmed present
        # (BurnerOnAlert_Off) though the reporter noted it mainly matters for
        # the SmartThings app's own alerting, not local automation.
        SwitchDesc(
            key="cooktop_on_alert",
            field="x.com.samsung.da.options",
            icon="mdi:alert-circle-outline",
            entity_category="config",
            exists_fn=_has_option("BurnerOnAlert"),
            value_fn=lambda opts: _option_value(opts, "BurnerOnAlert") == "On",
            write_fn=_option_switch_write("BurnerOnAlert"),
        ),
    ),
)
