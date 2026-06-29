import pytest

from tests.conftest import _load_resources
from samsung_appliance.registry import (CAPABILITIES, discover,
                                        build_runtime_descriptor)


@pytest.mark.parametrize('name,ip', [
    ('dishwasher', '10.0.0.129'),
    ('refrigerator', '10.0.0.254'),
])
def test_full_pipeline_produces_entities_and_intervals(name, ip):
    resources = _load_resources(ip)
    bound = discover(resources, CAPABILITIES)
    rd = build_runtime_descriptor(
        bound, topic_prefix=f'samsung_{name}', ha_prefix='homeassistant',
        device_name=name.title(), model='M', name=name, default_port=49154)
    assert rd.discovery_payloads, "no entities discovered"
    assert rd.active_interval_s > 0, "active_interval_s must be positive"
    assert rd.idle_interval_s > rd.active_interval_s, (
        "idle_interval_s must be greater than active_interval_s")
    # flatten runs cleanly over the real rep set
    flat = rd.flatten(resources)
    assert flat
