"""Capabilities for Samsung cooktops.

The first verified device is an NA9300K-class five-burner gas cooktop; the
NV9300K and NV8000T induction cooktops share its surface, as does the
NZ36K7570R radiant cooktop over the 8888 interface.  All of them report
burner state as strings embedded in the ``x.com.samsung.da.options`` array on
``/mode/vs/0``.  Heat-producing controls are intentionally not exposed: the
local write contract is unverified and a cooktop must not be remotely ignited
by an automation. The one exception is the NZ30K7570RS's paired hood, whose
fan and light writes were verified on hardware (issue #676).
"""

import dataclasses
import re

from ..batch import is_stub_rep
from ..capability import Capability
from ..entities import BinarySensorDesc, FanDesc, LightDesc, NumberDesc, SensorDesc
from . import dishwasher

# NotSupported marks a slot the board advertises but has no burner for:
# issue #621's Dacor reports it for slot 2 of five real burners.
_INACTIVE_OPERATION_STATES = {"Off", "Ready", "NotSupported"}


def _option_value(options, prefix):
    """Return the value from the first ``<prefix>_<value>`` option."""
    marker = prefix + "_"
    for option in options or ():
        if isinstance(option, str) and option.startswith(marker):
            return option[len(marker) :]
    return None


def _operation_slots(options) -> tuple[int, ...]:
    """Return the numeric burner slots advertised in an options array."""
    slots = set()
    for option in options or ():
        if not isinstance(option, str):
            continue
        match = re.match(r"^OperationState(\d+)_", option)
        if match:
            slots.add(int(match.group(1)))
    return tuple(sorted(slots))


def _any_burner_active(options):
    """True when any advertised burner slot is not idle."""
    states = [_option_value(options, f"OperationState{slot}") for slot in _operation_slots(options)]
    states = [state for state in states if state is not None]
    return any(state not in _INACTIVE_OPERATION_STATES for state in states)


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


COOKTOP_POWER = Capability(
    href="/power/vs/0",
    poll_tier="hot",
    entities=(
        BinarySensorDesc(
            key="power_state",
            field="x.com.samsung.da.power",
            device_class="power",
            icon="mdi:stove",
            value_fn=lambda value: str(value).lower() == "on",
        ),
    ),
)


# The verified NA9300K exposes physical slots 0, 1, 3, 4, and 5.  Declare a
# generous static superset so variants with other layouts are not silently
# omitted; exists_fn hides every slot the live options array does not report.
# Replace this bound with data-driven entity generation when #31 lands.
_SUPPORTED_OPERATION_SLOTS = tuple(range(8))


def _hot_surface(options, slot):
    value = _option_value(options, f"HotSurface{slot}")
    return None if value is None else value.lower() != "normal"


def _option_present(prefix):
    """exists_fn for a single `<prefix>_` option on /mode/vs/0."""
    return lambda rep, resources: (
        is_stub_rep(rep) or _option_value(rep.get("x.com.samsung.da.options"), prefix) is not None
    )


def _slot_option_present(prefix, slot):
    present = _option_present(f"{prefix}{slot}")
    # A NotSupported slot has no burner behind it (issue #621).
    return lambda rep, resources: (
        present(rep, resources)
        and (
            _option_value(rep.get("x.com.samsung.da.options"), f"OperationState{slot}")
            != "NotSupported"
        )
    )


def _option_on(options, prefix):
    return {"On": True, "Off": False}.get(_option_value(options, prefix))


