"""Decoders and entities for `/ehscycle/vs/0`, the EHS heat pump's on-device
indoor/outdoor cycle telemetry log.

Resource shape (verified against a real TP1X_DA_AC_EHS_01001_0000 dump):

    {
      "indoor":  [{"cycledata": "<hex>", "datetime": "<iso>"}, ...],
      "outdoor": [{"cycledata": "<hex>", "datetime": "<iso>"}, ...],
      "unit": "Celsius"
    }

Each of `indoor`/`outdoor` is a rolling log, OLDEST FIRST -- the freshest
reading is always the *last* list element, never the first. `_last_sample`
implements that selection and is deliberately defensive throughout: a
missing key, an empty list, a non-dict sample, a non-hex `cycledata`
string or an odd-length hex string all resolve to `None` rather than
raising, since a malformed dump must never crash `flatten()`.

`cycledata` is a fixed-width binary record whose meaning is keyed off its
decoded *byte length* -- Samsung reuses the same hex-string field across
several distinct board/firmware generations of record, each with its own
layout. Dispatch table:

    bytes  which    status
    31     indoor   VALIDATED against our fixture -- decoded below
    28     outdoor  VALIDATED against our fixture -- decoded below
    36     indoor   documented elsewhere, not in our fixture -- the first
                     31 bytes share the validated 31-byte indoor layout;
                     the trailing 5 bytes are unknown and unused. Byte 7
                     (flow rate) is separately documented as unreliable on
                     *this* variant specifically; we decode it the same way
                     regardless, because with no 36-byte dump to check
                     against there is nothing to substitute that would be
                     any better founded. Revisit if such a dump surfaces.
    24     indoor   documented elsewhere, not in our fixture, and uses a
                     *different* byte map than the 31/36-byte form -- we
                     don't have that map, so this returns None rather than
                     guess at it
    18     outdoor  documented elsewhere, not in our fixture -- same story,
                     returns None
    other  --       unrecognised length, returns None

Byte tables (0-indexed; all temperatures use `b - 55`, the same convention
`airconditioner.py` already uses for the room-AC outdoor temperature --
see the `outdoor_temperature` SensorDesc there, ~line 587):

Indoor (31-byte form, and the first 31 bytes of the 36-byte form):
    0      evaporator inlet temperature   b - 55
    2      return water temperature       b - 55
    3      flow water temperature         b - 55
    4      room air temp, air-reference   b - 55; 0x05 is a documented null
           units                          sentinel meaning "this unit runs
                                           on a water reference, not air" --
                                           not bound as a sensor
    7      flow rate                      b / 10, L/min
    8      inverter pump speed            b, %
    21-23  cycle counter, uint24 BE       monotonic; not bound as a sensor,
                                           only used to confirm which sample
                                           in the rolling log is newest
    27     DHW tank temperature           b - 55; exactly duplicates
                                           /temperatures/dhw/vs/0's `current`
                                           on this dump. Not bound as its
                                           own sensor -- only decoded so a
                                           test can cross-check this whole
                                           table's byte alignment against
                                           that independent resource.

Outdoor (28-byte form):
    0-1    compressor frequency            int16 BE, Hz
    2-3    target compressor frequency     int16 BE, Hz
    5      discharge gas temperature       b - 55
    6      evaporator saturation temp      b - 55
    7      suction line temperature        b - 55
    8      outdoor temperature             b - 55
    20-21  flow-rate fallback              int16 BE / 10, L/min -- NOT bound.
           On our fixture this reads 7.0 L/min while the indoor flow byte
           and the indoor pump-speed byte (above) independently agree on
           0.0 L/min / 0% for the very same sample. Two independent bytes
           outvote the third, so this fallback is treated as unreliable
           rather than blended in or preferred.

No staleness correction is applied to the indoor flow-rate byte. An
earlier version of this plan considered gating `cycle_flow_rate` to 0
whenever pump speed reads 0, on the theory that the cycle log free-runs a
stale non-zero flow reading while the compressor is idle. That behaviour
is documented for Samsung's cloud/SmartThings REST API, not for a direct
DTLS-CoAP read like this integration performs, and our fixture is direct
evidence against needing it here: pump speed and the indoor flow-rate
byte agree (both 0) within the same sample, on a direct-read dump. So
`cycle_flow_rate` is bound straight through, unmodified.
"""
from __future__ import annotations

