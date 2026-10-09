"""Capabilities for the Samsung EHS (Eco Heating System) air-to-water heat
pump family (TP1X_DA_AC_EHS-class, model TP1X_DA_AC_EHS_01001_0000).

An EHS unit runs two independently-controlled loops off one outdoor unit:
space heating/cooling ("zone1", through /mode/vs/0, /power/vs/0,
/temperatures/indoor/vs/0) and domestic hot water ("dhw", through
/mode/dhw/vs/0, /power/dhw/vs/0, /temperatures/dhw/vs/0). There's no shared
vocabulary with the room-AC family in airconditioner.py beyond the DA_AC_
board prefix -- EHS reports its own /mode/*/vs/0 and /temperatures/*/vs/0
shapes, not airconditioner.py's HREF_MODE/HREF_TEMP* OCF-pattern hrefs.

Each space-heating zone is an HA climate entity (issue #581), as in HA
core's smartthings integration: the zone's own power and temperature plus
the shared /mode/vs/0. zone1's older switch/select/number stay for existing
installs, disabled by default. dhw is a water_heater.py entity (see DHW
below). Both follow airconditioner.py's primary-resource-plus-sibling-reads
pattern.

Verified against a real TP1X_DA_AC_EHS_01001_0000 diagnostics dump
(firmware AEH-WW-TP1-22-AE6000_17260402, TizenRT 3.1 / DAWIT 2.0).
"""

from ..capability import Capability
from ..entities import (
    NumberDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
    WaterHeaterDesc,
    ZoneClimateDesc,
)
from .common import normalize_temp_unit


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _first_mode(rep):
    """Representative scalar for a mode select -- `modes` is a single-element
    list on every dump seen, mirroring airconditioner._first_mode."""
    modes = rep.get("x.com.samsung.da.modes")
    if isinstance(modes, (list, tuple)):
        return modes[0] if modes else None
    return modes


def _temp_unit(rep):
    return normalize_temp_unit(rep.get("x.com.samsung.da.unit"), "°C")


def _bounds(rep, default_min, default_max):
    """The resource's own (minimum, maximum) pair, or the defaults. Both
    ends together or neither -- a board reporting only one would otherwise
    pair a real bound with an invented default, silently wrong."""
    lo = _num(rep.get("x.com.samsung.da.minimum"))
    hi = _num(rep.get("x.com.samsung.da.maximum"))
    return (lo, hi) if (lo is not None and hi is not None) else (default_min, default_max)


def _step(rep, default):
    """`is None`, not `or` -- `or` collapses a genuine 0 (issue #160)."""
    step = _num(rep.get("x.com.samsung.da.increment"))
    return default if step is None else step


# Canonical zone hrefs. Mode is device-wide: zone2 has no /mode/zone2/vs/0
# on issue #581's board, so both zones' climate entities share /mode/vs/0.
HREF_ZONE_MODE = "/mode/vs/0"
HREF_ZONE1_POWER = "/power/vs/0"
HREF_ZONE1_TEMPERATURE = "/temperatures/indoor/vs/0"
HREF_ZONE2_POWER = "/power/zone2/vs/0"
HREF_ZONE2_TEMPERATURE = "/temperatures/zone2/indoor/vs/0"


def _segs(href):
    return href.strip("/").split("/")


def _zone_write(power_href, temperature_href):
    """(kind, value) -> (path_segs, body) for one zone's climate entity, same
    contract as _dhw_write below."""

    def write(payload, rep, href=None):
        kind, value = payload
        if kind == "power":
            return (_segs(power_href), {"x.com.samsung.da.power": "On" if value else "Off"})
        if kind == "mode":
            return (_segs(HREF_ZONE_MODE), {"x.com.samsung.da.modes": [value]})
        if kind == "temperature":
            return (_segs(temperature_href), {"x.com.samsung.da.desired": str(float(value))})
        return None

    return write


def _zone_climate(key, power_href, temperature_href):
    return ZoneClimateDesc(
        key=key,
        # Representative scalar for the snapshot; the entity reads its
        # siblings itself, like DHW's rep_fn below.
        field="x.com.samsung.da.desired",
        value_fn=_num,
        power_href=power_href,
        mode_href=HREF_ZONE_MODE,
        temperature_href=temperature_href,
        write_fn=_zone_write(power_href, temperature_href),
    )


def _zone_temperature_sensor(key):
    return SensorDesc(
        key=key,
        field="x.com.samsung.da.current",
        device_class="temperature",
        unit_fn=_temp_unit,
        state_class="measurement",
        value_fn=_num,
    )


ZONE_POWER = Capability(
    href=HREF_ZONE1_POWER,
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="zone_power",
            field="x.com.samsung.da.power",
            icon="mdi:radiator",
            enabled_default=False,
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["power", "vs", "0"],
                {"x.com.samsung.da.power": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

ZONE_MODE = Capability(
    href=HREF_ZONE_MODE,
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="zone_mode",
            rep_fn=_first_mode,
            enabled_default=False,
            icon="mdi:sun-snowflake-variant",
            options_field="x.com.samsung.da.supportedModes",
            write_fn=lambda p, rep, href=None: (
                ["mode", "vs", "0"],
                {"x.com.samsung.da.modes": [p]},
            ),
        ),
    ),
)

