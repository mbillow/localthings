"""Regression tests for AC target-temperature preservation on power-up."""

import pytest
from homeassistant.components.climate import HVACMode

from custom_components.localthings.climate import LocalThingsClimate
from custom_components.localthings.registry.by_type.airconditioner import REGISTRY
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import ClimateDesc
from tests.conftest import _load_device


class _FakeCoordinator:
    device_serial = 'TEST-AC-SERIAL'
    device_info = {}
    data = {}

    def __init__(self, resources):
        self.last_resources = resources
        self.commands = []
        self.command_hook = None

    def resource(self, href):
        return self.last_resources.get(href, {})

    async def async_send_command(self, bound, payload):
        self.commands.append((bound, payload))
        if self.command_hook is not None:
            self.command_hook(payload)


def _entity(resources):
    bound = discover(
        resources,
        REGISTRY.capabilities,
        REGISTRY.pattern_capabilities,
    )
    climate_bound = next(
        item for item in bound if isinstance(item.desc, ClimateDesc)
    )
    coordinator = _FakeCoordinator(resources)
    return LocalThingsClimate(coordinator, climate_bound), coordinator


def _payloads(coordinator):
    return [payload for _, payload in coordinator.commands]


@pytest.mark.parametrize('target', [16.0, 26.0])
async def test_off_to_cool_reasserts_ocf_target_after_power_and_mode(target):
    resources = _load_device('airconditioner')
    resources['/temperature/desired/0']['temperature'] = target
    entity, coordinator = _entity(resources)

    await entity.async_set_hvac_mode(HVACMode.COOL)

    assert _payloads(coordinator) == [
        ('power', True),
        ('mode', 'Cool'),
        ('temperature_ocf', target),
    ]


async def test_power_up_captures_target_and_write_channel_before_first_await():
    resources = _load_device('airconditioner')
    resources['/temperature/desired/0']['temperature'] = 26.0
    entity, coordinator = _entity(resources)

    def simulate_transition_readback(payload):
        if payload == ('power', True):
            resources['/temperature/desired/0']['temperature'] = 16.0
            resources['/temperature/current/0'] = {}

    coordinator.command_hook = simulate_transition_readback

    await entity.async_set_hvac_mode(HVACMode.COOL)

    assert _payloads(coordinator) == [
        ('power', True),
        ('mode', 'Cool'),
        ('temperature_ocf', 26.0),
    ]


async def test_off_to_cool_reasserts_vendor_target_on_vendor_only_board():
    resources = _load_device('airconditioner_tp1x_da_ac_rac_01011')
    entity, coordinator = _entity(resources)

    await entity.async_set_hvac_mode(HVACMode.COOL)

    assert _payloads(coordinator) == [
        ('power', True),
        ('mode', 'Cool'),
        ('temperature', 25.0),
    ]


async def test_mode_change_while_on_does_not_write_temperature():
    resources = _load_device('airconditioner')
    resources['/power/vs/0']['x.com.samsung.da.power'] = 'On'
    entity, coordinator = _entity(resources)

    await entity.async_set_hvac_mode(HVACMode.DRY)

    assert _payloads(coordinator) == [('mode', 'Dry')]


async def test_power_up_without_known_target_skips_temperature_write():
    resources = _load_device('airconditioner')
    resources['/temperature/desired/0'].pop('temperature')
    resources['/temperatures/vs/0']['x.com.samsung.da.items'][0].pop(
        'x.com.samsung.da.desired'
    )
    entity, coordinator = _entity(resources)

    await entity.async_set_hvac_mode(HVACMode.COOL)

    assert _payloads(coordinator) == [
        ('power', True),
        ('mode', 'Cool'),
    ]


async def test_explicit_temperature_with_mode_writes_only_the_new_target():
    resources = _load_device('airconditioner')
    resources['/temperature/desired/0']['temperature'] = 26.0
    entity, coordinator = _entity(resources)

    def simulate_transition_readback(payload):
        if payload == ('power', True):
            resources['/temperature/current/0'] = {}

    coordinator.command_hook = simulate_transition_readback

    await entity.async_set_temperature(
        hvac_mode=HVACMode.COOL,
        temperature=24.0,
    )

    assert _payloads(coordinator) == [
        ('power', True),
        ('mode', 'Cool'),
        ('temperature_ocf', 24.0),
    ]


async def test_explicit_temperature_with_off_only_turns_power_off():
    resources = _load_device('airconditioner')
    entity, coordinator = _entity(resources)

    await entity.async_set_temperature(
        hvac_mode=HVACMode.OFF,
        temperature=24.0,
    )

    assert _payloads(coordinator) == [('power', False)]
