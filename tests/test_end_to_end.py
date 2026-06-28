import json

import pytest

from samsung_appliance.registry import (CAPABILITIES, discover,
                                        build_runtime_descriptor)


@pytest.mark.parametrize('name,ip', [
    ('dishwasher', '10.0.0.129'),
    ('refrigerator', '10.0.0.254'),
])
def test_full_pipeline_produces_entities_and_observe_paths(name, ip):
    resources = json.load(open(f'local-tools/dumps/{ip}.json'))['resources']
    bound = discover(resources, CAPABILITIES)
    rd = build_runtime_descriptor(
        bound, topic_prefix=f'samsung_{name}', ha_prefix='homeassistant',
        device_name=name.title(), model='M', name=name, default_port=49154)
    assert rd.discovery_payloads, "no entities discovered"
    assert rd.observe_paths, "no observe paths"
    assert any(t.name == 'hot' for t in rd.poll_tiers), "no hot tier"
    assert any(t.is_sweep for t in rd.poll_tiers), "no sweep tier"
    # flatten runs cleanly over the real rep set
    flat = rd.flatten(resources)
    assert flat