from typing import Optional

from ..capability import Capability
from ..entities import SensorDesc
from .common import normalize_temp_unit, parse_iso_utc


# ---------------------------------------------------------------------------
# Sample selection -- defensive by construction, never raises.
# ---------------------------------------------------------------------------

def _hex_bytes(hexstr) -> Optional[bytes]:
    if not isinstance(hexstr, str) or not hexstr:
        return None
    try:
        return bytes.fromhex(hexstr)
    except ValueError:
        return None


def _last_sample_dict(rep, which: str) -> Optional[dict]:
    if not isinstance(rep, dict):
        return None
    samples = rep.get(which)
    if not isinstance(samples, list) or not samples:
        return None
    last = samples[-1]
    return last if isinstance(last, dict) else None


def _last_sample(rep, which: str) -> Optional[bytes]:
    """The most recent sample's decoded `cycledata` bytes for 'indoor' or
    'outdoor' -- the log is oldest-first, so the freshest reading is
    always the last list element."""
    sample = _last_sample_dict(rep, which)
    if sample is None:
        return None
    return _hex_bytes(sample.get('cycledata'))


def _cycle_temp_unit(rep):
    # This resource's own `unit` field, not the `x.com.samsung.da.unit`
    # field the other EHS temperature resources use -- see ehs._temp_unit.
    return normalize_temp_unit(rep.get('unit') if isinstance(rep, dict) else None, '°C')


def _i16(b: bytes, offset: int) -> int:
    return int.from_bytes(b[offset:offset + 2], 'big', signed=True)


def _u24(b: bytes, offset: int) -> int:
    return int.from_bytes(b[offset:offset + 3], 'big', signed=False)


# ---------------------------------------------------------------------------
# Indoor byte decoders (31-byte form; the 36-byte form shares this layout
# for its first 31 bytes). Each returns None for any other length.
# ---------------------------------------------------------------------------

def indoor_evaporator_inlet_temperature(b: bytes) -> Optional[float]:
    if len(b) not in (31, 36):
        return None
    return float(b[0] - 55)


def indoor_return_temperature(b: bytes) -> Optional[float]:
    if len(b) not in (31, 36):
        return None
    return float(b[2] - 55)


def indoor_flow_temperature(b: bytes) -> Optional[float]:
    if len(b) not in (31, 36):
        return None
    return float(b[3] - 55)


def indoor_flow_rate(b: bytes) -> Optional[float]:
    if len(b) not in (31, 36):
        return None
    return b[7] / 10


def indoor_pump_speed(b: bytes) -> Optional[float]:
    if len(b) not in (31, 36):
        return None
    return float(b[8])


def indoor_cycle_counter(b: bytes) -> Optional[int]:
    """Not bound as a sensor -- used only to prove sample-freshness
    selection (§ module docstring) against the real fixture bytes."""
    if len(b) not in (31, 36):
        return None
    return _u24(b, 21)


def indoor_dhw_tank_temperature(b: bytes) -> Optional[float]:
    """Not bound as a sensor -- duplicates /temperatures/dhw/vs/0's
    `current`. Exposed so a test can decode byte 27 directly and assert
    that equality, the cheapest proof this whole byte table is aligned."""
    if len(b) not in (31, 36):
        return None
    return float(b[27] - 55)


# ---------------------------------------------------------------------------
# Outdoor byte decoders (28-byte form only).
# ---------------------------------------------------------------------------

def outdoor_compressor_frequency(b: bytes) -> Optional[float]:
    if len(b) != 28:
        return None
    return float(_i16(b, 0))


def outdoor_compressor_target_frequency(b: bytes) -> Optional[float]:
    if len(b) != 28:
        return None
    return float(_i16(b, 2))


def outdoor_discharge_temperature(b: bytes) -> Optional[float]:
    if len(b) != 28:
        return None
    return float(b[5] - 55)


def outdoor_evaporator_saturation_temperature(b: bytes) -> Optional[float]:
    if len(b) != 28:
        return None
    return float(b[6] - 55)


def outdoor_suction_temperature(b: bytes) -> Optional[float]:
    if len(b) != 28:
        return None
    return float(b[7] - 55)


def outdoor_temperature(b: bytes) -> Optional[float]:
    if len(b) != 28:
        return None
    return float(b[8] - 55)


