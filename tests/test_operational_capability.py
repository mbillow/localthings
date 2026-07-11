from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE


def test_machine_state_maps_samsung_to_ocf():
    ms = next(e for e in OPERATIONAL_STATE.entities if e.key == 'machine_state')
    assert ms.value_fn('Run') == 'active'
    assert ms.value_fn('Pause') == 'pause'
    assert ms.value_fn('Ready') == 'idle'


class TestDelayFieldFallback:
    def test_reads_delay_end_time_when_delay_start_time_absent(self):
        from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == 'delay_start_hours')
        rep = {'x.com.samsung.da.delayEndTime': '02:30:00'}
        assert desc.rep_fn(rep) == 2.5

    def test_prefers_delay_start_time_when_both_present(self):
        from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == 'delay_start_hours')
        rep = {
            'x.com.samsung.da.delayStartTime': '01:00:00',
            'x.com.samsung.da.delayEndTime': '02:00:00',
        }
        assert desc.rep_fn(rep) == 1.0

    def test_write_targets_delay_end_time_when_that_is_what_device_reports(self):
        from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == 'delay_start_hours')
        rep = {'x.com.samsung.da.delayEndTime': '00:00:00'}
        path, body = desc.write_fn(1.5, rep)
        assert path == ['operational', 'state', 'vs', '0']
        assert body == {'x.com.samsung.da.delayEndTime': '1:30:00'}

    def test_write_targets_delay_start_time_by_default(self):
        from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == 'delay_start_hours')
        rep = {'x.com.samsung.da.delayStartTime': '00:00:00'}
        path, body = desc.write_fn(1.5, rep)
        assert body == {'x.com.samsung.da.delayStartTime': '1:30:00'}
