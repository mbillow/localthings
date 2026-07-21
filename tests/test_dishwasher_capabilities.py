"""Tests for dishwasher-specific capabilities.

The shared /course/vs/0 cycle-select machinery (parse_edit_course_list,
cycle_options, cycle_write) is tested in test_laundry_capabilities.py; here we
check the dishwasher wiring and its device-specific options.
"""
from custom_components.localthings.registry.capabilities import dishwasher, laundry


class TestCycleOptions:
    def _cycle(self):
        return next(e for e in dishwasher.CYCLE_OPTIONS.entities if e.key == 'cycle')

    def test_cycle_desc_uses_shared_cycle_options(self):
        desc = self._cycle()
        assert desc.options is laundry.cycle_options
        assert desc.translation_key == 'dishwasher_cycle'

    def test_exists_only_when_edit_course_list_is_live(self):
        desc = self._cycle()
        assert desc.exists_fn({}, {}) is False
        assert desc.exists_fn({}, {'/wm/editcourse/vs/0': {}}) is False
        live = {'/wm/editcourse/vs/0': {'x.com.samsung.da.editCourseList': 'EditCourseList_0E'}}
        assert desc.exists_fn({}, live) is True

    def test_cycle_write_uses_raw_code_directly(self):
        desc = self._cycle()
        rep = {'x.com.samsung.da.options': ['DeviceType_0001', 'Course_0E', 'GMT_04']}
        path, body = desc.write_fn('90', rep)
        assert path == ['course', 'vs', '0']
        assert body == {
            'x.com.samsung.da.options': ['DeviceType_0001', 'Course_90', 'GMT_04'],
        }


class TestDishwasherOptions:
    def test_storm_wash_read_and_write(self):
        desc = next(e for e in dishwasher.CYCLE_OPTIONS.entities if e.key == 'storm_wash')
        assert desc.rep_fn({'x.com.samsung.da.options': ['StormWashZone_On']}) is True
        assert desc.rep_fn({'x.com.samsung.da.options': ['StormWashZone_Off']}) is False
        path, body = desc.write_fn('Off', {'x.com.samsung.da.options': ['StormWashZone_On']})
        assert path == ['course', 'vs', '0']
        assert 'StormWashZone_Off' in body['x.com.samsung.da.options']

    def test_auto_release_exists_only_when_field_present(self):
        desc = next(e for e in dishwasher.CYCLE_OPTIONS.entities if e.key == 'auto_release_dry')
        assert desc.exists_fn({'x.com.samsung.da.options': []}, {}) is False
        assert desc.exists_fn(
            {'x.com.samsung.da.options': ['AutoDoorRelease_On']}, {}) is True
