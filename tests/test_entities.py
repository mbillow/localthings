from custom_components.localthings.registry.by_type import _REGISTRY_BY_KEY
from custom_components.localthings.registry.entities import (
    PLATFORM_OF,
    BinarySensorDesc,
    ButtonDesc,
    ClimateDesc,
    FanDesc,
    LightDesc,
    NumberDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
    TimeDesc,
)


def test_value_fn_defaults_to_identity():
    d = SensorDesc(key="power", field="x.com.samsung.da.instantaneousPower")
    assert d.value_fn(42) == 42


def test_descriptions_are_frozen():
    d = SensorDesc(key="power", field="f")
    try:
        # setattr() through a variable name (not `d.key = "other"`, and not a
        # literal setattr(d, "key", ...) -- ruff's B010 rewrites that back to
        # attribute-assignment syntax), so this reaches the same
        # frozen-dataclass __setattr__ at runtime without ty statically
        # flagging the (deliberately illegal) direct attribute assignment.
        attr = "key"
        setattr(d, attr, "other")
    except Exception as e:
        assert "frozen" in str(type(e)).lower() or "cannot" in str(e).lower()
    else:
        raise AssertionError("expected frozen dataclass")


def test_platform_mapping_covers_all_subclasses():
    assert PLATFORM_OF[SensorDesc] == "sensor"
    assert PLATFORM_OF[BinarySensorDesc] == "binary_sensor"
    assert PLATFORM_OF[SelectDesc] == "select"
    assert PLATFORM_OF[SwitchDesc] == "switch"
    assert PLATFORM_OF[LightDesc] == "light"
    assert PLATFORM_OF[ButtonDesc] == "button"
    assert PLATFORM_OF[NumberDesc] == "number"
    assert PLATFORM_OF[TimeDesc] == "time"
    assert PLATFORM_OF[ClimateDesc] == "climate"
    assert PLATFORM_OF[FanDesc] == "fan"


def test_select_carries_options_and_write_fn():
    d = SelectDesc(
        key="sound",
        field="mode",
        options=("voice", "tone", "mute"),
        write_fn=lambda p, rep: (["settings", "sound", "mode", "vs", "0"], {"mode": p}),
    )
    assert d.write_fn is not None
    assert d.write_fn("tone", {}) == (["settings", "sound", "mode", "vs", "0"], {"mode": "tone"})


def test_read_only_sensor_platforms_do_not_use_config_category():
    """Home Assistant rejects config-category Sensor/BinarySensor entities.

    Traverse device registries rather than the smaller global capability set
    so device-specific and pattern capabilities stay covered as the registry
    grows.
    """
    violations = set()

    for registry in _REGISTRY_BY_KEY.values():
        capabilities = [
            capability for group in registry.capabilities.values() for capability in group
        ]
        capabilities.extend(registry.pattern_capabilities)

        for capability in capabilities:
            href = capability.href or capability.href_prefix or "<pattern>"
            for desc in capability.entities:
                if (
                    isinstance(desc, (SensorDesc, BinarySensorDesc))
                    and desc.entity_category == "config"
                ):
                    violations.add((registry.name, href, desc.key))

    assert violations == set()
