from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE


def test_machine_state_maps_samsung_to_ocf():
    ms = next(e for e in OPERATIONAL_STATE.entities if e.key == 'machine_state')
    assert ms.value_fn('Run') == 'active'
    assert ms.value_fn('Pause') == 'pause'
    assert ms.value_fn('Ready') == 'idle'


class TestProgressPercentage:
    """issue #9: device firmware leaves progressPercentage stale (e.g. '1')
    after a cycle ends instead of resetting it, so it must be gated on
    active state the same way `progress`/`cycle_active`/`finish_time` are."""

    def test_zeroed_when_not_active(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == 'progress_percentage')
        rep = {'x.com.samsung.da.state': 'Ready', 'x.com.samsung.da.progressPercentage': '1'}
        assert desc.rep_fn(rep) == 0

    def test_passes_through_when_active(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == 'progress_percentage')
        rep = {'x.com.samsung.da.state': 'Run', 'x.com.samsung.da.progressPercentage': '42'}
        assert desc.rep_fn(rep) == 42


class TestCompletionTime:
    """Unit tests for completion_time and completion_minutes entities."""

    def test_completion_time_string(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == 'completion_time')
        rep = {'x.com.samsung.da.remainingTime': '01:25:30'}
        assert desc.rep_fn(rep) == '01:25:30'

    def test_completion_time_fallback(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == 'completion_time')
        rep = {'remainingTime': '00:45:00'}
        assert desc.rep_fn(rep) == '00:45:00'

    def test_completion_minutes_parsing(self):
        desc = next(e for e in OPERATIONAL_STATE.entities if e.key == 'completion_minutes')
        
        # 1 hour 25 mins 30 secs -> 85 mins + 1 sec ceiling = 86 mins
        rep = {'x.com.samsung.da.remainingTime': '01:25:30'}
        assert desc.rep_fn(rep) == 86

        # Exact minutes: 1 hour 30 mins 00 secs -> 90 mins
        rep_exact = {'x.com.samsung.da.remainingTime': '01:30:00'}
        assert desc.rep_fn(rep_exact) == 90

    def test_completion_time_missing_or_invalid(self):
        desc_time = next(e for e in OPERATIONAL_STATE.entities if e.key == 'completion_time')
        desc_min = next(e for e in OPERATIONAL_STATE.entities if e.key == 'completion_minutes')
        
        rep = {}
        assert desc_time.rep_fn(rep) is None
        assert desc_min.rep_fn(rep) is None


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