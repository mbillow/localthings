"""An offline load must not publish a collapsed capability list.

Home Assistant writes capability attributes even while an entity is
unavailable, so an option list read before the first poll is what a consumer
that snapshots one keeps. HomeKit Bridge reads `hvac_modes` once, when it
builds the thermostat accessory, and never re-reads it: a list holding
nothing but OFF leaves the Home app offering nothing but "off" for the rest
of the run.
"""

from homeassistant.components.climate import ClimateEntityCapabilityAttribute, HVACMode

from custom_components.localthings.climate import LocalThingsClimate
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.entities import ClimateDesc
from tests.test_subdevice_discovery import _coordinator, _discover

# Two climate entities across a subdevice, so one snapshot covers the
# per-subdevice path as well as the single-entity one.
FIXTURE = "airconditioner_artik051_dongle_fac_18k"

# A /wind/strength board, whose fan modes come from supportedModes. The
# fixture above is a legacy /airflow board, which offers the full scale
# instead and reaches it through a board-generation test, not _supported.
WIND_FIXTURE = "airconditioner_tp2x_rac_20k"


def _climate(hass, coordinator) -> list[LocalThingsClimate]:
    entities = []
    for bound in coordinator.bound:
        if isinstance(bound.desc, ClimateDesc):
            entity = LocalThingsClimate(coordinator, bound)
            # capability_attributes reaches for hass.config to convert units.
            entity.hass = hass
            entities.append(entity)
    return entities


async def _rehydrated(hass, online: LocalThingsCoordinator) -> LocalThingsCoordinator:
    await online._async_save_snapshot(online.last_resources, online.subdevices)
    restored = LocalThingsCoordinator(hass, online._entry)
    assert await restored.async_rehydrate()
    # The live cache stays empty until the device answers -- that emptiness is
    # what marks the entities unavailable, and what used to collapse the lists.
    assert restored.last_resources == {}
    return restored


async def test_offline_load_keeps_each_units_hvac_modes(hass):
    online = _coordinator(hass)
    await _discover(online, FIXTURE)
    expected = [entity.hvac_modes for entity in _climate(hass, online)]
    assert expected and all(len(modes) > 1 for modes in expected)

    restored = await _rehydrated(hass, online)
    assert [entity.hvac_modes for entity in _climate(hass, restored)] == expected


async def test_offline_load_keeps_wind_strength_fan_modes(hass):
    online = _coordinator(hass)
    await _discover(online, WIND_FIXTURE)
    expected = [entity.fan_modes for entity in _climate(hass, online)]
    assert expected and all(modes for modes in expected)

    restored = await _rehydrated(hass, online)
    assert [entity.fan_modes for entity in _climate(hass, restored)] == expected


async def test_offline_load_keeps_legacy_airflow_fan_and_swing(hass):
    """A legacy /airflow board answers its fan and swing lists from a
    board-generation test rather than supportedModes, and `supported_features`
    carries SWING_MODE only while `swing_modes` is non-empty. HomeKit adds its
    fan and swing characteristics only if the entity advertises them when the
    accessory is built, so a list that empties before the first poll leaves
    the Home app with a bare thermostat until the bridge is rebuilt.
    """
    online = _coordinator(hass)
    await _discover(online, FIXTURE)
    expected = [
        (entity.fan_modes, entity.swing_modes, entity.supported_features)
        for entity in _climate(hass, online)
    ]
    assert expected and all(fan and swing for fan, swing, _ in expected)

    restored = await _rehydrated(hass, online)
    assert [
        (entity.fan_modes, entity.swing_modes, entity.supported_features)
        for entity in _climate(hass, restored)
    ] == expected


async def test_offline_load_keeps_the_setpoint_bounds(hass):
    """min_temp/max_temp are capability attributes too, and HomeKit freezes
    them into the target-temperature characteristic when it builds the
    accessory. Falling back to Home Assistant's generic 7-35 defaults leaves
    a slider that writes values the unit rejects."""
    online = _coordinator(hass)
    await _discover(online, FIXTURE)
    expected = [
        (entity.min_temp, entity.max_temp, entity.target_temperature_step)
        for entity in _climate(hass, online)
    ]
    assert expected and all(lo != 7 or hi != 35 for lo, hi, _ in expected)

    restored = await _rehydrated(hass, online)
    assert [
        (entity.min_temp, entity.max_temp, entity.target_temperature_step)
        for entity in _climate(hass, restored)
    ] == expected


async def test_offline_load_publishes_the_modes_homekit_reads(hass):
    """HomeKit Bridge builds its thermostat from `capability_attributes`, so
    that mapping -- not just the property -- has to carry the real list."""
    online = _coordinator(hass)
    await _discover(online, FIXTURE)
    restored = await _rehydrated(hass, online)

    for entity in _climate(hass, restored):
        capabilities = entity.capability_attributes
        assert capabilities is not None
        modes = capabilities[ClimateEntityCapabilityAttribute.HVAC_MODES]
        assert modes != [HVACMode.OFF]
        assert HVACMode.OFF in modes


async def test_offline_load_still_withholds_stale_values(hass):
    """The fallback is for option lists only. Values keep reading the live
    cache, because its emptiness is what keeps a rehydrated entity
    unavailable instead of rendering a snapshot's stale reading."""
    online = _coordinator(hass)
    await _discover(online, FIXTURE)
    assert any(entity.current_temperature is not None for entity in _climate(hass, online))

    restored = await _rehydrated(hass, online)
    for entity in _climate(hass, restored):
        assert entity.current_temperature is None
        assert entity.target_temperature is None


async def test_offline_load_keeps_legacy_presets(hass):
    """A legacy /airflow board has no convenient resource: its presets are
    built from the Comode_* token in /mode/vs/0's options, which the
    capability view has to reach as well."""
    online = _coordinator(hass)
    await _discover(online, FIXTURE)
    expected = [entity.preset_modes for entity in _climate(hass, online)]
    assert expected and all(modes for modes in expected)

    restored = await _rehydrated(hass, online)
    assert [entity.preset_modes for entity in _climate(hass, restored)] == expected
    assert all(entity.preset_mode is None for entity in _climate(hass, restored))