COOKTOP_MODE = Capability(
    href="/mode/vs/0",
    poll_tier="hot",
    entities=(
        BinarySensorDesc(
            key="any_burner_active",
            field="x.com.samsung.da.options",
            device_class="running",
            icon="mdi:fire",
            value_fn=_any_burner_active,
        ),
        *(
            SensorDesc(
                key=f"burner_{slot}_state",
                field="x.com.samsung.da.options",
                translation_key="burner_state",
                translation_placeholders={"number": str(slot)},
                icon="mdi:gas-burner",
                value_fn=lambda options, slot=slot: _option_value(options, f"OperationState{slot}"),
                exists_fn=_slot_option_present("OperationState", slot),
            )
            for slot in _SUPPORTED_OPERATION_SLOTS
        ),
        # Induction boards only (issue #508's NV9300K and issue #314's
        # NV8000T); the gas NA9300K reports neither. Power level stays raw:
        # only idle readings have been seen (Off on one board, 0 on the
        # other). Hot surface reads like range.py's hotSurfaceState: anything
        # but Normal is hot.
        *(
            SensorDesc(
                key=f"burner_{slot}_power_level",
                field="x.com.samsung.da.options",
                translation_key="burner_power_level",
                translation_placeholders={"number": str(slot)},
                icon="mdi:gauge",
                value_fn=lambda options, slot=slot: _option_value(options, f"PowerLevel{slot}"),
                exists_fn=_slot_option_present("PowerLevel", slot),
            )
            for slot in _SUPPORTED_OPERATION_SLOTS
        ),
        *(
            BinarySensorDesc(
                key=f"burner_{slot}_hot_surface",
                field="x.com.samsung.da.options",
                device_class="heat",
                translation_key="burner_hot_surface",
                translation_placeholders={"number": str(slot)},
                value_fn=lambda options, slot=slot: _hot_surface(options, slot),
                exists_fn=_slot_option_present("HotSurface", slot),
            )
            for slot in _SUPPORTED_OPERATION_SLOTS
        ),
        SensorDesc(
            key="main_timer_state",
            field="x.com.samsung.da.options",
            icon="mdi:timer-outline",
            value_fn=lambda options: _option_value(options, "MainTimerState"),
        ),
        SensorDesc(
            key="main_timer_current",
            field="x.com.samsung.da.options",
            icon="mdi:timer-sand",
            enabled_default=False,
            value_fn=lambda options: _int_or_none(_option_value(options, "MainTimerCurrent")),
        ),
        # These values stay raw on unverified boards; the legacy radiant
        # profile below supplies the NZ36K7570R's confirmed meanings.
        SensorDesc(
            key="main_timer_set",
            field="x.com.samsung.da.options",
            icon="mdi:timer-cog-outline",
            value_fn=lambda options: _int_or_none(_option_value(options, "MainTimerSet")),
            exists_fn=_option_present("MainTimerSet"),
        ),
        BinarySensorDesc(
            key="cooktop_paused",
            field="x.com.samsung.da.options",
            icon="mdi:pause",
            value_fn=lambda options: _option_on(options, "Pause"),
            exists_fn=_option_present("Pause"),
        ),
        BinarySensorDesc(
            key="cooktop_sync_control",
            field="x.com.samsung.da.options",
            icon="mdi:vector-link",
            value_fn=lambda options: _option_on(options, "SyncFlex"),
            exists_fn=_option_present("SyncFlex"),
        ),
        SensorDesc(
            key="cooktop_device_type",
            field="x.com.samsung.da.options",
            entity_category="diagnostic",
            value_fn=lambda options: _option_value(options, "DeviceType"),
            exists_fn=_option_present("DeviceType"),
        ),
        SensorDesc(
            key="cooktop_flex_coil",
            field="x.com.samsung.da.options",
            entity_category="diagnostic",
            value_fn=lambda options: _option_value(options, "FlexCoil"),
            exists_fn=_option_present("FlexCoil"),
        ),
        *(
            SensorDesc(
                key=f"burner_{slot}_pan_size",
                field="x.com.samsung.da.options",
                translation_key="burner_pan_size",
                translation_placeholders={"number": str(slot)},
                icon="mdi:circle-double",
                value_fn=lambda options, slot=slot: _option_value(options, f"PanSize{slot}"),
                exists_fn=_slot_option_present("PanSize", slot),
            )
            for slot in _SUPPORTED_OPERATION_SLOTS
        ),
    ),
)


def is_legacy_radiant_cooktop(rep, resources):
    information = resources.get("/information/vs/0") or {}
    board = str(information.get("x.com.samsung.da.modelNum", "")).split("|", 1)[0]
    model = _option_value(rep.get("x.com.samsung.da.options"), "DeviceType")
    # Only this reported identity has verified timer units and ring-size meanings.
    return board == "TP6X_CT_16K" and model == "NV9300K-/AA2"


