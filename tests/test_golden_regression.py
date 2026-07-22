import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / 'fixtures' / 'golden'


def _new_state_keys(name, resources):
    from custom_components.localthings.registry.by_type import for_device, for_device_by_model
    from custom_components.localthings.registry.discovery import discover
    from custom_components.localthings.registry.adapter import flatten
    otn = resources.get('/otninformation/vs/0', {})
    one_ui = otn.get('swVersionInfo', {}).get('oneUiVersion', '')
    info = resources.get('/information/vs/0', {})
    reg = for_device(one_ui) if one_ui else None
    if reg is None:
        reg = for_device_by_model(
            info.get('x.com.samsung.da.modelNum', ''),
            info.get('x.com.samsung.da.description', ''),
        )
    if reg is None:
        from custom_components.localthings.registry.registry import CAPABILITIES
        caps, pats = CAPABILITIES, []
    else:
        caps, pats = reg.capabilities, reg.pattern_capabilities
    bound = discover(resources, caps, pats)
    state = flatten(bound, resources)
    return sorted(state.keys())


@pytest.mark.parametrize('name,ip', [
    ('dishwasher', '10.0.0.129'),
    ('refrigerator', '10.0.0.254'),
])
def test_registry_reproduces_golden_state_keys(name, ip, request):
    from tests.conftest import _load_resources
    resources = _load_resources(ip)
    golden = json.loads((GOLDEN / f'{name}.json').read_text())
    state_keys = _new_state_keys(name, resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_washer():
    from tests.conftest import _load_device
    resources = _load_device('washer')
    golden = json.loads((GOLDEN / 'washer.json').read_text())
    state_keys = _new_state_keys('washer', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_dryer():
    from tests.conftest import _load_device
    resources = _load_device('dryer')
    golden = json.loads((GOLDEN / 'dryer.json').read_text())
    state_keys = _new_state_keys('dryer', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner():
    from tests.conftest import _load_device
    resources = _load_device('airconditioner')
    golden = json.loads((GOLDEN / 'airconditioner.json').read_text())
    state_keys = _new_state_keys('airconditioner', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_tp1x_ref_21k_us():
    from tests.conftest import _load_device
    resources = _load_device('refrigerator_tp1x_ref_21k_us')
    golden = json.loads(
        (GOLDEN / 'refrigerator_tp1x_ref_21k_us.json').read_text()
    )
    state_keys = _new_state_keys('refrigerator_tp1x_ref_21k_us', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_washer_flexwash():
    """FlexWash twin washers (WV-prefix consumer model, e.g. WV55M9600AW)
    report no oneUiVersion and previously fell through for_device_by_model's
    consumer-prefix map entirely -- issue #19."""
    from tests.conftest import _load_device
    resources = _load_device('washer_flexwash')
    golden = json.loads((GOLDEN / 'washer_flexwash.json').read_text())
    state_keys = _new_state_keys('washer_flexwash', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_washer_dryer_combo():
    """Washer/dryer combo units carry a writable dryLevel field on
    /washer/vs/0 itself, with no separate dryer resource -- issue #22."""
    from tests.conftest import _load_device
    resources = _load_device('washer_dryer_combo')
    golden = json.loads((GOLDEN / 'washer_dryer_combo.json').read_text())
    state_keys = _new_state_keys('washer_dryer_combo', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_artik051_ref_17k():
    """ARTIK051_REF_17K's Cool Select Zone pantry compartment
    (/status/pantry/one/vs/0) -- issue #20."""
    from tests.conftest import _load_device
    resources = _load_device('refrigerator_artik051_ref_17k')
    golden = json.loads((GOLDEN / 'refrigerator_artik051_ref_17k.json').read_text())
    state_keys = _new_state_keys('refrigerator_artik051_ref_17k', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_tp2x_ref_20k():
    """TP2X_REF_20K -- CV_FDR_-prefixed flex zone (issue #32) plus the extra
    energy fields (cumulativeConsumption/monthlyConsumption/
    thismonthlyConsumption) surfaced by issue #26."""
    from tests.conftest import _load_device
    resources = _load_device('refrigerator_tp2x_ref_20k')
    golden = json.loads((GOLDEN / 'refrigerator_tp2x_ref_20k.json').read_text())
    state_keys = _new_state_keys('refrigerator_tp2x_ref_20k', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_resources_from_batch_preferred_over_flat():
    from tests.conftest import _resources_from_dump
    dump = {
        'device0': [
            {'di': 'device'},  # [0] device-level rep, skipped
            {'href': '/foo', 'rep': {'x': 1}},
        ],
        'resources': {'/foo': {'x': 99}},
    }
    result = _resources_from_dump(dump)
    assert result == {'/foo': {'x': 1}}
