"""Capabilities for Samsung AHD-WW-TP1-22 range hoods.

The verified device exposes its fan, two-level work lamp, washable-filter
status, and particulate sensors as distinct local OCF resources.  Fan power
and speed are combined into one HA fan entity by ``fan.py``; lamp power and
brightness remain separate controls because the device advertises them as two
independent fields.

DAWIT 3.0 generation built-in vent hood (issue #433, combi microwave):
a different board reports the vent fan/lamp/filter as one bare-field
`/hood/status/vs/0` (unrelated to `/hood/fanspeed/vs/0` above, which it
doesn't carry), with the fan-speed/lamp vocabulary in a sibling
`/hood/spec/vs/0`. Read-only like the rest of that generation -- writes
4.05 on hardware, see capabilities/microwave.py's module docstring -- so
speed and lamp are enum sensors here, not the fan entity and selects the
fields otherwise invite.
"""

import datetime

from ..batch import is_stub_rep
from ..capability import Capability
from ..entities import (
    BinarySensorDesc,
    ButtonDesc,
    FanDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
    TimeDesc,
)
from .air_purifier import (
    _periodic_sensing_write,
    _skip_status_write,
    _skip_time_read,
    _skip_time_write,
)
from .common import (
    epoch_to_utc,
    filter_reset_button,
    has_sensor_type,
    int_or_none,
    sensor_item_value,
)


def _active_alarm_codes(items):
    """Discard the hood firmware's retained/deleted ``ErrorCode_OFF`` row.

    Unlike ``common._alarm_codes``, the hood retains a deleted alarm row in
    its live representation, so this family-specific helper also checks state.
    """
    codes = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        if str(item.get("x.com.samsung.da.state", "")).lower() == "deleted":
            continue
        code = item.get("x.com.samsung.da.code")
        if code and str(code).lower() != "errorcode_off":
            codes.append(code)
    return ", ".join(codes) if codes else "none"


HOOD_ALARMS = Capability(
    href="/alarms/vs/0",
    poll_tier="hot",
    entities=(
        SensorDesc(
            key="alarm_code",
            icon="mdi:alert",
            entity_category="diagnostic",
            # Like common.ALARMS, a present resource need not contain items
            # when clear; metadata must not suppress the sensor at setup.
            rep_fn=lambda rep: _active_alarm_codes(rep.get("x.com.samsung.da.items")),
        ),
    ),
)


def _hood_fan_write(payload, rep, href=None):
    kind, value, *args = payload
    if kind == "power":
        power_href = args[0] if args else "/power/0"
        if power_href == "/power/0":
            return ["power", "0"], {"value": bool(value)}
        if power_href == "/power/vs/0":
            return ["power", "vs", "0"], {
                "x.com.samsung.da.power": "On" if value else "Off",
            }
        return None
    if kind == "speed":
        value = str(value)
        supported = [str(code) for code in rep.get("x.com.samsung.da.hood.supportedFanSpeed", ())]
        if not supported:
            min_s = rep.get("x.com.samsung.da.hood.settableMinFanSpeed")
            max_s = rep.get("x.com.samsung.da.hood.settableMaxFanSpeed")
            if min_s is not None and max_s is not None:
                try:
                    mn, mx = int(min_s), int(max_s)
                    supported = [str(i) for i in range(mn, mx + 1)]
                except (ValueError, TypeError):
                    pass
        if value not in supported:
            return None
        return ["hood", "fanspeed", "vs", "0"], {
            "x.com.samsung.da.hood.fanSpeed": value,
        }
    return None


HOOD_FAN = Capability(
    href="/hood/fanspeed/vs/0",
    poll_tier="hot",
    entities=(
        FanDesc(
            key="fan",
            field="x.com.samsung.da.hood.fanSpeed",
            write_fn=_hood_fan_write,
        ),
        BinarySensorDesc(
            key="automatic_operation",
            field="x.com.samsung.da.hood.autoOperation",
            icon="mdi:fan-auto",
            entity_category="diagnostic",
            # Absent on the microwave family's built-in vent fan (issue
            # #137) -- this board has no auto-ventilation mode, unlike the
            # standalone range hood this capability was written for.
            exists_fn=lambda rep, resources: (
                is_stub_rep(rep) or "x.com.samsung.da.hood.autoOperation" in rep
            ),
            value_fn=lambda value: str(value).lower() == "on",
        ),
    ),
)


def _lamp_level_write(value, rep, href=None):
    code = str(value)
    supported = [str(level) for level in rep.get("x.com.samsung.lamp.range", ())]
    if code not in supported:
        return None
    return ["hood", "lamp", "vs", "0"], {
        "x.com.samsung.lamp.current": code,
    }


