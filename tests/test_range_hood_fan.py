"""HA fan-entity mapping tests for the range hood."""

from custom_components.localthings.fan import LocalThingsRangeHoodFan
from custom_components.localthings.registry.by_type import range_hood
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import FanDesc
from tests.conftest import _load_device


class _FakeCoordinator:
    device_serial = 'TEST-HOOD-SERIAL'
    device_info = {}
    data = {}

    def __init__(self, resources):
        self.last_resources = resources

    def resource(self, href):
        return self.last_resources.get(href, {})


def _entity(resources):
    bound = discover(
        resources,
        range_hood.REGISTRY.capabilities,
        range_hood.REGISTRY.pattern_capabilities,
    )
    fan_bound = next(item for item in bound if isinstance(item.desc, FanDesc))
    return LocalThingsRangeHoodFan(_FakeCoordinator(resources), fan_bound)


def test_power_off_maps_to_zero_percent_and_four_retained_speeds():
    entity = _entity(_load_device('range_hood'))
    assert entity.is_on is False
    assert entity.speed_count == 4
    assert entity.percentage == 0


def test_active_codes_map_to_ordered_percentages():
    resources = _load_device('range_hood')
    resources['/power/0']['value'] = True

    resources['/hood/fanspeed/vs/0'][
        'x.com.samsung.da.hood.fanSpeed'
    ] = '14'
    assert _entity(resources).percentage == 25

    resources['/hood/fanspeed/vs/0'][
        'x.com.samsung.da.hood.fanSpeed'
    ] = '15'
    assert _entity(resources).percentage == 50

    resources['/hood/fanspeed/vs/0'][
        'x.com.samsung.da.hood.fanSpeed'
    ] = '16'
    assert _entity(resources).percentage == 75

    resources['/hood/fanspeed/vs/0'][
        'x.com.samsung.da.hood.fanSpeed'
    ] = '17'
    assert _entity(resources).percentage == 100