# type=Water/unit=Celsius on this dump names the space-heating loop's flow/
# room setpoint, not a literal water temperature -- EHS zone control is
# leaving-water-temperature-based, same convention as the dhw loop below.
ZONE_TEMPERATURE = Capability(
    href=HREF_ZONE1_TEMPERATURE,
    poll_tier="warm",
    entities=(
        _zone_temperature_sensor("zone_temperature"),
        _zone_climate("zone_climate", HREF_ZONE1_POWER, HREF_ZONE1_TEMPERATURE),
        NumberDesc(
            key="zone_target_temperature",
            field="x.com.samsung.da.desired",
            device_class="temperature",
            unit_fn=_temp_unit,
            entity_category="config",
            enabled_default=False,
            value_fn=_num,
            native_min_fn=lambda rep: _bounds(rep, 5.0, 30.0)[0],
            native_max_fn=lambda rep: _bounds(rep, 5.0, 30.0)[1],
            step_fn=lambda rep: _step(rep, 0.5),
            write_fn=lambda p, rep, href=None: (
                ["temperatures", "indoor", "vs", "0"],
                {"x.com.samsung.da.desired": str(float(p))},
            ),
        ),
    ),
)

# Zone 2 (issue #581, TP1X_DA_AC_EHS_01002_0000): same fields as zone1's,
# minus the temperature unit, which _temp_unit defaults to Celsius. Writes
# mirror zone1's and need live confirmation.
ZONE2_TEMPERATURE = Capability(
    href=HREF_ZONE2_TEMPERATURE,
    poll_tier="warm",
    entities=(
        _zone_temperature_sensor("zone2_temperature"),
        _zone_climate("zone2_climate", HREF_ZONE2_POWER, HREF_ZONE2_TEMPERATURE),
    ),
)
ZONE2_POWER = Capability(href=HREF_ZONE2_POWER, poll_tier="warm")

# Canonical dhw resource hrefs. water_heater.py binds HREF_DHW_MODE via DHW
# below and reads the sibling power/temperature hrefs off the coordinator
# snapshot -- same primary-plus-siblings shape as airconditioner.py's
# HREF_MODE/CLIMATE_CONSUMED_HREFS.
HREF_DHW_POWER = "/power/dhw/vs/0"  # on/off
HREF_DHW_MODE = "/mode/dhw/vs/0"  # primary (bound by DHW) -- current_operation
HREF_DHW_TEMPERATURE = "/temperatures/dhw/vs/0"  # current/target temperature

DHW_CONSUMED_HREFS = [HREF_DHW_POWER, HREF_DHW_TEMPERATURE]


def _dhw_write(payload, rep, href=None):
    """Map a (kind, value) command from the water_heater platform to the
    (path_segs, body) for that one sub-write -- same contract as
    airconditioner._climate_write, across the dhw loop's three resources."""
    kind, value = payload
    if kind == "power":
        return (["power", "dhw", "vs", "0"], {"x.com.samsung.da.power": "On" if value else "Off"})
    if kind == "mode":
        return (["mode", "dhw", "vs", "0"], {"x.com.samsung.da.modes": [value]})
    if kind == "temperature":
        return (["temperatures", "dhw", "vs", "0"], {"x.com.samsung.da.desired": str(float(value))})
    return None


DHW = Capability(
    href=HREF_DHW_MODE,
    poll_tier="warm",
    entities=(
        WaterHeaterDesc(
            key="water_heater", translation_key="dhw", rep_fn=_first_mode, write_fn=_dhw_write
        ),
    ),
)

# Power and temperature are read by the composite DHW entity above, not
# given their own entities -- coverage-only caps so discover() reports no
# gap (see airconditioner.py's CLIMATE_CONSUMED_HREFS).
DHW_CONSUMED = [Capability(href=h, poll_tier="warm") for h in DHW_CONSUMED_HREFS]

# Deliberately a plain config switch, not water_heater's AWAY_MODE feature.
# HA core's smartthings integration wires this same Samsung capability up
# to WaterHeaterEntityFeature.AWAY_MODE, so the divergence is worth
# stating: /option/outgoing/vs/0 is device-wide (one `away` flag covering
# zone1 too, with no dhw-scoped sibling href). Hanging it off the DHW card
# would present a device-wide setting as hot-water-only.
AWAY_MODE = Capability(
    href="/option/outgoing/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="away_mode",
            field="x.com.samsung.da.away",
            icon="mdi:home-export-outline",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["option", "outgoing", "vs", "0"],
                {"x.com.samsung.da.away": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# EHS-scoped coverage: opaque vendor plumbing or resources with no
# confirmed write contract on this dump. Not in the global ignored.IGNORED
# since these are EHS-only shapes needing their own verification elsewhere.
_EHS_IGNORED = [
    "/availablecontrolsets/vs/0",  # opaque hex-encoded control-set bitmap (id: EHS)
    "/da/softreset/vs/0",  # soft-reset trigger plumbing
    "/diagnosis/vs/0",  # empty {} on this dump
    "/ehscycle/vs/0",  # opaque hex-encoded indoor/outdoor cycle log
    "/ehsfsv/vs/0",  # opaque hex-encoded factory setting values
    "/option/dhwdisplay/vs/0",  # front-panel DHW-display show/hide, cosmetic only
    "/reserverulesets/vs/0",  # opaque hex-encoded schedule reservation blob
    "/sac/installationinfo/vs/0",  # static outdoor/indoor installation info, diagnostic only
    "/actions/zone1/vs/0",  # zone1 schedule/timer program -- unmodeled for now
    "/actions/zone2/vs/0",  # zone2 schedule/timer program -- unmodeled for now
    "/actions/dhw/vs/0",  # DHW schedule/timer program -- unmodeled for now
]

COVERAGE = [Capability(href=h) for h in _EHS_IGNORED]
