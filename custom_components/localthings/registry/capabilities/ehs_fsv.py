"""Sensors decoded from `/ehsfsv/vs/0`, the EHS heat pump's Field Setting
Values (installer settings).

Each `items[].setting` is a hex-encoded 13-byte record:

    0-1    FSV code, uint16 BE (0x0407 is FSV 1031)
    3      scale divisor, 1 or 10
    5-6    minimum, int16 BE / scale
    7-8    maximum, int16 BE / scale
    9-10   value, int16 BE / scale

FSV 1051/1052's values match `/temperatures/dhw/vs/0`'s `maximum`/`minimum` on
the 01001 and MIM-H04EN dumps. Code meanings follow
https://github.com/pookey/samsung-heatpump-smartthings/blob/main/fsv_settings.py.
Every dump so far reports the same 20 codes; a sensor whose code is missing
isn't created.
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
    try:
        b = bytes.fromhex(setting)
    except (TypeError, ValueError):
        return None
    if len(b) != 13:
        return None
    scale = b[3] or 1
    return FsvRecord(
        code=int.from_bytes(b[0:2], "big"),
        minimum=_i16(b, 5) / scale,
        maximum=_i16(b, 7) / scale,
        value=_i16(b, 9) / scale,
    )


def _find_fsv(rep, code: int) -> FsvRecord | None:
    items = rep.get("items") if isinstance(rep, dict) else None
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict):
            record = _decode_setting(item.get("setting"))
            if record is not None and record.code == code:
                return record
    return None


def _fsv(code: int, key: str, *, temperature: bool = False) -> SensorDesc:
    # Read-only until a write contract is confirmed. Enum-valued codes stay
    # numeric until their labels are confirmed on a device.
    extra: dict[str, Any] = {}
    if temperature:
        extra["device_class"] = "temperature"
        extra["unit"] = "°C"

    def value(rep):
        record = _find_fsv(rep, code)
        return None if record is None else record.value

    return SensorDesc(
        key=key,
        rep_fn=value,
        exists_fn=lambda rep, resources: _find_fsv(rep, code) is not None,
        entity_category="diagnostic",
        enabled_default=False,
        **extra,
    )


FSV_SENSORS = (
    _fsv(2011, "fsv_outdoor_curve_cold_point", temperature=True),
    _fsv(2012, "fsv_outdoor_curve_warm_point", temperature=True),
    _fsv(2021, "fsv_water_law_1_cold_target", temperature=True),
    _fsv(2022, "fsv_water_law_1_warm_target", temperature=True),
    _fsv(2031, "fsv_water_law_2_cold_target", temperature=True),
    _fsv(2032, "fsv_water_law_2_warm_target", temperature=True),
    _fsv(1051, "fsv_dhw_temperature_maximum", temperature=True),
    _fsv(1052, "fsv_dhw_temperature_minimum", temperature=True),
    _fsv(3011, "fsv_dhw_application_mode"),
    _fsv(4011, "fsv_heating_dhw_priority"),
    _fsv(4012, "fsv_heating_dhw_changeover_temperature", temperature=True),
    _fsv(4021, "fsv_backup_heater_application"),
    _fsv(1031, "fsv_heating_outlet_temperature_maximum", temperature=True),
    _fsv(1032, "fsv_heating_outlet_temperature_minimum", temperature=True),
    _fsv(4061, "fsv_zone_control_application"),
)

# Installer settings change only when someone recommissions the unit.
EHS_FSV = Capability(
    href="/ehsfsv/vs/0",
    poll_tier="cold",
    entities=FSV_SENSORS,
)
