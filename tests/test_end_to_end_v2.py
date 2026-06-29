import json
import pytest
from tests.conftest import _resources_from_dump
from samsung_appliance.registry.by_type import for_device, _type_key
from samsung_appliance.registry.discovery import discover
from samsung_appliance.registry.adapter import build_runtime_descriptor


@pytest.mark.parametrize('name,ip,expected_type_key', [
    ('dishwasher',   '10.0.0.129', 'dishwasher'),
    ('refrigerator', '10.0.0.254', 'refrigerator'),
])
def test_full_pipeline_v2(name, ip, expected_type_key):
    with open(f'local-tools/dumps/{ip}.json') as f:
        dump = json.load(f)
    resources = _resources_from_dump(dump)

    otn = resources.get('/otninformation/vs/0', {})
    one_ui = otn.get('swVersionInfo', {}).get('oneUiVersion', '')
    assert _type_key(one_ui) == expected_type_key

    reg = for_device(one_ui)
    assert reg is not None

    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    assert bound

    rd = build_runtime_descriptor(bound, topic_prefix=f'samsung_{name}',
                                   ha_prefix='homeassistant', device_name=name.title(),
                                   model='M', name=name, default_port=49154)
    assert rd.discovery_payloads
    flat = rd.flatten(resources)
    assert flat
    assert rd.active_interval_s > 0
    assert rd.idle_interval_s > rd.active_interval_s
