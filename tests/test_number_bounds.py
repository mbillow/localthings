"""NumberDesc.bounds_fn: bounds read from the whole resources view."""

from typing import cast

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.number import LocalThingsNumber
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
