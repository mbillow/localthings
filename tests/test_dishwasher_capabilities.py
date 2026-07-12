"""Tests for dishwasher-specific capabilities."""
from custom_components.localthings.registry.capabilities import dishwasher


class TestCycleOptions:
    def test_parses_edit_course_list(self):
        raw = 'EditCourseList_0E07908683848D808E8F'
        assert dishwasher._parse_edit_course_list(raw) == [
            '0E', '07', '90', '86', '83', '84', '8D', '80', '8E', '8F',
        ]

    def test_parse_edit_course_list_handles_missing_or_malformed(self):
        assert dishwasher._parse_edit_course_list(None) == []
        assert dishwasher._parse_edit_course_list('') == []
        assert dishwasher._parse_edit_course_list('no underscore') == []

    def test_cycle_options_reads_live_edit_course_list(self):
        resources = {
            '/wm/editcourse/vs/0': {
                'x.com.samsung.da.editCourseList': 'EditCourseList_0E9086',
            },
        }
        assert dishwasher._cycle_options(resources) == ['0E', '90', '86']

    def test_cycle_options_empty_when_resource_absent(self):
        assert dishwasher._cycle_options({}) == []

    def test_cycle_options_empty_when_resource_empty(self):
        resources = {'/wm/editcourse/vs/0': {}}
        assert dishwasher._cycle_options(resources) == []

    def test_cycle_desc_uses_cycle_options_callable(self):
        desc = next(e for e in dishwasher.CYCLE_OPTIONS.entities if e.key == 'cycle')
        assert desc.options is dishwasher._cycle_options
        assert desc.translation_key == 'dishwasher_cycle'

    def test_exists_only_when_edit_course_list_is_live(self):
        desc = next(e for e in dishwasher.CYCLE_OPTIONS.entities if e.key == 'cycle')
        assert desc.exists_fn({}, {}) is False
        assert desc.exists_fn({}, {'/wm/editcourse/vs/0': {}}) is False
        live = {'/wm/editcourse/vs/0': {'x.com.samsung.da.editCourseList': 'EditCourseList_0E'}}
        assert desc.exists_fn({}, live) is True

    def test_cycle_write_uses_raw_code_directly(self):
        desc = next(e for e in dishwasher.CYCLE_OPTIONS.entities if e.key == 'cycle')
        rep = {'x.com.samsung.da.options': ['DeviceType_0001', 'Course_0E', 'GMT_04']}
        path, body = desc.write_fn('90', rep)
        assert path == ['course', 'vs', '0']
        assert body == {
            'x.com.samsung.da.options': ['DeviceType_0001', 'Course_90', 'GMT_04'],
        }