HOOD_LAMP = Capability(
    href="/hood/lamp/vs/0",
    poll_tier="hot",
    entities=(
        SwitchDesc(
            key="lamp",
            field="x.com.samsung.lamp.power",
            icon="mdi:range-hood",
            value_fn=lambda value: str(value).lower() == "on",
            write_fn=lambda payload, rep, href=None: (
                ["hood", "lamp", "vs", "0"],
                {"x.com.samsung.lamp.power": "On" if payload == "On" else "Off"},
            ),
        ),
        SelectDesc(
            key="lamp_brightness",
            field="x.com.samsung.lamp.current",
            icon="mdi:brightness-6",
            translation_key="range_hood_lamp_brightness",
            options_field="x.com.samsung.lamp.range",
            write_fn=_lamp_level_write,
        ),
    ),
)


HOOD_FILTER = Capability(
    href="/filter/hoodfilter/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="hood_filter_usage",
            field="x.com.samsung.da.filterUsage",
            unit="%",
            state_class="measurement",
            icon="mdi:air-filter",
            entity_category="diagnostic",
            value_fn=int_or_none,
        ),
        SensorDesc(
            key="hood_filter_status",
            field="x.com.samsung.da.filterStatus",
            icon="mdi:air-filter",
            entity_category="diagnostic",
            device_class="enum",
            options=("normal", "wash", "replace"),
            translation_key="filter_status",
            value_fn=lambda value: value.lower() if isinstance(value, str) else value,
        ),
        SensorDesc(
            key="hood_filter_capacity",
            field="x.com.samsung.da.filterCapacity",
            unit="h",
            icon="mdi:timer-outline",
            entity_category="diagnostic",
            enabled_default=False,
            value_fn=int_or_none,
        ),
        filter_reset_button("hood_filter_reset", "/filter/hoodfilter/vs/0"),
    ),
)


# After Run (issue #147): the hood keeps the fan running at low speed after
# it's switched off, to clear residual cooking smoke -- a feature a user
# actively watches and cancels, so none of the three entities below carry
# entity_category. No supported-values list is advertised for
# activationState, so it's read-only monitoring rather than an invented
# "enable" write; runningCancel's only observed value is the command name
# itself ('Cancel'), the same shape as operational.STOP_BUTTON.
AFTER_RUN = Capability(
    href="/afterrun/vs/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="after_run_active",
            field="x.com.samsung.da.activationState",
            icon="mdi:fan-clock",
            value_fn=lambda value: str(value).lower() == "on",
        ),
        SensorDesc(
            key="after_run_progress",
            field="x.com.samsung.da.runningProgress",
            unit="%",
            state_class="measurement",
            icon="mdi:fan-clock",
            value_fn=int_or_none,
        ),
        ButtonDesc(
            key="after_run_cancel",
            field="",
            payload="Cancel",
            icon="mdi:fan-off",
            write_fn=lambda p, rep, href=None: (
                ["afterrun", "vs", "0"],
                {"x.com.samsung.da.runningCancel": p},
            ),
        ),
    ),
)


# Each reading is gated on the hood listing that sensor type, the same guard
# air_purifier.AIR_QUALITY needed for a board that reports only some of them
# (issue #414). No hood dump has been short one yet; this is the guard, not a
# fix for anything observed here.
def _particle_grade(items, sensor_type):
    # The hood's second value matches SmartThings' four-level display;
    # other Samsung families use different grade floors, so keep this local.
    grade = sensor_item_value(items, sensor_type, 1)
    # Direct sensing captures show all three grades become 10 until readings settle.
    return {1: "good", 2: "normal", 3: "poor", 4: "very_poor", 10: "processing"}.get(
        grade, str(grade) if grade is not None else None
    )


def _clean_level(items):
    # CleanLevel 1-4 tracks this hood's Kr1-Kr4 stored air-quality assessment.
    level = sensor_item_value(items, "CleanLevel")
    return {1: "good", 2: "normal", 3: "poor", 4: "very_poor"}.get(
        level, str(level) if level is not None else None
    )


