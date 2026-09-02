"""Capabilities for Samsung AHD-WW-TP1-22 range hoods.

The verified device exposes its fan, two-level work lamp, washable-filter
status, and particulate sensors as distinct local OCF resources.  Fan power
and speed are combined into one HA fan entity by ``fan.py``; lamp power and
brightness remain separate controls because the device advertises them as two
independent fields.

DAWIT 3.0 generation built-in vent hood (issue #433, combi microwave):
a different board reports the vent fan/lamp/filter as one bare-field
`/hood/status/vs/0` (no `x.com.samsung.da.` prefix, no `.hood.` namespace
-- unrelated to `/hood/fanspeed/vs/0` above, which this board doesn't
carry), with the static fan-speed/lamp-state vocabulary split into a
sibling `/hood/spec/vs/0`. `HOOD_STATUS`/`HOOD_SPEC` below are that
generation's capabilities. Modeled as selects rather than a composite
FanDesc: unlike `HOOD_FAN`'s supportedFanSpeed (colocated on its own
href), this board's full speed/lamp vocabularies live only on the sibling
spec resource, and FanDesc/fan.py's LocalThingsRangeHoodFan has no hook
for a cross-href option list -- same shape as range.py's
`_power_level_options` reading `/cooktop/spec/vs/0` for a SelectDesc.
"""

from ..batch import is_stub_rep
from ..capability import Capability
from ..entities import (
    BinarySensorDesc,
    ButtonDesc,
    FanDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
)
from .common import epoch_to_utc, int_or_none, sensor_item_value


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
            field="x.com.samsung.da.items",
            icon="mdi:alert",
            entity_category="diagnostic",
            value_fn=_active_alarm_codes,
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


AIR_QUALITY = Capability(
    href="/sensors/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="clean_level",
            field="x.com.samsung.da.items",
            icon="mdi:air-filter",
            value_fn=lambda items: sensor_item_value(items, "CleanLevel"),
        ),
        SensorDesc(
            key="dust",
            field="x.com.samsung.da.items",
            value_fn=lambda items: sensor_item_value(items, "Dust"),
        ),
        SensorDesc(
            key="fine_dust",
            field="x.com.samsung.da.items",
            value_fn=lambda items: sensor_item_value(items, "FineDust"),
        ),
        SensorDesc(
            key="super_fine_dust",
            field="x.com.samsung.da.items",
            value_fn=lambda items: sensor_item_value(items, "SuperFineDust"),
        ),
    ),
)


AIR_LEVEL_CHECK = Capability(
    href="/airlevelcheck/vs/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="periodic_air_sensing",
            field="x.com.samsung.da.periodicSensingActivationState",
            icon="mdi:radar",
            entity_category="diagnostic",
            value_fn=lambda value: str(value).lower() == "on",
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
            field="x.com.samsung.da.lastSensingLevel",
            icon="mdi:air-filter",
            entity_category="diagnostic",
        ),
        SensorDesc(
            key="automatic_ventilation_state",
            field="x.com.samsung.da.autoExeState",
            icon="mdi:fan-auto",
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


def _hood_status_fan_speed_options(resources):
    """Live fanSpeedList from the sibling spec resource, minus whatever
    unavailableFanSpeedList currently excludes. Issue #433's dump reports
    unavailableFanSpeedList as [''] (nothing excluded) -- the empty-string
    placeholder is filtered out so it can't accidentally exclude a real
    speed code."""
    spec = resources.get("/hood/spec/vs/0") or {}
    status = resources.get("/hood/status/vs/0") or {}
    unavailable = {str(v) for v in (status.get("unavailableFanSpeedList") or ()) if v}
    return [s for s in (spec.get("fanSpeedList") or []) if s not in unavailable]


def _hood_status_fan_speed_write(p, rep, href=None):
    """Direct single-field PUT -- unconfirmed, no live device to verify
    against (issue #433's reporter is asked to try this)."""
    if not isinstance(p, str):
        return None
    return ["hood", "status", "vs", "0"], {"fanSpeed": p}


def _hood_status_lamp_options(resources):
    spec = resources.get("/hood/spec/vs/0") or {}
    return list(spec.get("lampStateList") or [])


def _hood_status_lamp_write(p, rep, href=None):
    if not isinstance(p, str):
        return None
    return ["hood", "status", "vs", "0"], {"lamp": p}


def _hood_status_filter_alarm(items):
    for item in items or ():
        if not isinstance(item, dict):
            continue
        alarm = item.get("alarm")
        # Falsy (missing, '', explicit JSON null) all mean no alarm --
        # str(None).lower() is 'none', which isn't 'off' either, so a
        # bare `not in ("off", "")` check would misread a null alarm
        # field as active.
        if alarm and str(alarm).lower() != "off":
            return True
    return False


HOOD_STATUS = Capability(
    href="/hood/status/vs/0",
    poll_tier="hot",
    entities=(
        # No separate power resource on this board (same shape as
        # HOOD_FAN's combi-appliance fallback) -- 'off' is itself a
        # fanSpeedList option, so a select needs no dedicated off/on
        # toggle.
        SelectDesc(
            key="hood_fan_speed",
            field="fanSpeed",
            icon="mdi:fan",
            options=_hood_status_fan_speed_options,
            # Both option lists come only from the sibling spec resource
            # (see the docstrings above) -- gate off rather than register
            # a permanently empty, unusable select on a device reporting
            # /hood/status/vs/0 without its /hood/spec/vs/0 counterpart.
            exists_fn=lambda rep, resources: bool(_hood_status_fan_speed_options(resources)),
            write_fn=_hood_status_fan_speed_write,
        ),
        SelectDesc(
            key="hood_lamp",
            field="lamp",
            icon="mdi:track-light",
            options=_hood_status_lamp_options,
            exists_fn=lambda rep, resources: bool(_hood_status_lamp_options(resources)),
            write_fn=_hood_status_lamp_write,
        ),
        BinarySensorDesc(
            key="grease_filter_alarm",
            field="filter",
            device_class="problem",
            entity_category="diagnostic",
            icon="mdi:air-filter",
            value_fn=_hood_status_filter_alarm,
        ),
        # Meaning not confirmed beyond the field name itself -- exposed as
        # a plain raw on/off passthrough rather than an interpreted
        # device_class, same caution as AUTO_VENTILATION's bare 'action'
        # field above.
        BinarySensorDesc(
            key="front_vent_open",
            field="frontVent",
            entity_category="diagnostic",
            icon="mdi:fan",
            value_fn=lambda v: str(v).lower() == "on",
        ),
    ),
)

# Static fan-speed/lamp-state vocabulary + hood type metadata, read live by
# HOOD_STATUS's two selects (options=<callable>) rather than exposed
# through its own entity -- same pattern as range.py's COOKTOP_SPEC.
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
