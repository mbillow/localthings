"""Capabilities for the Samsung stick-vacuum clean/auto-empty station
(models A-VSKR-TP1-22-VS9500AL / A-VSWW-TP1-23-VS9700, issues #131 / #219).

The WiFi/DTLS module lives in the clean station. Older VS9500 dumps
(#131) exposed only dustbag/dustbin/UV-C station state. VS9700 dumps
(#219) additionally expose `/status/stick/vs/0` with the wand's battery
%, cleaning/charging status, and BLE link -- still no suction/room-map
control. Modeled as its own device type -- these hrefs don't overlap with
any existing family (see registry/by_type/__init__.py's docstring).

Resources verified against issue #131 and #219 diagnostics dumps.
"""

from ..capability import Capability
from ..entities import BinarySensorDesc, SelectDesc, SensorDesc, SwitchDesc
from .common import int_or_none
from .common import parse_iso_utc as _parse_iso_utc

DUSTBAG = Capability(
    href="/component/station/dustbag/vs/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="dustbag_full",
            field="x.com.samsung.da.status",
            device_class="problem",
            icon="mdi:bag-personal",
            value_fn=lambda v: v == "full",
        ),
    ),
)

# dustbagUsage/dustbagPrevUsage are raw counters with no capacity/resolution
# field alongside them to normalize into a percentage (unlike the AC/range
# families' filterUsage, which always ships filterCapacity) -- exposed as a
# plain diagnostic count rather than guessing a unit.
DUSTBAG_USAGE = Capability(
    href="/component/station/dustbagusage/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="dustbag_usage",
            field="x.com.samsung.da.dustbagUsage",
            icon="mdi:counter",
            entity_category="diagnostic",
            state_class="total_increasing",
            value_fn=int_or_none,
        ),
    ),
)

DUSTBIN_SETTING = Capability(
    href="/setting/dustbin/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="auto_empty",
            field="x.com.samsung.da.autoEmpty",
            icon="mdi:delete-empty",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["setting", "dustbin", "vs", "0"],
                {"x.com.samsung.da.autoEmpty": "On" if p == "On" else "Off"},
            ),
        ),
        SwitchDesc(
            key="dustbin_auto_close",
            field="x.com.samsung.da.autoClose",
            icon="mdi:door-sliding",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["setting", "dustbin", "vs", "0"],
                {"x.com.samsung.da.autoClose": "On" if p == "On" else "Off"},
            ),
        ),
        SelectDesc(
            key="discharging_time",
            field="x.com.samsung.da.desiredDischargingTime",
            icon="mdi:timer-outline",
            entity_category="config",
            options_field="x.com.samsung.da.supportedDischargingTime",
            write_fn=lambda p, rep, href=None: (
                ["setting", "dustbin", "vs", "0"],
                {"x.com.samsung.da.desiredDischargingTime": p},
            ),
        ),
    ),
)

# stickStatus's exact meaning (docked? powered? charging?) isn't confirmed
# from this single On/Off dump -- exposed as a plain diagnostic sensor
# rather than a binary_sensor, so no polarity/semantic is asserted that
# might be wrong.
CLEANSTATION_STATUS = Capability(
    href="/status/cleanstation/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="cleanstation_status",
            field="x.com.samsung.da.status",
            icon="mdi:home-lightning-bolt",
            entity_category="diagnostic",
        ),
        SensorDesc(
            key="stick_status",
            field="x.com.samsung.da.stickStatus",
            icon="mdi:broom",
            entity_category="diagnostic",
        ),
        SwitchDesc(
            key="uvc_intensive_mode",
            field="x.com.samsung.da.uvcIntensive",
            icon="mdi:lightbulb-on-outline",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["status", "cleanstation", "vs", "0"],
                {"x.com.samsung.da.uvcIntensive": "On" if p == "On" else "Off"},
            ),
        ),
        SensorDesc(
            key="uvc_operation_time",
            field="x.com.samsung.da.uvcOperationTime",
            icon="mdi:timer-sand",
            entity_category="diagnostic",
            value_fn=int_or_none,
        ),
        SensorDesc(
            key="uvc_total_operation_time",
            field="x.com.samsung.da.uvcTotalOperationTime",
            icon="mdi:timer-sand",
            entity_category="diagnostic",
            state_class="total_increasing",
            value_fn=int_or_none,
        ),
        SensorDesc(
            key="uvc_finished_time",
            field="x.com.samsung.da.uvcFinishedTime",
            device_class="timestamp",
            entity_category="diagnostic",
            value_fn=_parse_iso_utc,
        ),
        SensorDesc(
            key="uvc_emitted_time",
            field="x.com.samsung.da.emittedTime",
            device_class="timestamp",
            entity_category="diagnostic",
            value_fn=_parse_iso_utc,
        ),
    ),
)

# Wand body state reported through the station (VS9700 / issue #219). Absent
# on the older VS9500 dump (#131) -- discovery drops entities when the href
# is missing.
STICK_BODY = Capability(
    href="/status/stick/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="battery",
            field="x.com.samsung.da.stickbattery",
            device_class="battery",
            state_class="measurement",
            unit="%",
            value_fn=int_or_none,
        ),
        BinarySensorDesc(
            key="battery_charging",
            field="x.com.samsung.da.stickcleaningstatus",
            device_class="battery_charging",
            value_fn=lambda v: v == "Charging",
        ),
        SensorDesc(
            key="stick_cleaning_status",
            field="x.com.samsung.da.stickcleaningstatus",
            icon="mdi:vacuum",
            entity_category="diagnostic",
        ),
        SensorDesc(
            key="stick_operation_mode",
            field="x.com.samsung.da.stickoperationmode",
            icon="mdi:broom",
            entity_category="diagnostic",
        ),
        BinarySensorDesc(
            key="stick_ble_connected",
            field="x.com.samsung.da.stickbleconnection",
            device_class="connectivity",
            entity_category="diagnostic",
            value_fn=lambda v: v == "On",
        ),
    ),
)
