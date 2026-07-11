from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE


def test_machine_state_maps_samsung_to_ocf():
    ms = next(e for e in OPERATIONAL_STATE.entities if e.key == 'machine_state')
    assert ms.value_fn('Run') == 'active'
    assert ms.value_fn('Pause') == 'pause'
    assert ms.value_fn('Ready') == 'idle'
