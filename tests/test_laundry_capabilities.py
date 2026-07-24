"""Tests for the shared laundry capabilities (washer/dryer/dishwasher)."""
from custom_components.localthings.registry.capabilities import laundry


class TestCourseHelpers:
    def test_parses_edit_course_list(self):
        raw = 'EditCourseList_1C1D211B1E29243328262722202325322F2E30662D8F96'
        assert laundry.parse_edit_course_list(raw) == [
            '1C', '1D', '21', '1B', '1E', '29', '24', '33', '28', '26', '27',
            '22', '20', '23', '25', '32', '2F', '2E', '30', '66', '2D', '8F', '96',
            '2b', '2c'
        ]

    def test_parse_edit_course_list_handles_missing_or_malformed(self):
        assert laundry.parse_edit_course_list(None) == []
        assert laundry.parse_edit_course_list('') == []
        assert laundry.parse_edit_course_list('no underscore') == []

    def test_cycle_options_reads_live_edit_course_list(self):
        """The device's own course list -- including a code ('65') never seen
        on the primary dump -- is used as-is; there is no hardcoded table."""
        resources = {
            '/wm/editcourse/vs/0': {
                'x.com.samsung.da.editCourseList': 'EditCourseList_651C',
            },
        }
        assert laundry.cycle_options(resources) == ['65', '1C']

    def test_cycle_options_empty_when_resource_absent_or_empty(self):
        assert laundry.cycle_options({}) == []
        assert laundry.cycle_options({'/wm/editcourse/vs/0': {}}) == []

    def test_option_value(self):
        opts = ['DeviceType_0167', 'Course_1C', 'GMT_04']
        assert laundry.option_value(opts, 'Course') == '1C'
        assert laundry.option_value(opts, 'Missing') is None


class TestCycleSelect:
    def test_builds_labelled_cycle_select(self):
        desc = laundry.cycle_select(translation_key='dryer_cycle', icon='mdi:tumble-dryer')
        assert desc.key == 'cycle'
        assert desc.translation_key == 'dryer_cycle'
        assert desc.icon == 'mdi:tumble-dryer'
        assert desc.options is laundry.cycle_options

    def test_reads_raw_course_code_from_options(self):
        desc = laundry.cycle_select(translation_key='dryer_cycle', icon='x')
        rep = {'x.com.samsung.da.options': ['DeviceType_0167', 'Course_16', 'GMT_04']}
        assert desc.rep_fn(rep) == '16'
        assert desc.rep_fn({'x.com.samsung.da.options': ['GMT_04']}) is None

    def test_exists_only_when_edit_course_list_is_live(self):
        desc = laundry.cycle_select(translation_key='dryer_cycle', icon='x')
        assert desc.exists_fn({}, {}) is False
        assert desc.exists_fn({}, {'/wm/editcourse/vs/0': {}}) is False
        live = {'/wm/editcourse/vs/0': {'x.com.samsung.da.editCourseList': 'EditCourseList_16'}}
        assert desc.exists_fn({}, live) is True

    def test_cycle_write_rmw_on_options(self):
        desc = laundry.cycle_select(translation_key='dryer_cycle', icon='x')
        rep = {'x.com.samsung.da.options': ['DeviceType_0167', 'Course_16', 'GMT_04']}
        path, body = desc.write_fn('1D', rep)
        assert path == ['course', 'vs', '0']
        assert body == {
            'x.com.samsung.da.options': ['DeviceType_0167', 'Course_1D', 'GMT_04'],
        }

    def test_cycle_write_noop_without_options(self):
        desc = laundry.cycle_select(translation_key='dryer_cycle', icon='x')
        assert desc.write_fn('1D', {}) is None


class TestBuzzerSound:
    def test_href(self):
        assert laundry.BUZZER_SOUND.href == '/buzzersound/vs/0'

    def test_buzzer_sound_write(self):
        desc = next(e for e in laundry.BUZZER_SOUND.entities if e.key == 'buzzer_sound')
        assert desc.options_field == 'supportedBuzzerSound'
        path, body = desc.write_fn('On', {})
        assert path == ['buzzersound', 'vs', '0']
        assert body == {'setBuzzerSound': 'On'}

    def test_finish_sound_exists_only_when_supported(self):
        desc = next(e for e in laundry.BUZZER_SOUND.entities if e.key == 'finish_sound')
        assert desc.exists_fn({'setBuzzerSound': 'On'}, {}) is False
        assert desc.exists_fn({'supportedFinishSound': ['FinishSound_1']}, {}) is True


class TestJobBeginningStatus:
    def test_href_and_field(self):
        assert laundry.JOB_BEGINNING_STATUS.href == '/wm/jobbeginingstatus/vs/0'
        desc = laundry.JOB_BEGINNING_STATUS.entities[0]
        assert desc.field == 'x.com.samsung.da.currentStatus'
        assert desc.entity_category == 'diagnostic'
