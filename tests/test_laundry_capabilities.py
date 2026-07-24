"""Tests for the shared laundry capabilities (washer/dryer/dishwasher)."""
from custom_components.localthings.registry.capabilities import laundry


class TestCourseHelpers:
    def test_parses_edit_course_list(self):
        raw = 'EditCourseList_1C1D211B1E29243328262722202325322F2E30662D8F962b2a'
        assert laundry.parse_edit_course_list(raw) == [
            '1C', '1D', '21', '1B', '1E', '29', '24', '33', '28', '26', '27',
            '22', '20', '23', '25', '32', '2F', '2E', '30', '66', '2D', '8F', '96',
            '2b', '2a'
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


class TestCourseCodesFromSupportedOptions:
    """cycle_options()'s fallback for boards that populate
    /wm/editcourse/vs/0 without ever filling in editCourseList itself
    (issue #1) -- derives the course list from /course/vs/0's own
    supportedOptions instead."""

    # Real dump from issue #1 (DA_WM_TP1_21_COMMON, WW5000C): a 1-hex-nibble
    # header followed by 14 self-indexed 7-byte-per-course records. '1C' (the
    # first record) is confirmed as "Eco 40-60" both by this device's own
    # currently-selected course matching the SmartThings app screenshot's
    # checked item, and by six other independent devices' already-shipped
    # translations agreeing on the same code -> name mapping.
    _REAL_SUPPORTED_OPTIONS = (
        '31C8410923FA67F1B847E923FA67F25843E933FA57F20857E943FA67F'
        '088000913FA67F7485209204A5208780009000A00006841E930FA30F'
        '7F841E920FA30F65841E943FA57F8F8102923FA57F96841E920FA37F'
        '34841E923FA67FA0811E933FA33F'
    )

    def test_derives_codes_when_edit_course_list_is_empty(self):
        resources = {
            '/wm/editcourse/vs/0': {'x.com.samsung.da.editCourseList': ''},
            '/course/vs/0': {
                'x.com.samsung.da.options': ['Course_1C'],
                'x.com.samsung.da.supportedOptions': [self._REAL_SUPPORTED_OPTIONS],
            },
        }
        assert laundry.cycle_options(resources) == [
            '1C', '1B', '25', '20', '08', '74', '87', '06',
            '7F', '65', '8F', '96', '34', 'A0',
        ]

    def test_edit_course_list_still_takes_priority(self):
        """A live editCourseList wins even with supportedOptions present --
        no reason to prefer a derived list over the authoritative one."""
        resources = {
            '/wm/editcourse/vs/0': {'x.com.samsung.da.editCourseList': 'EditCourseList_651C'},
            '/course/vs/0': {
                'x.com.samsung.da.options': ['Course_1C'],
                'x.com.samsung.da.supportedOptions': [self._REAL_SUPPORTED_OPTIONS],
            },
        }
        assert laundry.cycle_options(resources) == ['65', '1C']

    def test_rejects_a_table_missing_the_current_course(self):
        """The device's own currently-selected course must be a member of
        its derived list -- a mismatch means the guess is wrong, not that
        the device selected something outside its own supported set."""
        resources = {
            '/course/vs/0': {
                'x.com.samsung.da.options': ['Course_FF'],
                'x.com.samsung.da.supportedOptions': [self._REAL_SUPPORTED_OPTIONS],
            },
        }
        assert laundry.cycle_options(resources) == []

    def test_smallest_passing_split_wins_over_its_own_multiples(self):
        """K=2 and K=4 both trivially re-pass the same two checks here --
        each is just a sparser sampling of the true, smaller K=1 table (its
        first bytes are a subset of K=1's, so uniqueness and "contains the
        current course" carry over for free) -- but K=1 is the real, most
        specific table and must be the one returned."""
        resources = {
            '/course/vs/0': {
                'x.com.samsung.da.options': ['Course_AA'],
                'x.com.samsung.da.supportedOptions': ['0AABBCCDD'],
            },
        }
        assert laundry.cycle_options(resources) == ['AA', 'BB', 'CC', 'DD']

    def test_empty_without_supported_options_or_course_href(self):
        assert laundry.cycle_options({}) == []
        assert laundry.cycle_options({'/course/vs/0': {}}) == []

    def test_smallest_wins_even_when_a_larger_pass_is_not_a_multiple(self):
        """Real dishwasher dump (K=7, 10 courses): K=10, 14, and 35 also
        pass both checks here, and none of them are multiples of 7 --
        position 0 lands on the same real course code ('0e') regardless of
        K, which alone satisfies the current-course guard for several
        unrelated splits. Smallest-K-wins is a heuristic that matches every
        real dump checked so far, not a proven guarantee -- see
        _course_codes_from_supported_options's docstring."""
        resources = {
            '/course/vs/0': {
                'x.com.samsung.da.options': ['Course_0E'],
                'x.com.samsung.da.supportedOptions': [
                    '30E5434B102D102835034B002D002845034B002D002805034B000D000'
                    '865034B002D002075000B000D000905000B000D0008D5034B002D0028'
                    'E5034B000D0008F5034B000D000'
                ],
            },
        }
        assert laundry.cycle_options(resources) == [
            '0E', '83', '84', '80', '86', '07', '90', '8D', '8E', '8F',
        ]


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


class TestCycleSelectTableGating:
    """translation_key becomes a resolver, not a plain string, once
    table_href is given -- washer/dryer's real call sites (issue: course
    codes aren't guaranteed consistent across board generations sharing
    the same /course/vs/0 contract; FlexWash's older board reports a
    different course table than every device the shipped translations
    were confirmed against). The resolved key is built from whatever table
    the device actually reports -- a table with no strings.json entries
    yet just falls through Home Assistant's own missing-translation
    handling to raw-code display, the same as any individual untranslated
    code within an existing table."""

    def _desc(self):
        return laundry.cycle_select(
            translation_key='washer_cycle', icon='x',
            table_href='/st/washercourse/vs/0',
        )

    def test_static_string_when_no_table_href_given(self):
        """dishwasher's call site -- no equivalent table-id resource in any
        dump seen, no evidence of the same cross-board risk -- keeps the
        plain static key unconditionally."""
        desc = laundry.cycle_select(translation_key='dishwasher_cycle', icon='x')
        assert desc.translation_key == 'dishwasher_cycle'

    def test_resolved_key_is_built_from_the_reported_table(self):
        desc = self._desc()
        resources = {'/st/washercourse/vs/0': {'x.com.samsung.da.st.courseTable': 'Table_02'}}
        assert callable(desc.translation_key)
        assert desc.translation_key(resources) == 'washer_cycle_table_02'

    def test_resolved_key_reflects_an_unbuilt_table_too(self):
        """No gating against a hardcoded 'known good' table -- a table we
        haven't shipped translations for yet still gets a key built for
        it, just one strings.json has nothing under (raw-code display)."""
        desc = self._desc()
        resources = {'/st/washercourse/vs/0': {'x.com.samsung.da.st.courseTable': 'Table_00'}}
        assert desc.translation_key(resources) == 'washer_cycle_table_00'

    def test_resolves_to_none_when_table_id_is_unknown(self):
        """No href, or an empty rep, gets no translation_key at all --
        there's nothing to build a key from."""
        desc = self._desc()
        assert desc.translation_key({}) is None
        assert desc.translation_key({'/st/washercourse/vs/0': {}}) is None


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
