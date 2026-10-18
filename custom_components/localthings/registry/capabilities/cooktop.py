"""Read-only capabilities for Samsung cooktops.

The first verified device is an NA9300K-class five-burner gas cooktop; the
NV9300K and NV8000T induction cooktops share its surface, as does the
NZ36K7570R radiant cooktop over the 8888 interface.  All of them report
burner state as strings embedded in the ``x.com.samsung.da.options`` array on
``/mode/vs/0``.  Heat-producing controls are intentionally not exposed: the
local write contract is unverified and a cooktop must not be remotely ignited
by an automation.
"""

import dataclasses
import re

from ..batch import is_stub_rep
from ..capability import Capability
from ..entities import BinarySensorDesc, SensorDesc
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


def is_empty_legacy_hood_slot(resources):
    """Recognize the NZ36K7570R's captured, unpaired hood placeholder."""
    information = resources.get("/information/vs/0") or {}
    board = str(information.get("x.com.samsung.da.modelNum", "")).split("|", 1)[0]
    options = (resources.get("/mode/vs/0") or {}).get("x.com.samsung.da.options")
    return (
        board == "TP6X_CT_16K"
        and information.get("x.com.samsung.da.description") == "TP6X_CT_16K_HOOD"
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