AIR_QUALITY = Capability(
    href="/sensors/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="clean_level",
            translation_key="range_hood_clean_level",
            device_class="enum",
            options=("good", "normal", "poor", "very_poor"),
            field="x.com.samsung.da.items",
            icon="mdi:air-filter",
            exists_fn=has_sensor_type("CleanLevel"),
            value_fn=_clean_level,
        ),
        *(
            SensorDesc(
                key=key,
                translation_key=f"range_hood_{device_class}",
                device_class=device_class,
                state_class="measurement",
                unit="μg/m³",
                icon="mdi:blur",
                field="x.com.samsung.da.items",
                exists_fn=has_sensor_type(type_),
                value_fn=lambda items, t=type_: sensor_item_value(items, t),
            )
            for key, type_, device_class in (
                ("dust", "Dust", "pm10"),
                ("fine_dust", "FineDust", "pm25"),
                ("super_fine_dust", "SuperFineDust", "pm1"),
            )
        ),
        *(
            SensorDesc(
                key=f"{key}_air_quality",
                translation_key=f"range_hood_{device_class}_air_quality",
                field="x.com.samsung.da.items",
                device_class="enum",
                options=("good", "normal", "poor", "very_poor", "processing"),
                icon="mdi:air-filter",
                exists_fn=has_sensor_type(type_),
                value_fn=lambda items, t=type_: _particle_grade(items, t),
            )
            for key, type_, device_class in (
                ("dust", "Dust", "pm10"),
                ("fine_dust", "FineDust", "pm25"),
                ("super_fine_dust", "SuperFineDust", "pm1"),
            )
        ),
    ),
)


def _sensing_modes(resources):
    rep = resources.get("/airlevelcheck/vs/0") or {}
    supported = rep.get("x.com.samsung.da.supportedAutoExeState")
    modes = supported if isinstance(supported, list) else []
    # Detection only is writable even though this hood omits Sensing from
    # supportedAutoExeState; all three app labels were cross-checked live.
    return list(dict.fromkeys(["Sensing", *(m for m in modes if isinstance(m, str) and m)]))


def _sensing_mode_write(payload, rep, href=None):
    if payload not in _sensing_modes({"/airlevelcheck/vs/0": rep}):
        return None
    return ["airlevelcheck", "vs", "0"], {"x.com.samsung.da.autoExeState": payload}


def _hood_dnd_read(part):
    def read(raw):
        if not isinstance(raw, str) or len(raw) != 8:
            return None
        start, end = _skip_time_read("start")(raw), _skip_time_read("end")(raw)
        if start is None or end is None:
            return None
        # App 22:00-05:00 in Los Angeles reports 06001300 locally: UTC,
        # unlike the air-purifier's wall-clock field. TimeEntity localizes it.
        now = datetime.datetime.now(datetime.UTC)
        value = datetime.datetime.combine(
            now.date(), start if part == "start" else end, datetime.UTC
        )
        return value if value >= now else value + datetime.timedelta(days=1)

    return read


def _hood_dnd_payload(value, now):
    local = datetime.datetime.combine(now.date(), value, now.tzinfo)
    if local < now:
        local += datetime.timedelta(days=1)
    return local.astimezone(datetime.UTC).time()


AIR_LEVEL_CHECK = Capability(
    href="/airlevelcheck/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="smart_ventilate",
            field="x.com.samsung.da.periodicSensingActivationState",
            icon="mdi:fan-auto",
            entity_category="config",
            exists_fn=lambda rep, resources: (
                rep.get("x.com.samsung.da.periodicSensingActivationState") is not None
            ),
            value_fn=lambda value: str(value).lower() == "on",
            write_fn=_periodic_sensing_write,
        ),
        SelectDesc(
            key="smart_ventilate_mode",
            field="x.com.samsung.da.autoExeState",
            options=_sensing_modes,
            icon="mdi:fan-auto",
            entity_category="config",
            exists_fn=lambda rep, resources: bool(rep.get("x.com.samsung.da.autoExeState")),
            write_fn=_sensing_mode_write,
        ),
        SwitchDesc(
            key="hood_do_not_disturb",
            field="x.com.samsung.da.periodicSensingSkipStatus",
            icon="mdi:sleep",
            entity_category="config",
            exists_fn=lambda rep, resources: (
                rep.get("x.com.samsung.da.periodicSensingSkipStatus") is not None
            ),
            value_fn=lambda value: str(value).lower() == "on",
            write_fn=_skip_status_write,
        ),
        *(
            TimeDesc(
                key=f"hood_do_not_disturb_{part}",
                field="x.com.samsung.da.periodicSensingSkipTime",
                icon=f"mdi:clock-{part}",
                entity_category="config",
                exists_fn=lambda rep, resources: (
                    rep.get("x.com.samsung.da.periodicSensingSkipTime") is not None
                ),
                value_fn=_hood_dnd_read(part),
                payload_fn=_hood_dnd_payload,
                write_fn=_skip_time_write(part),
            )
            for part in ("start", "end")
        ),
        SensorDesc(
            key="air_sensing_interval",
            field="x.com.samsung.da.periodicSensingInterval",
            device_class="duration",
            unit="s",
            icon="mdi:timer-cog",
            entity_category="diagnostic",
            exists_fn=lambda rep, resources: (
                rep.get("x.com.samsung.da.periodicSensingInterval") is not None
            ),
            value_fn=int_or_none,
        ),
        SensorDesc(
            key="air_sensing_state",
            field="x.com.samsung.da.sensingState",
            icon="mdi:radar",
            entity_category="diagnostic",
        ),
        SensorDesc(
            key="last_air_sensing_time",
            field="x.com.samsung.da.lastSensingTime",
            device_class="timestamp",
            entity_category="diagnostic",
            value_fn=epoch_to_utc,
        ),
        SensorDesc(
            key="last_air_sensing_level",
            translation_key="range_hood_last_air_sensing_level",
            device_class="enum",
            options=("good", "normal", "poor", "very_poor"),
            # Kr1-Kr4 track CleanLevel 1-4 in this hood's recorded last results.
            value_fn=lambda value: {
                "Kr1": "good",
                "Kr2": "normal",
                "Kr3": "poor",
                "Kr4": "very_poor",
            }.get(value, value),
            field="x.com.samsung.da.lastSensingLevel",
            icon="mdi:air-filter",
            entity_category="diagnostic",
        ),
    ),
)


