"""NumberDesc.bounds_fn: bounds read from the whole resources view."""

from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.number import NumberMode

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.number import LocalThingsNumber
from custom_components.localthings.registry.capabilities.oven import OVEN_OPERATIONAL_STATE
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import NumberDesc


class _FakeCoordinator:
    device_key = "TEST-NUMBER"

    def __init__(self, resources):
        self.last_resources = resources

    def resource(self, href):
        return self.last_resources.get(href) or {}

    def canonical_resources(self, _subdevice):
        return self.last_resources

    def cook_time_is_unset(self, _subdevice):
        return False


def _number(desc, resources):
    href = "/temperatures/vs/0"
    bound = BoundEntity(href=href, capability=Capability(href=href, entities=(desc,)), desc=desc)
    return LocalThingsNumber(cast(LocalThingsCoordinator, _FakeCoordinator(resources)), bound)


def test_bounds_fn_reads_a_sibling_href_and_overrides_static_bounds():
    desc = NumberDesc(
        key="setpoint",
        field="value",
        native_min=0.0,
        native_max=10.0,
        step=1.0,
        bounds_fn=lambda rep, resources: (
            float(resources["/spec/vs/0"]["min"]),
            float(resources["/spec/vs/0"]["max"]),
            5.0,
        ),
    )
    entity = _number(desc, {"/temperatures/vs/0": {}, "/spec/vs/0": {"min": 40, "max": 230}})

    assert entity.mode == NumberMode.SLIDER
    assert (entity.native_min_value, entity.native_max_value, entity.native_step) == (
        40.0,
        230.0,
        5.0,
    )


def test_bounds_fn_returning_none_falls_through_to_static_bounds():
    desc = NumberDesc(
        key="setpoint",
        field="value",
        native_min=0.0,
        native_max=10.0,
        step=1.0,
        bounds_fn=lambda rep, resources: None,
    )
    entity = _number(desc, {"/temperatures/vs/0": {}})

    assert (entity.native_min_value, entity.native_max_value, entity.native_step) == (
        0.0,
        10.0,
        1.0,
    )


def test_cook_time_uses_numeric_entry_without_narrowing_the_range():
    desc = next(e for e in OVEN_OPERATIONAL_STATE.entities if e.key == "cook_time")
    entity = _number(desc, {})

    assert entity.mode == NumberMode.BOX
    assert entity.native_unit_of_measurement == "min"
    assert (entity.native_min_value, entity.native_max_value, entity.native_step) == (0, 1439, 1)


@pytest.mark.parametrize("minutes", [0, 4, 10, 90, 599])
async def test_cook_time_unit_changes_preserve_duration_bounds_and_writes(hass, minutes):
    desc = next(e for e in OVEN_OPERATIONAL_STATE.entities if e.key == "cook_time")
    resources = {
        "/information/vs/0": {"x.com.samsung.da.modelNum": "LCD_OV_WALL_16K"},
    }
    entity = _number(desc, resources)
    entity.hass = hass
    coordinator = entity.coordinator
    coordinator.data = {entity._state_key: minutes}
    coordinator.async_send_command = AsyncMock()
    registry = Mock(options={"number": {"unit_of_measurement": "h"}})
    entity.registry_entry = registry
    entity.async_registry_entry_updated()

    assert entity.unit_of_measurement == "h"
    assert entity.state == pytest.approx(minutes / 60)
    assert entity.capability_attributes == {
        "min": 0.0,
        "max": pytest.approx(599 / 60),
        "step": pytest.approx(1 / 60),
        "mode": "box",
    }
    coordinator.async_send_command.assert_not_awaited()
    await entity.async_set_native_value(entity.state)
    coordinator.async_send_command.assert_awaited_once_with(entity._bound, minutes)

    registry.options = {"number": {"unit_of_measurement": "min"}}
    entity.async_registry_entry_updated()
    assert entity.state == minutes
    assert entity.capability_attributes == {"min": 0.0, "max": 599.0, "step": 1.0, "mode": "box"}
    assert coordinator.data[entity._state_key] == minutes


async def test_hour_entry_sends_minutes_and_retains_one_minute_precision(hass):
    desc = next(e for e in OVEN_OPERATIONAL_STATE.entities if e.key == "cook_time")
    entity = _number(desc, {})
    entity.hass = hass
    entity.coordinator.async_send_command = AsyncMock()
    entity.registry_entry = Mock(options={"number": {"unit_of_measurement": "h"}})
    entity.async_registry_entry_updated()

    await entity.async_set_native_value(1.5)
    entity.coordinator.async_send_command.assert_awaited_once_with(entity._bound, 90)
