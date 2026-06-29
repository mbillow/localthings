import json

from samsung_appliance.registry.adapter import build_runtime_descriptor
from samsung_appliance.registry.capabilities import common
from samsung_appliance.registry.discovery import discover


def _bound(resources):
    reg = {c.href: c for c in (common.KIDS_LOCK, common.ENERGY_METER)}
    return discover(resources, reg)


def _bound_with_power(resources):
    reg = {c.href: c for c in (common.KIDS_LOCK, common.ENERGY_METER, common.POWER)}
    return discover(resources, reg)


def test_flatten_applies_value_fn(dishwasher_resources):
    rd = build_runtime_descriptor(
        _bound(dishwasher_resources), topic_prefix='t', ha_prefix='homeassistant',
        device_name='Dishwasher', model='M', name='dishwasher', default_port=49154)
    flat = rd.flatten(dishwasher_resources)
    assert flat['power_watts'] is not None
    assert isinstance(flat['child_lock'], bool)


def test_observe_paths_are_segment_lists(dishwasher_resources):
    rd = build_runtime_descriptor(
        _bound(dishwasher_resources), topic_prefix='t', ha_prefix='homeassistant',
        device_name='Dishwasher', model='M', name='dishwasher', default_port=49154)
    assert ['kidslock', 'vs', '0'] in rd.observe_paths


def test_discovery_payloads_have_unique_ids(dishwasher_resources):
    rd = build_runtime_descriptor(
        _bound(dishwasher_resources), topic_prefix='samsung_dishwasher',
        ha_prefix='homeassistant', device_name='Dishwasher', model='M',
        name='dishwasher', default_port=49154)
    topics = [t for t, _ in rd.discovery_payloads]
    # one config topic per produced sensor/binary_sensor entity
    assert any('/binary_sensor/samsung_dishwasher/child_lock/config' in t for t in topics)
    assert any('/sensor/samsung_dishwasher/power_watts/config' in t for t in topics)
    # payloads are JSON bytes carrying unique_id
    cfg = json.loads(next(p for t, p in rd.discovery_payloads if 'child_lock' in t))
    assert cfg['unique_id'] == 'samsung_dishwasher_child_lock'


def test_switch_discovery_config_has_correct_payloads(dishwasher_resources):
    rd = build_runtime_descriptor(
        _bound_with_power(dishwasher_resources), topic_prefix='samsung_dishwasher',
        ha_prefix='homeassistant', device_name='Dishwasher', model='M',
        name='dishwasher', default_port=49154)
    cfg = json.loads(next(p for t, p in rd.discovery_payloads if 'power_switch' in t))
    assert cfg['payload_on'] == 'On'
    assert cfg['payload_off'] == 'Off'
    assert cfg['state_on'] == 'On'
    assert cfg['state_off'] == 'Off'
    assert 'On' in cfg['value_template']
    assert 'Off' in cfg['value_template']
