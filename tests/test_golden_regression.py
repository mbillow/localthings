import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / 'fixtures' / 'golden'


def _new_state_and_uids(name, resources):
    from samsung_appliance.registry.registry import CAPABILITIES
    from samsung_appliance.registry.discovery import discover
    from samsung_appliance.registry.adapter import build_runtime_descriptor
    bound = discover(resources, CAPABILITIES)
    rd = build_runtime_descriptor(
        bound, topic_prefix=f'samsung_{name}', ha_prefix='homeassistant',
        device_name=name.title(), model='M', name=name, default_port=49154)
    state = rd.flatten(resources)
    uids = [json.loads(p)['unique_id'] for _, p in rd.discovery_payloads]
    return sorted(state.keys()), sorted(uids)


@pytest.mark.parametrize('name,ip', [
    ('dishwasher', '10.0.0.129'),
    ('refrigerator', '10.0.0.254'),
])
def test_registry_reproduces_golden_state_keys(name, ip, request):
    resources = json.load(open(f'local-tools/dumps/{ip}.json'))['resources']
    golden = json.loads((GOLDEN / f'{name}.json').read_text())
    state_keys, _ = _new_state_and_uids(name, resources)
    missing = set(golden['state_keys']) - set(state_keys)
    assert not missing, f"registry missing state keys: {sorted(missing)}"