# ---------------------------------------------------------------------------
# rep_fn plumbing: decode the newest sample of the given loop, defensively.
# ---------------------------------------------------------------------------

def _indoor(decoder):
    def rep_fn(rep):
        b = _last_sample(rep, 'indoor')
        return None if b is None else decoder(b)
    return rep_fn


def _outdoor(decoder):
    def rep_fn(rep):
        b = _last_sample(rep, 'outdoor')
        return None if b is None else decoder(b)
    return rep_fn


def _cycle_updated(rep):
    sample = _last_sample_dict(rep, 'indoor')
    if sample is None:
        return None
    # The device reports a bare ISO string ("2026-08-01T21:23:08"), no
    # offset/'Z'. parse_iso_utc is this codebase's established handling for
    # that shape (see its docstring in common.py) -- treat a naive value as
    # UTC rather than feed the entity's device_class='timestamp' a naive
    # datetime, which HA rejects outright.
    #
    # Open question, resolvable only against live hardware: whether this
    # board's clock is actually UTC or local wall time. If it's local, this
    # reads off by the site's UTC offset. Treating it as UTC keeps us
    # consistent with every other bare ISO field in this integration rather
    # than making EHS the one family that guesses differently -- but a
    # capture taken at a known wall-clock time would settle it either way.
    return parse_iso_utc(sample.get('datetime'))


# Cycle data refreshes roughly every 5 minutes on the device (consecutive
# fixture samples are 5 minutes apart and the counter advances by 5 between
# them) -- 'warm' matches that cadence rather than over- or under-polling it.
EHS_CYCLE = Capability(
    href='/ehscycle/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='cycle_flow_temperature', rep_fn=_indoor(indoor_flow_temperature),
                   device_class='temperature', unit_fn=_cycle_temp_unit,
                   state_class='measurement'),
        SensorDesc(key='cycle_return_temperature', rep_fn=_indoor(indoor_return_temperature),
                   device_class='temperature', unit_fn=_cycle_temp_unit,
                   state_class='measurement'),
        SensorDesc(key='cycle_flow_rate', rep_fn=_indoor(indoor_flow_rate),
                   unit='L/min', device_class='volume_flow_rate',
                   state_class='measurement'),
        SensorDesc(key='cycle_pump_speed', rep_fn=_indoor(indoor_pump_speed),
                   unit='%', state_class='measurement'),
        SensorDesc(key='compressor_frequency', rep_fn=_outdoor(outdoor_compressor_frequency),
                   unit='Hz', device_class='frequency',
                   state_class='measurement'),
        SensorDesc(key='outdoor_temperature', rep_fn=_outdoor(outdoor_temperature),
                   device_class='temperature', unit_fn=_cycle_temp_unit,
                   state_class='measurement'),
        SensorDesc(key='compressor_target_frequency',
                   rep_fn=_outdoor(outdoor_compressor_target_frequency),
                   unit='Hz', device_class='frequency',
                   state_class='measurement',
                   entity_category='diagnostic', enabled_default=False),
        SensorDesc(key='discharge_temperature', rep_fn=_outdoor(outdoor_discharge_temperature),
                   device_class='temperature', unit_fn=_cycle_temp_unit,
                   state_class='measurement',
                   entity_category='diagnostic', enabled_default=False),
        SensorDesc(key='evaporator_saturation_temperature',
                   rep_fn=_outdoor(outdoor_evaporator_saturation_temperature),
                   device_class='temperature', unit_fn=_cycle_temp_unit,
                   state_class='measurement',
                   entity_category='diagnostic', enabled_default=False),
        SensorDesc(key='suction_temperature', rep_fn=_outdoor(outdoor_suction_temperature),
                   device_class='temperature', unit_fn=_cycle_temp_unit,
                   state_class='measurement',
                   entity_category='diagnostic', enabled_default=False),
        SensorDesc(key='evaporator_inlet_temperature',
                   rep_fn=_indoor(indoor_evaporator_inlet_temperature),
                   device_class='temperature', unit_fn=_cycle_temp_unit,
                   state_class='measurement',
                   entity_category='diagnostic', enabled_default=False),
        SensorDesc(key='cycle_updated', rep_fn=_cycle_updated,
                   device_class='timestamp',
                   entity_category='diagnostic', enabled_default=False),
    ),
)