AUTO_VENTILATION = Capability(
    href="/autoventilation/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="auto_ventilation_action",
            field="action",
            icon="mdi:fan-auto",
        ),
    ),
)


# ---------------------------------------------------------------------------
# DAWIT 3.0 generation (issue #433) -- see module docstring.
# ---------------------------------------------------------------------------


def _hood_fan_speed_options(resources):
    """The spec resource's full fanSpeedList. `unavailableFanSpeedList` on
    the status rep is deliberately not subtracted: it says what can't be
    selected right now, and this is a sensor that has to be able to render
    whatever the device reports."""
    spec = resources.get("/hood/spec/vs/0") or {}
    return list(spec.get("fanSpeedList") or ())


def _hood_lamp_options(resources):
    spec = resources.get("/hood/spec/vs/0") or {}
    return list(spec.get("lampStateList") or ())


def _hood_filter_alarm(items):
    for item in items or ():
        if not isinstance(item, dict):
            continue
        alarm = item.get("alarm")
        # Falsy (missing, '', JSON null) all mean no alarm -- str(None) is
        # 'none', so a bare `not in ("off", "")` would read a null as active.
        if alarm and str(alarm).lower() != "off":
            return True
    return False


HOOD_STATUS = Capability(
    href="/hood/status/vs/0",
    poll_tier="hot",
    entities=(
        SensorDesc(
            key="hood_fan_speed",
            field="fanSpeed",
            icon="mdi:fan",
            device_class="enum",
            options=_hood_fan_speed_options,
            # Both vocabularies live only on the sibling spec resource, so
            # gate off rather than register an enum sensor with no options
            # on a board reporting status without spec.
            exists_fn=lambda rep, resources: bool(_hood_fan_speed_options(resources)),
        ),
        SensorDesc(
            key="hood_lamp",
            field="lamp",
            icon="mdi:track-light",
            device_class="enum",
            options=_hood_lamp_options,
            exists_fn=lambda rep, resources: bool(_hood_lamp_options(resources)),
        ),
        BinarySensorDesc(
            key="grease_filter_alarm",
            field="filter",
            device_class="problem",
            entity_category="diagnostic",
            icon="mdi:air-filter",
            value_fn=_hood_filter_alarm,
        ),
        # Meaning not confirmed beyond the field name -- raw on/off
        # passthrough, same caution as AUTO_VENTILATION's `action` above.
        BinarySensorDesc(
            key="front_vent_open",
            field="frontVent",
            entity_category="diagnostic",
            icon="mdi:fan",
            value_fn=lambda v: str(v).lower() == "on",
        ),
    ),
)

# Static fan-speed/lamp vocabulary + hood type metadata, read live by
# HOOD_STATUS's two enum sensors -- same pattern as range.py's COOKTOP_SPEC.
HOOD_SPEC = Capability(href="/hood/spec/vs/0")


# Resource plumbing and opaque feature-negotiation fields that are specific to
# this family.  Bare capabilities mark them covered without creating entities.
COVERAGE = [
    Capability(href=href)
    for href in (
        "/power/0",
        "/power/vs/0",
        "/mode/vs/0",
        "/personality/presence/vs/0",
        "/availablecontrolsets/vs/0",
        "/da/softreset/vs/0",
    )
]
