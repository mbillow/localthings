import time
from samsung_appliance.registry.capabilities.operational import OPERATIONAL_STATE


def test_machine_state_maps_samsung_to_ocf():
    ms = next(e for e in OPERATIONAL_STATE.entities if e.key == 'machine_state')
    assert ms.value_fn('Run') == 'active'
    assert ms.value_fn('Pause') == 'pause'
    assert ms.value_fn('Ready') == 'idle'


def test_active_when_true_only_when_running():
    assert OPERATIONAL_STATE.active_when({'x.com.samsung.da.state': 'Run'}) is True
    assert OPERATIONAL_STATE.active_when({'x.com.samsung.da.state': 'Ready'}) is False


def test_project_extrapolates_remaining_time():
    state = {}
    OPERATIONAL_STATE.on_observation(state, {'x.com.samsung.da.remainingTime': '1:00:00'})
    assert 'remaining_anchor' in state
    out = OPERATIONAL_STATE.project(state, {'machine_state': 'active', 'completion_minutes': 60})
    assert out['completion_minutes'] <= 60
