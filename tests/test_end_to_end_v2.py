import pytest
from tests.conftest import _load_device
from custom_components.localthings.registry.by_type import for_device, _type_key
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.adapter import flatten, is_active


@pytest.mark.parametrize('name,expected_type_key', [
    ('dishwasher',   'dishwasher'),
    ('refrigerator', 'refrigerator'),
])
def test_full_pipeline_v2(name, expected_type_key):
    resources = _load_device(name)

    otn = resources.get('/otninformation/vs/0', {})
    one_ui = otn.get('swVersionInfo', {}).get('oneUiVersion', '')
    assert _type_key(one_ui) == expected_type_key

    reg = for_device(one_ui)
    assert reg is not None

    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    assert bound

    state = flatten(bound, resources)
    assert state
    # is_active returns bool; just verify it runs without error
    is_active(bound, resources)
