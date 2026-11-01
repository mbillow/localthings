"""HA water_heater-entity mapping tests for the EHS DHW loop."""

from typing import ClassVar, cast

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_HEAT_PUMP,
    STATE_HIGH_DEMAND,
    STATE_PERFORMANCE,
    WaterHeaterEntity,
)
from homeassistant.const import STATE_OFF, UnitOfTemperature

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type import ehs
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import WaterHeaterDesc
from custom_components.localthings.water_heater import LocalThingsWaterHeater
from tests.conftest import _load_device


class _FakeCoordinator:
    device_key = "TEST-EHS-SERIAL"
    device_info: ClassVar[dict] = {}
    data: ClassVar[dict] = {}

    def __init__(self, resources):
        self.last_resources = resources
        self.commands = []

    def resource(self, href):
        return self.last_resources.get(href, {})

    def canonical_resources(self, subdevice):
        # Every bound entity in this test uses the default MAIN subdevice,
        # so the canonical view is just the raw snapshot (issue #177 --
        # see LocalThingsEntity._resources).
        return self.last_resources

    async def async_send_command(self, bound, payload):
        self.commands.append((bound, payload))


def _entity(resources, coordinator=None):
    bound = discover(
        resources,
        ehs.REGISTRY.capabilities,
        ehs.REGISTRY.pattern_capabilities,
    )
    water_heater_bound = next(item for item in bound if isinstance(item.desc, WaterHeaterDesc))
    return LocalThingsWaterHeater(
        cast(LocalThingsCoordinator, coordinator or _FakeCoordinator(resources)), water_heater_bound
    )


def test_current_operation_reads_eco_when_on():
    entity = _entity(_load_device("ehs"))
    assert entity.current_operation == STATE_ECO


def test_current_operation_is_off_when_powered_off():
    resources = _load_device("ehs")
    resources["/power/dhw/vs/0"]["x.com.samsung.da.power"] = "Off"
    entity = _entity(resources)
    assert entity.current_operation == STATE_OFF


def test_operation_list_includes_off_and_mapped_modes():
    """Fixture's supportedModes is Eco/Std/Power/Force -- all four map onto
    HA's own standard water_heater states (see water_heater.py's module
    docstring for the SmartThings-cloud precedent this mirrors)."""
    entity = _entity(_load_device("ehs"))
    assert entity.operation_list == [
        STATE_OFF,
        STATE_ECO,
        STATE_HEAT_PUMP,
        STATE_PERFORMANCE,
        STATE_HIGH_DEMAND,
    ]


def test_temperature_reads_current_and_target():
    entity = _entity(_load_device("ehs"))
    assert entity.native_current_temperature == 38.0
    assert entity.native_target_temperature == 40.0
    assert entity.native_temperature_unit == UnitOfTemperature.CELSIUS
    assert entity.min_temp == 40.0
    assert entity.max_temp == 62.0
    assert entity.target_temperature_step == 0.5


def test_temperature_bounds_fall_back_together():
    """A board reporting only one end of the range must not pair a real
    device bound with an HA default -- both ends or neither, same rule as
    climate._range()."""
    resources = _load_device("ehs")
    del resources["/temperatures/dhw/vs/0"]["x.com.samsung.da.maximum"]
    entity = _entity(resources)

    # Neither end comes from the device: the device minimum (40.0) is
    # dropped along with the missing maximum, so both fall back to HA's own
    # water_heater defaults rather than being mixed.
    assert entity.min_temp != 40.0
    assert (entity.min_temp, entity.max_temp) == (
        WaterHeaterEntity.min_temp.fget(entity),
        WaterHeaterEntity.max_temp.fget(entity),
    )


def test_zero_increment_is_not_collapsed_into_the_default():
    """`or` would turn a genuine 0 into 0.5 (issue #160)."""
    resources = _load_device("ehs")
    resources["/temperatures/dhw/vs/0"]["x.com.samsung.da.increment"] = "0"
    entity = _entity(resources)

    assert entity.target_temperature_step == 0.0


def test_current_operation_tolerates_lowercase_device_codes():
    resources = _load_device("ehs")
    resources["/mode/dhw/vs/0"]["x.com.samsung.da.modes"] = ["eco"]
    entity = _entity(resources)

    assert entity.current_operation == STATE_ECO


def test_entity_is_named_rather_than_taking_the_device_name():
    """Unlike climate.py's AC, DHW is one loop of a two-loop device, so it
    takes a catalog name instead of presenting as the device itself."""
    entity = _entity(_load_device("ehs"))

    # No _attr_name override (that would present as the bare device name,
    # and would also beat the catalog); the name comes from the catalog key.
    assert "_attr_name" not in entity.__dict__
    assert entity.translation_key == "dhw"


async def test_set_temperature_writes_dhw_temperature():
    resources = _load_device("ehs")
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_temperature(temperature=45.0)

    assert coordinator.commands == [(entity._bound, ("temperature", 45.0))]


async def test_set_temperature_honours_operation_mode():
    """water_heater.set_temperature carries an optional operation_mode
    (SET_TEMPERATURE_SCHEMA); dropping it would move the setpoint without
    ever changing mode -- same bug fixed for climate's hvac_mode."""
    resources = _load_device("ehs")
    resources["/power/dhw/vs/0"]["x.com.samsung.da.power"] = "Off"
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_temperature(temperature=55.0, operation_mode=STATE_HIGH_DEMAND)

    assert coordinator.commands == [
        (entity._bound, ("power", True)),
        (entity._bound, ("mode", "Force")),
        (entity._bound, ("temperature", 55.0)),
    ]


async def test_set_temperature_with_off_mode_skips_the_setpoint_write():
    resources = _load_device("ehs")
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_temperature(temperature=55.0, operation_mode=STATE_OFF)

    assert coordinator.commands == [(entity._bound, ("power", False))]


async def test_turn_on_and_off_write_dhw_power():
    resources = _load_device("ehs")
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_turn_on()
    await entity.async_turn_off()

    assert coordinator.commands == [
        (entity._bound, ("power", True)),
        (entity._bound, ("power", False)),
    ]


async def test_set_operation_mode_off_turns_off_without_writing_mode():
    resources = _load_device("ehs")
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_operation_mode(STATE_OFF)

    assert coordinator.commands == [(entity._bound, ("power", False))]


async def test_set_operation_mode_writes_mapped_device_code():
    resources = _load_device("ehs")
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_operation_mode(STATE_HIGH_DEMAND)

    # DHW power is already on (fixture default) -- no extra power write.
    assert coordinator.commands == [(entity._bound, ("mode", "Force"))]


async def test_set_operation_mode_powers_on_first_when_off():
    resources = _load_device("ehs")
    resources["/power/dhw/vs/0"]["x.com.samsung.da.power"] = "Off"
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_operation_mode(STATE_PERFORMANCE)

    assert coordinator.commands == [
        (entity._bound, ("power", True)),
        (entity._bound, ("mode", "Power")),
    ]
