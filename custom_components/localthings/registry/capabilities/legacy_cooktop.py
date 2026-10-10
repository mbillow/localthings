"""Read-only options shared by legacy cooktops and their Bluetooth hood bridge.

NZ36K7570R reports this surface over HTTPS. Timer units, flex-coil encoding
and hood writes are unverified; keep their values raw and expose no commands.
"""

import dataclasses

from ..capability import Capability
from ..entities import BinarySensorDesc, SensorDesc
from . import common, cooktop, dishwasher

_OPTIONS = "x.com.samsung.da.options"


def _value(options, prefix):
    return cooktop._option_value(options, prefix)


def _present(prefix):
    return lambda rep, resources: _value(rep.get(_OPTIONS), prefix) is not None


def _on_off(value):
    return {"On": True, "Off": False}.get(value)


def is_hood(resources):
    """The bridge has Bluetooth and fan/lamp options, not burner options."""
    options = resources.get("/mode/vs/0", {}).get(_OPTIONS)
    return all(_value(options, key) is not None for key in ("BTState", "FanSpeed", "Lamp"))


def _hood_present(rep, resources):
    # The unpaired bridge advertises DeviceType_NULL even though /devices
    # lists it. Listing alone must not create a phantom physical hood.
    options = resources.get("/mode/vs/0", {}).get(_OPTIONS)
    model = _value(options, "DeviceType")
    return bool(model and model.upper() not in {"NULL", "UNKNOWN", "NONE"})


def _hood_option_present(prefix):
    return lambda rep, resources: _hood_present(rep, resources) and _present(prefix)(rep, resources)


def _option_sensor(key, prefix, *, exists=None, value_fn=None, **kwargs):
    def read(options):
        value = _value(options, prefix)
        return value_fn(value) if value_fn else value

    return SensorDesc(
        key=key,
        field=_OPTIONS,
        value_fn=read,
        exists_fn=exists or _present(prefix),
        **kwargs,
    )


def _option_binary(key, prefix, **kwargs):
    return BinarySensorDesc(
        key=key,
        field=_OPTIONS,
        value_fn=lambda options: _on_off(_value(options, prefix)),
        exists_fn=_present(prefix),
        **kwargs,
    )


def _burner_attributes(slot):
    def attributes(rep, resources):
        options = rep.get(_OPTIONS)
        values = {
            "power_level": _value(options, f"PowerLevel{slot}"),
            "hot_surface": cooktop._hot_surface(options, slot),
            "pan_size": _value(options, f"PanSize{slot}"),
        }
        return {key: value for key, value in values.items() if value is not None}

    return attributes


def _timer_attributes(rep, resources):
    options = rep.get(_OPTIONS)
    values = {
        "current_raw": cooktop._int_or_none(_value(options, "MainTimerCurrent")),
        "set_raw": cooktop._int_or_none(_value(options, "MainTimerSet")),
    }
    return {key: value for key, value in values.items() if value is not None}


def _with_details(entity):
    if entity.key.startswith("burner_") and entity.key.endswith("_state"):
        slot = int(entity.key.split("_")[1])
        return dataclasses.replace(entity, extra_state_attributes_fn=_burner_attributes(slot))
    if entity.key == "main_timer_state":
        return dataclasses.replace(entity, extra_state_attributes_fn=_timer_attributes)
    return entity


MODE = dataclasses.replace(
    cooktop.COOKTOP_MODE,
    match_fn=lambda rep, resources: not is_hood(resources),
    entities=(
        *(_with_details(e) for e in cooktop.COOKTOP_MODE.entities),
        _option_sensor("cooktop_model", "DeviceType", entity_category="diagnostic"),
        _option_binary("cooktop_sync_flex", "SyncFlex", icon="mdi:vector-link"),
        _option_sensor("cooktop_flex_coil", "FlexCoil", entity_category="diagnostic"),
        _option_binary("cooktop_paused", "Pause", icon="mdi:pause"),
        _option_sensor(
            "main_timer_set",
            "MainTimerSet",
            value_fn=cooktop._int_or_none,
            icon="mdi:timer-cog-outline",
        ),
        *(
            _option_sensor(
                f"burner_{slot}_pan_size",
                f"PanSize{slot}",
                translation_key="burner_pan_size",
                translation_placeholders={"number": str(slot)},
                icon="mdi:circle-double",
            )
            for slot in cooktop._SUPPORTED_OPERATION_SLOTS
        ),
    ),
)

# Reuse the shared diagnosis status without offering an untested start command.
DIAGNOSIS = dataclasses.replace(
    dishwasher.DIAGNOSIS,
    entities=tuple(e for e in dishwasher.DIAGNOSIS.entities if isinstance(e, SensorDesc)),
)

HOOD_MODE = Capability(
    href="/mode/vs/0",
    poll_tier="hot",
    match_fn=lambda rep, resources: is_hood(resources),
    entities=(
        _option_sensor("paired_hood_model", "DeviceType", entity_category="diagnostic"),
        _option_sensor("hood_bluetooth_state", "BTState", entity_category="diagnostic"),
        _option_binary("hood_bluetooth_enabled", "Bluetooth", entity_category="diagnostic"),
        _option_sensor(
            "paired_hood_fan_speed",
            "FanSpeed",
            value_fn=cooktop._int_or_none,
            exists=_hood_option_present("FanSpeed"),
            icon="mdi:fan",
        ),
        BinarySensorDesc(
            key="paired_hood_light",
            field=_OPTIONS,
            device_class="light",
            value_fn=lambda options: _on_off(_value(options, "Lamp")),
            exists_fn=_hood_option_present("Lamp"),
        ),
        *(
            _option_sensor(
                key,
                prefix,
                exists=_hood_option_present(prefix),
                value_fn=cooktop._int_or_none,
                icon="mdi:timer-outline",
            )
            for key, prefix in (
                ("hood_shutoff_timer_set", "ShutOffTimerSet"),
                ("hood_shutoff_timer_current", "ShutOffTimerCurrent"),
            )
        ),
        _option_sensor(
            "hood_shutoff_timer_state",
            "ShutOffTimerState",
            exists=_hood_option_present("ShutOffTimerState"),
            icon="mdi:timer-outline",
        ),
    ),
)

HOOD_POWER = dataclasses.replace(
    cooktop.COOKTOP_POWER,
    match_fn=lambda rep, resources: is_hood(resources),
    entities=tuple(
        dataclasses.replace(e, exists_fn=_hood_present) for e in cooktop.COOKTOP_POWER.entities
    ),
)

HOOD_REMOTE_CONTROL = dataclasses.replace(
    common.REMOTE_CONTROL_VS_FALLBACK,
    entities=tuple(
        dataclasses.replace(e, entity_category="diagnostic")
        for e in common.REMOTE_CONTROL_VS_FALLBACK.entities
    ),
)

HOOD_LISTED = Capability(
    href="/connected/vs/0",
    entities=(
        BinarySensorDesc(
            key="hood_interface_available",
            field="x.com.samsung.da.connected",
            device_class="connectivity",
            entity_category="diagnostic",
            value_fn=_on_off,
        ),
    ),
)
