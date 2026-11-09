"""Sensors decoded from `/ehscycle/vs/0`, the EHS heat pump's cycle log.

`indoor` and `outdoor` each hold a few `{"datetime", "cycledata"}` samples,
oldest first, and `cycledata` is a hex-encoded fixed-width record. Byte map
from https://www.dwellsee.com/decoding-guide/ (0-indexed, temperatures are
`b - 55` in °C):

    indoor, 31 bytes (TP1X, MIM-H04EN) or 36 bytes (S905D3)
      2     return water temperature
      3     flow water temperature
      7     flow rate, b / 10 L/min
      8     water pump speed, %
      27    DHW tank temperature (not bound, duplicates /temperatures/dhw/vs/0)
    outdoor, 28 bytes
      0-1   compressor frequency, int16 BE, Hz
      2-3   target compressor frequency, int16 BE, Hz
      5     discharge temperature
      6     evaporator saturation temperature
      7     suction temperature
      8     outdoor temperature

Indoor byte 27 matches `/temperatures/dhw/vs/0` on the 01001 and MIM-H04EN
dumps, outdoor byte 8 matches the MIM-H04EN's `/temperatures/outside/vs/0`,
and the S905D3 sample in #697 matches its `/overview/vs/0` flow, return, flow
rate and outdoor readings. The guide's 24-byte indoor and 18-byte outdoor
records aren't in any local dump yet, so they and any other length read as
unknown.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..capability import Capability
from ..entities import SensorDesc
from .common import normalize_temp_unit, parse_iso_utc

_INDOOR_LENGTHS = (31, 36)
_OUTDOOR_LENGTHS = (28,)

# The log stops advancing while the unit is switched off (decoding guide,
# "System Off Behaviour"), so an old sample would otherwise read as live. The
# boards' clocks report UTC, and samples arrive every 30 s (S905D3) to 5 min
# (TP1X).
_STALE_AFTER = timedelta(minutes=30)


def _now() -> datetime:
    return datetime.now(UTC)


def _newest(rep, which: str) -> dict | None:
    samples = rep.get(which) if isinstance(rep, dict) else None
    if not isinstance(samples, list) or not samples:
        return None
    sample = samples[-1]
    return sample if isinstance(sample, dict) else None


def _fresh_bytes(rep, which: str, lengths: tuple[int, ...]) -> bytes | None:
    sample = _newest(rep, which)
    if sample is None:
        return None
    taken = parse_iso_utc(sample.get("datetime"))
    if taken is None or _now() - taken > _STALE_AFTER:
        return None
    try:
        b = bytes.fromhex(sample.get("cycledata") or "")
    except (TypeError, ValueError):
        return None
    return b if len(b) in lengths else None


def _indoor(decode):
    def rep_fn(rep):
        b = _fresh_bytes(rep, "indoor", _INDOOR_LENGTHS)
        return None if b is None else decode(b)

    return rep_fn


def _outdoor(decode):
    def rep_fn(rep):
        b = _fresh_bytes(rep, "outdoor", _OUTDOOR_LENGTHS)
        return None if b is None else decode(b)

    return rep_fn


def _temp(offset: int):
    return lambda b: float(b[offset] - 55)


def _i16(offset: int):
    return lambda b: float(int.from_bytes(b[offset : offset + 2], "big", signed=True))


def _cycle_temp_unit(rep):
    return normalize_temp_unit(rep.get("unit") if isinstance(rep, dict) else None, "°C")


def _updated(rep):
    sample = _newest(rep, "indoor")
    return None if sample is None else parse_iso_utc(sample.get("datetime"))


def _temperature(key: str, rep_fn, **kw) -> SensorDesc:
    return SensorDesc(
        key=key,
        rep_fn=rep_fn,
        device_class="temperature",
        unit_fn=_cycle_temp_unit,
        state_class="measurement",
        **kw,
    )


def _diagnostic_temperature(key: str, rep_fn) -> SensorDesc:
    return _temperature(key, rep_fn, entity_category="diagnostic", enabled_default=False)


EHS_CYCLE = Capability(
    href="/ehscycle/vs/0",
    poll_tier="warm",
    entities=(
        _temperature("cycle_flow_temperature", _indoor(_temp(3))),
        _temperature("cycle_return_temperature", _indoor(_temp(2))),
        SensorDesc(
            key="cycle_flow_rate",
            rep_fn=_indoor(lambda b: b[7] / 10),
            unit="L/min",
            device_class="volume_flow_rate",
            state_class="measurement",
        ),
        SensorDesc(
            key="cycle_pump_speed",
            rep_fn=_indoor(lambda b: float(b[8])),
            unit="%",
            state_class="measurement",
        ),
        SensorDesc(
            key="compressor_frequency",
            rep_fn=_outdoor(_i16(0)),
            unit="Hz",
            device_class="frequency",
            state_class="measurement",
        ),
        # The MIM-H04EN's /temperatures/outside/vs/0 reads the same sensor at
        # 0.1 °C resolution, so this one stays off the boards that have it.
        _temperature(
            "cycle_outdoor_temperature",
            _outdoor(_temp(8)),
            translation_key="outdoor_temperature",
            exists_fn=lambda rep, resources: "/temperatures/outside/vs/0" not in resources,
        ),
        SensorDesc(
            key="compressor_target_frequency",
            rep_fn=_outdoor(_i16(2)),
            unit="Hz",
            device_class="frequency",
            state_class="measurement",
            entity_category="diagnostic",
            enabled_default=False,
        ),
        _diagnostic_temperature("discharge_temperature", _outdoor(_temp(5))),
        _diagnostic_temperature("evaporator_saturation_temperature", _outdoor(_temp(6))),
        _diagnostic_temperature("suction_temperature", _outdoor(_temp(7))),
        SensorDesc(
            key="cycle_updated",
            rep_fn=_updated,
            device_class="timestamp",
            entity_category="diagnostic",
            enabled_default=False,
        ),
    ),
)
