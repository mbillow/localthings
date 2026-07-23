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
        self.commands = []

    def resource(self, href):
        return self.last_resources.get(href, {})

    async def async_send_command(self, bound, payload):
        self.commands.append((bound, payload))


def _entity(resources, coordinator=None):
    bound = discover(
        resources,
        range_hood.REGISTRY.capabilities,
        range_hood.REGISTRY.pattern_capabilities,
    )
    fan_bound = next(item for item in bound if isinstance(item.desc, FanDesc))
    return LocalThingsRangeHoodFan(
        coordinator or _FakeCoordinator(resources), fan_bound,
    )


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


async def test_power_write_prefers_standard_resource_when_both_exist():
    resources = _load_device('range_hood')
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_turn_on()

    assert coordinator.commands[-1][1] == ('power', True, '/power/0')


async def test_power_write_falls_back_to_vendor_resource():
    resources = _load_device('range_hood')
    resources.pop('/power/0')
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_turn_off()

    assert coordinator.commands[-1][1] == ('power', False, '/power/vs/0')
