"""Swing controls follow each unit's available options."""

import pytest
from homeassistant.components.climate import ClimateEntityFeature

from custom_components.localthings.climate import LocalThingsClimate
from custom_components.localthings.registry.entities import ClimateDesc
from tests.test_subdevice_discovery import _coordinator, _discover, _discover_with


@pytest.mark.parametrize(
    "fixture",
    [
        "airconditioner_tp2x_rac_20k",
        "airconditioner_windfree_oscillation",
        "airconditioner_artik051_dongle_fac_18k",
    ],
)
async def test_swing_fallback_channels_keep_their_controls(hass, fixture):
    coordinator = _coordinator(hass)
    await _discover(coordinator, fixture)
    entities = [
        LocalThingsClimate(coordinator, bound)
        for bound in coordinator.bound
        if isinstance(bound.desc, ClimateDesc)
    ]
    assert entities
    for entity in entities:
        assert entity.swing_modes
        assert entity.supported_features & ClimateEntityFeature.SWING_MODE


async def test_swing_feature_updates_when_options_arrive_later(hass):
    coordinator = _coordinator(hass)
    await _discover(coordinator, "airconditioner_fac_bora")
    bound = next(b for b in coordinator.bound if isinstance(b.desc, ClimateDesc))
    entity = LocalThingsClimate(coordinator, bound)
    entity.hass = hass
    assert entity.swing_modes == []
    before = entity.supported_features
    assert not before & ClimateEntityFeature.SWING_MODE
    capabilities = entity.capability_attributes
    assert capabilities is not None
    assert "swing_modes" not in capabilities
    assert "swing_mode" not in entity.state_attributes

    coordinator._observe.apply(
        "/wind/direction/vs/0",
        {
            "x.com.samsung.da.modes": "Fix",
            "x.com.samsung.da.supportedModes": ["Fix", "Up_And_Low"],
        },
        source="poll",
    )
    assert entity.swing_modes == ["off", "vertical"]
    assert entity.supported_features == before | ClimateEntityFeature.SWING_MODE
    capabilities = entity.capability_attributes
    assert capabilities is not None
    assert capabilities["swing_modes"] == ["off", "vertical"]
    assert entity.state_attributes["swing_mode"] == "off"

    # Partial poll/Observe updates omit supportedModes, not withdraw them.
    coordinator._observe.apply(
        "/wind/direction/vs/0", {"x.com.samsung.da.modes": "Up_And_Low"}, source="observe"
    )
    capabilities = entity.capability_attributes
    assert capabilities is not None
    assert capabilities["swing_modes"] == ["off", "vertical"]
    assert entity.supported_features == before | ClimateEntityFeature.SWING_MODE
    assert entity.state_attributes["swing_mode"] == "vertical"


async def test_swing_features_are_scoped_to_each_subdevice(hass):
    subdevice = "11111111-2222-3333-aaaa-555555555555"
    mode = {
        "x.com.samsung.da.modes": ["Cool"],
        "x.com.samsung.da.supportedModes": ["Cool", "Dry", "Wind"],
    }
    main = {
        "/information/vs/0": {"x.com.samsung.da.modelNum": "R18_BIXBY_FAC_TIME_23K"},
        "/subdevices/vs/0": {"x.com.samsung.da.subdeviceIdList": [subdevice]},
        "/mode/vs/0": mode,
        "/power/vs/0": {"x.com.samsung.da.power": "Off"},
    }
    wall = {
        "/mode/vs/0": mode,
        "/power/vs/0": {"x.com.samsung.da.power": "Off"},
        "/wind/direction/vs/0": {
            "x.com.samsung.da.modes": "Fix",
            "x.com.samsung.da.supportedModes": ["Fix", "Up_And_Low"],
        },
    }
    coordinator = _coordinator(hass)
    await _discover_with(
        coordinator,
        main,
        [],
        {f"/{subdevice}/device/0": [{"href": href, "rep": rep} for href, rep in wall.items()]},
    )
    entities = {
        bound.subdevice.key: LocalThingsClimate(coordinator, bound)
        for bound in coordinator.bound
        if isinstance(bound.desc, ClimateDesc)
    }
    assert set(entities) == {"", subdevice}
    assert not entities[""].supported_features & ClimateEntityFeature.SWING_MODE
    assert entities[subdevice].supported_features & ClimateEntityFeature.SWING_MODE