def is_legacy_hood_slot(resources):
    """The TP6X cooktop's /device/1, which holds its Bluetooth hood, paired or not.

    The description names the board. The board's own modelNum comes from a
    linked read that a slow cooktop can skip (PR #677), so it only has to
    agree when present."""
    information = resources.get("/information/vs/0") or {}
    board = str(information.get("x.com.samsung.da.modelNum", "")).split("|", 1)[0]
    return information.get("x.com.samsung.da.description") == "TP6X_CT_16K_HOOD" and board in (
        "",
        "TP6X_CT_16K",
    )


def paired_hood_model(resources):
    """The hood's own model, such as NK9300KM, when the TP6X slot holds one.
    The unpaired NZ36K7570R slot reports DeviceType_NULL (#642)."""
    if not is_legacy_hood_slot(resources):
        return None
    options = (resources.get("/mode/vs/0") or {}).get("x.com.samsung.da.options")
    model = _option_value(options, "DeviceType")
    return model if model and model != "NULL" else None


def paired_hood_running(resources):
    """Whether the paired hood's fan runs; the slot's power is the hood's (#676)."""
    power = resources.get("/power/vs/0") or {}
    return str(power.get("x.com.samsung.da.power", "")).lower() == "on"


def is_empty_legacy_hood_slot(resources):
    """Recognize the NZ36K7570R's captured, unpaired hood placeholder."""
    options = (resources.get("/mode/vs/0") or {}).get("x.com.samsung.da.options")
    return (
        is_legacy_hood_slot(resources)
        and (resources.get("/power/vs/0") or {}).get("x.com.samsung.da.power") == "Off"
        and all(
            _option_value(options, key) == value
            for key, value in {
                "DeviceType": "NULL",
                "BTState": "Idle",
                "Bluetooth": "Off",
                "FanSpeed": "0",
                "Lamp": "Off",
                "ShutOffTimerSet": "0",
                "ShutOffTimerCurrent": "0",
                "ShutOffTimerState": "Ready",
            }.items()
        )
    )


def _ring_size(value):
    return value.lower() if value in ("Idle", "Single", "Dual", "Triple") else value


def _legacy_radiant_entity(desc):
    if desc.key == "cooktop_paused":
        # NZ36K7570R lacks All-Pause but still reports Pause_Off.
        return dataclasses.replace(desc, enabled_default=False)
    if not isinstance(desc, SensorDesc):
        return desc
    if desc.key == "main_timer_set":
        # NZ36K7570R reports remaining minutes in both timer fields; retain
        # the existing enabled entity (docs/investigations/legacy-cooktop.md).
        return dataclasses.replace(
            desc, translation_key="cooktop_timer_remaining", unit="min", device_class="duration"
        )
    if desc.key == "main_timer_state":
        return dataclasses.replace(desc, translation_key="cooktop_timer_status")
    if desc.key == "cooktop_flex_coil":
        return dataclasses.replace(desc, enabled_default=False)
    if desc.translation_key == "burner_pan_size":
        return dataclasses.replace(
            desc,
            translation_key="cooktop_ring_size",
            device_class="enum",
            options=("idle", "single", "dual", "triple"),
            value_fn=lambda options: _ring_size(desc.value_fn(options)),
        )
    return desc


LEGACY_RADIANT_MODE = dataclasses.replace(
    COOKTOP_MODE,
    match_fn=is_legacy_radiant_cooktop,
    entities=tuple(
        _legacy_radiant_entity(desc)
        for desc in COOKTOP_MODE.entities
        if desc.key != "main_timer_current"
    ),
)


# The diagnosis status only; the start command is untested on a cooktop.
DIAGNOSIS = dataclasses.replace(
    dishwasher.DIAGNOSIS,
    entities=tuple(e for e in dishwasher.DIAGNOSIS.entities if isinstance(e, SensorDesc)),
)


