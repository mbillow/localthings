"""Native temperature values must work before and after HA 2026.11."""

from __future__ import annotations

import importlib
from typing import ClassVar, cast

import pytest
from homeassistant.components.climate import ClimateEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

from custom_components.localthings.climate import LocalThingsClimate
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type import airconditioner, ehs
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import (
    ClimateDesc,
    WaterHeaterDesc,
    ZoneClimateDesc,
)
from custom_components.localthings.water_heater import LocalThingsWaterHeater
from custom_components.localthings.zone_climate import LocalThingsZoneClimate
from tests.conftest import _load_device


class _Coordinator:
    device_key = "test-temperature-device"
    device_info: ClassVar[dict] = {}
    data: ClassVar[dict] = {}

    def __init__(self, resources):
        self.last_resources = resources
        self.commands = []

    def resource(self, href):
        return self.last_resources.get(href, {})

    def canonical_resources(self, subdevice):
        return self.last_resources

    def learned_modes(self, href):
        return []

    async def async_send_command(self, bound, payload):
        self.commands.append((bound, payload))


def _entity(kind, unit, current, target):
    ac = kind.startswith("ac")
    registry = airconditioner.REGISTRY if ac else ehs.REGISTRY
    fixture = "airconditioner" if ac else "ehs_01002"
    resources = _load_device(fixture)
    device_unit = "Fahrenheit" if unit == UnitOfTemperature.FAHRENHEIT else "Celsius"
    if kind == "ac_vendor":
        resources.pop("/temperature/current/0", None)
        resources.pop("/temperature/desired/0", None)
        resources["/temperatures/vs/0"] = {
            "x.com.samsung.da.items": [
                {
                    "x.com.samsung.da.unit": device_unit,
                    "x.com.samsung.da.current": current,
                    "x.com.samsung.da.desired": target,
                    "x.com.samsung.da.minimum": 10,
                    "x.com.samsung.da.maximum": 104,
                    "x.com.samsung.da.increment": 1,
                }
            ]
        }
    elif ac:
        resources["/temperature/current/0"] = {"temperature": current}
        resources["/temperature/desired/0"] = {
            "temperature": target,
            "units": device_unit,
            "range": [10, 104],
        }
    bound = discover(resources, registry.capabilities, registry.pattern_capabilities)
    if ac:
        selected = next(item for item in bound if isinstance(item.desc, ClimateDesc))
        cls = LocalThingsClimate
    elif kind == "dhw":
        selected = next(item for item in bound if isinstance(item.desc, WaterHeaterDesc))
        cls = LocalThingsWaterHeater
        resources["/temperatures/dhw/vs/0"].update(
            {
                "x.com.samsung.da.unit": device_unit,
                "x.com.samsung.da.current": current,
                "x.com.samsung.da.desired": target,
            }
        )
    else:
        key = "zone2_climate" if kind == "zone2" else "zone_climate"
        selected = next(item for item in bound if item.desc.key == key)
        desc = cast(ZoneClimateDesc, selected.desc)
        resources[desc.temperature_href].update(
            {
                "x.com.samsung.da.unit": device_unit,
                "x.com.samsung.da.current": current,
                "x.com.samsung.da.desired": target,
            }
        )
        cls = LocalThingsZoneClimate
    coordinator = _Coordinator(resources)
    entity = cls(cast(LocalThingsCoordinator, coordinator), selected)
    return entity, coordinator


@pytest.mark.parametrize("kind", ["ac_ocf", "ac_vendor", "zone1", "zone2", "dhw"])
@pytest.mark.parametrize(
    ("unit", "current", "target", "ha_units", "shown_current", "shown_target"),
    [
        (UnitOfTemperature.CELSIUS, 20, 25, METRIC_SYSTEM, 20, 25),
        (UnitOfTemperature.CELSIUS, 20, 25, US_CUSTOMARY_SYSTEM, 68, 77),
        (UnitOfTemperature.FAHRENHEIT, 68, 77, METRIC_SYSTEM, 20, 25),
        (UnitOfTemperature.FAHRENHEIT, 68, 77, US_CUSTOMARY_SYSTEM, 68, 77),
    ],
)
async def test_temperature_units_display_and_native_commands(
    hass: HomeAssistant,
    caplog,
    kind,
    unit,
    current,
    target,
    ha_units,
    shown_current,
    shown_target,
):
    hass.config.units = ha_units
    entity, coordinator = _entity(kind, unit, current, target)
    entity.hass = hass
    caplog.clear()

    assert entity.native_temperature_unit == unit
    assert entity.native_current_temperature == current
    assert entity.native_target_temperature == target
    attributes = entity.state_attributes
    assert attributes["current_temperature"] == shown_current
    assert attributes["temperature"] == shown_target

    if hasattr(ClimateEntity, "native_temperature_unit"):
        assert attributes["temperature_unit"] == ha_units.temperature_unit
    else:
        assert entity.temperature_unit == unit
        assert entity.current_temperature == current
        assert entity.target_temperature == target

    await entity.async_set_temperature(temperature=target)
    command = "temperature_ocf" if kind == "ac_ocf" else "temperature"
    assert coordinator.commands == [(entity._bound, (command, target))]
    assert not any("deprecated" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("module", ["climate", "zone_climate", "water_heater"])
def test_platform_import_without_temperature_deprecation(caplog, module):
    imported = importlib.import_module(f"custom_components.localthings.{module}")
    caplog.clear()
    importlib.reload(imported)
    assert not any("provides the deprecated" in record.getMessage() for record in caplog.records)
