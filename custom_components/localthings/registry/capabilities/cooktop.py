"""Read-only capabilities for Samsung cooktops.

The first verified device is an NA9300K-class five-burner gas cooktop; the
NV9300K and NV8000T induction cooktops share its surface.  All of them report
burner state as strings embedded in the ``x.com.samsung.da.options`` array on
``/mode/vs/0``.  Heat-producing controls are intentionally not exposed: the
local write contract is unverified and a cooktop must not be remotely ignited
by an automation.
"""

import re

from ..batch import is_stub_rep
from ..capability import Capability
from ..entities import BinarySensorDesc, SensorDesc

_INACTIVE_OPERATION_STATES = {"Off", "Ready"}


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


def _slot_option_present(prefix, slot):
    """exists_fn for a per-slot `<prefix><slot>_` option on /mode/vs/0."""
    return lambda rep, resources: (
        is_stub_rep(rep)
        or _option_value(rep.get("x.com.samsung.da.options"), f"{prefix}{slot}") is not None
    )


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
        # NV8000T); the gas NA9300K reports neither. Raw values: only idle
        # readings have been seen (PowerLevel Off/0, HotSurface Normal), so
        # no value is interpreted as "on" or "hot" yet.
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
            SensorDesc(
                key=f"burner_{slot}_hot_surface",
                field="x.com.samsung.da.options",
                translation_key="burner_hot_surface",
                translation_placeholders={"number": str(slot)},
                icon="mdi:heat-wave",
                value_fn=lambda options, slot=slot: _option_value(options, f"HotSurface{slot}"),
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
    ),
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