COOKTOP_CONNECTED = Capability(
    href="/connected/vs/0",
    poll_tier="warm",
    entities=(
        BinarySensorDesc(
            key="cloud_connected",
            field="x.com.samsung.da.connected",
            device_class="connectivity",
            entity_category="diagnostic",
            value_fn=lambda value: str(value).lower() == "on",
        ),
    ),
)


PAIRED_HOOD_STATUS = Capability(
    href="/bluetooth/hood/status/vs/0",
    poll_tier="hot",
    entities=(
        BinarySensorDesc(
            key="paired_hood_connected",
            field="connectionState",
            device_class="connectivity",
            value_fn=lambda value: str(value).lower() == "connected",
        ),
        BinarySensorDesc(
            key="paired_hood_power",
            field="power",
            device_class="running",
            value_fn=lambda value: str(value).lower() == "on",
        ),
        SensorDesc(
            key="paired_hood_fan_speed",
            field="fanSpeed",
            icon="mdi:fan",
            value_fn=_int_or_none,
        ),
        BinarySensorDesc(
            key="paired_hood_light",
            field="lampState",
            device_class="light",
            value_fn=lambda value: str(value).lower() == "on",
        ),
        SensorDesc(
            key="paired_hood_model",
            field="micomModelId",
            icon="mdi:information-outline",
            entity_category="diagnostic",
            enabled_default=False,
        ),
        SensorDesc(
            key="paired_hood_firmware",
            field="firmwareVersion",
            icon="mdi:chip",
            entity_category="diagnostic",
            enabled_default=False,
        ),
    ),
)


# The NZ30K7570RS's paired NK9300KM hood (issue #676) reports its state as
# options on its own /mode/vs/0, unlike the burnerList boards'
# /bluetooth/hood/status/vs/0 above, so it reuses those entity names where
# the meaning matches. Read from captures taken idle, running and after
# shutoff: Bluetooth_On is read as the link to the cooktop because the
# unpaired slot reports Off, the lamp reported Lamp_On
# when lit, and the shutoff timer counts in minutes (9 to 4 over five
# minutes). FanSpeed and Lamp keep their values after the hood turns off; the
# light runs on its own, but whether the fan is running comes from the slot's
# /power/vs/0, so FanSpeed reads as the fan's setting.
#
# The hood offers Low, Med, High and Boost, captured as 1 to 4, with 2
# confirmed as Med and 4 as Boost. 0 is the unpaired NZ36K7570R slot's value.
_HOOD_FAN_SETTINGS = {"0": "off", "1": "low", "2": "medium", "3": "high", "4": "boost"}
HOOD_FAN_SPEEDS = ("1", "2", "3", "4")
# The hood keeps the minutes in one byte (256 wraps to 0, which cancels) and
# its two-digit panel misreads 100 to 255, so 99 is the panel's own top step.
HOOD_TIMER_MAX_MINUTES = 99


# Both writes go to the hood's own slot as one-token aggregate PUTs, verified
# on the reporter's hood: power On starts the fan on Low whatever FanSpeed
# held, and a FanSpeed write lands only while the fan runs (fan.py sends it
# after power On). The light switches with the fan on or off.
def _hood_fan_write(payload, rep, href=None):
    kind, value = payload
    if kind == "power":
        return ["power", "vs", "0"], {"x.com.samsung.da.power": "On" if value else "Off"}
    if kind == "speed" and value in HOOD_FAN_SPEEDS:
        return ["mode", "vs", "0"], {"x.com.samsung.da.options": [f"FanSpeed_{value}"]}
    return None


def _hood_light_write(on, rep, href=None):
    return ["mode", "vs", "0"], {"x.com.samsung.da.options": ["Lamp_On" if on else "Lamp_Off"]}


# ShutOffTimerSet_N sets N minutes and 0 cancels, leaving the fan running,
# verified on a second NK9300KM (PR #677). It only lands while the fan runs,
# so number.py refuses a timer while it's off. Writing ShutOffTimerState_Ready
# reads back Ready but doesn't stop the countdown, so it isn't used to cancel.
def _hood_timer_write(value, rep, href=None):
    if value == int(value) and 0 <= value <= HOOD_TIMER_MAX_MINUTES:
        return ["mode", "vs", "0"], {"x.com.samsung.da.options": [f"ShutOffTimerSet_{int(value)}"]}
    return None


def _hood_timer_optimistic(body):
    """The hood reports a new timer as running right after the write (#677),
    and the entity reads Current, so carry it rather than show the old value
    for the whole settle window."""
    options = body.get("x.com.samsung.da.options")
    minutes = _option_value(options, "ShutOffTimerSet")
    if minutes is None:
        return body
    state = "Ready" if minutes == "0" else "Run"
    return {
        **body,
        "x.com.samsung.da.options": [
            *options,
            f"ShutOffTimerCurrent_{minutes}",
            f"ShutOffTimerState_{state}",
        ],
    }


def _hood_slot_in_use(rep, resources):
    # The empty slot's own exclusion, so the cooktop registry needn't wrap
    # these and the platforms can recognize them by identity.
    return is_legacy_hood_slot(resources) and not is_empty_legacy_hood_slot(resources)


LEGACY_HOOD_POWER = Capability(
    href="/power/vs/0",
    poll_tier="hot",
    match_fn=_hood_slot_in_use,
    entities=(
        FanDesc(
            key="paired_hood_fan",
            field="x.com.samsung.da.power",
            write_fn=_hood_fan_write,
        ),
    ),
)

LEGACY_HOOD_MODE = Capability(
    href="/mode/vs/0",
    poll_tier="hot",
    match_fn=_hood_slot_in_use,
    model_fn=paired_hood_model,
    entities=(
        BinarySensorDesc(
            key="paired_hood_connected",
            field="x.com.samsung.da.options",
            device_class="connectivity",
            value_fn=lambda options: _option_on(options, "Bluetooth"),
            exists_fn=_option_present("Bluetooth"),
        ),
        SensorDesc(
            key="paired_hood_fan_setting",
            field="x.com.samsung.da.options",
            icon="mdi:fan",
            device_class="enum",
            options=tuple(_HOOD_FAN_SETTINGS.values()),
            value_fn=lambda options: _HOOD_FAN_SETTINGS.get(_option_value(options, "FanSpeed")),
            exists_fn=_option_present("FanSpeed"),
        ),
        LightDesc(
            key="paired_hood_light",
            field="x.com.samsung.da.options",
            icon="mdi:light-recessed",
            value_fn=lambda options: _option_on(options, "Lamp"),
            write_fn=_hood_light_write,
            exists_fn=_option_present("Lamp"),
        ),
        SensorDesc(
            key="paired_hood_model",
            field="x.com.samsung.da.options",
            icon="mdi:information-outline",
            entity_category="diagnostic",
            enabled_default=False,
            value_fn=lambda options: _option_value(options, "DeviceType"),
            exists_fn=_option_present("DeviceType"),
        ),
        SensorDesc(
            key="paired_hood_shutoff_timer_state",
            field="x.com.samsung.da.options",
            icon="mdi:timer-outline",
            value_fn=lambda options: _option_value(options, "ShutOffTimerState"),
            exists_fn=_option_present("ShutOffTimerState"),
        ),
        SensorDesc(
            key="paired_hood_shutoff_timer_set",
            field="x.com.samsung.da.options",
            icon="mdi:timer-cog-outline",
            unit="min",
            device_class="duration",
            enabled_default=False,
            value_fn=lambda options: _int_or_none(_option_value(options, "ShutOffTimerSet")),
            exists_fn=_option_present("ShutOffTimerSet"),
        ),
        # Reads the minutes left, which is 0 with no timer running; Set
        # keeps its last value after the timer ends.
        NumberDesc(
            key="paired_hood_shutoff_timer",
            field="x.com.samsung.da.options",
            icon="mdi:timer-sand",
            unit="min",
            device_class="duration",
            native_min=0,
            native_max=HOOD_TIMER_MAX_MINUTES,
            step=1,
            value_fn=lambda options: _int_or_none(_option_value(options, "ShutOffTimerCurrent")),
            write_fn=_hood_timer_write,
            optimistic_fn=_hood_timer_optimistic,
            exists_fn=_option_present("ShutOffTimerCurrent"),
        ),
    ),
)
