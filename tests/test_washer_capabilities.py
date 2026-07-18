"""Tests for washer-specific capabilities."""
from custom_components.localthings.registry.capabilities import washer


def _rep_by_href(cap, href):
    assert cap.href == href
    return cap


class TestWasherSettings:
    def test_href(self):
        assert washer.WASHER_SETTINGS.href == '/washer/vs/0'

    def test_wash_temperature_read(self):
        desc = next(e for e in washer.WASHER_SETTINGS.entities if e.key == 'wash_temperature')
        assert desc.field == 'x.com.samsung.da.waterTemperature'
        assert desc.options_field == 'x.com.samsung.da.supportedWaterTemperature'

    def test_wash_temperature_write(self):
        desc = next(e for e in washer.WASHER_SETTINGS.entities if e.key == 'wash_temperature')
        path, body = desc.write_fn('60', {})
        assert path == ['washer', 'vs', '0']
        assert body == {'x.com.samsung.da.waterTemperature': '60'}

    def test_spin_speed_write(self):
        desc = next(e for e in washer.WASHER_SETTINGS.entities if e.key == 'spin_speed')
        path, body = desc.write_fn('1400', {})
        assert path == ['washer', 'vs', '0']
        assert body == {'x.com.samsung.da.spinLevel': '1400'}

    def test_rinse_cycles_write(self):
        desc = next(e for e in washer.WASHER_SETTINGS.entities if e.key == 'rinse_cycles')
        path, body = desc.write_fn('3', {})
        assert path == ['washer', 'vs', '0']
        assert body == {'x.com.samsung.da.rinseCycles': '3'}


class TestWasherCourse:
    def test_href(self):
        assert washer.WASHER_COURSE.href == '/course/vs/0'

    def test_translation_key(self):
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == 'cycle')
        assert desc.translation_key == 'washer_cycle'

    def test_reads_raw_course_code_from_options_array(self):
        """rep_fn returns the raw device code; display names come from
        strings.json via translation_key, not from Python (see select.py's
        _display())."""
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == 'cycle')
        rep = {'x.com.samsung.da.options': ['DeviceType_0167', 'Course_1C', 'GMT_04']}
        assert desc.rep_fn(rep) == '1C'

    def test_missing_course_option_returns_none(self):
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == 'cycle')
        assert desc.rep_fn({'x.com.samsung.da.options': ['GMT_04']}) is None

    def test_cycle_desc_uses_cycle_options_callable(self):
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == 'cycle')
        assert desc.options is washer._cycle_options

    def test_exists_only_when_edit_course_list_is_live(self):
        """No hardcoded course table is kept -- the selector only appears
        when a device actually populates editCourseList (see
        _cycle_options's docstring for why MostUsed_ isn't used either)."""
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == 'cycle')
        assert desc.exists_fn({}, {}) is False
        assert desc.exists_fn({}, {'/wm/editcourse/vs/0': {}}) is False
        live = {'/wm/editcourse/vs/0': {'x.com.samsung.da.editCourseList': 'EditCourseList_1C'}}
        assert desc.exists_fn({}, live) is True

    def test_cycle_write(self):
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == 'cycle')
        rep = {'x.com.samsung.da.options': ['DeviceType_0167', 'Course_1C', 'GMT_04']}
        path, body = desc.write_fn('1D', rep)
        assert path == ['course', 'vs', '0']
        assert body == {
            'x.com.samsung.da.options': ['DeviceType_0167', 'Course_1D', 'GMT_04'],
        }


class TestCycleOptions:
    def test_parses_edit_course_list(self):
        raw = 'EditCourseList_1C1D211B1E29243328262722202325322F2E30662D8F96'
        assert washer._parse_edit_course_list(raw) == [
            '1C', '1D', '21', '1B', '1E', '29', '24', '33', '28', '26', '27',
            '22', '20', '23', '25', '32', '2F', '2E', '30', '66', '2D', '8F', '96',
        ]

    def test_parse_edit_course_list_handles_missing_or_malformed(self):
        assert washer._parse_edit_course_list(None) == []
        assert washer._parse_edit_course_list('') == []
        assert washer._parse_edit_course_list('no underscore') == []

    def test_cycle_options_reads_live_edit_course_list(self):
        """A different washer model's own course list -- including a code
        ('65') never seen on the primary dump -- is used as-is; there is
        no hardcoded table to fall back to or reconcile against."""
        resources = {
            '/wm/editcourse/vs/0': {
                'x.com.samsung.da.editCourseList': 'EditCourseList_651C',
            },
        }
        assert washer._cycle_options(resources) == ['65', '1C']

    def test_cycle_options_empty_when_resource_absent(self):
        assert washer._cycle_options({}) == []

    def test_cycle_options_empty_when_resource_empty(self):
        """The second known washer dump has /wm/editcourse/vs/0 == {}."""
        resources = {'/wm/editcourse/vs/0': {}}
        assert washer._cycle_options(resources) == []


class TestDrumClean:
    def test_cycles_remaining(self):
        """DrumCleanProposal_40 - WashingTimes_3 == 37, matching a live
        app screenshot's 'Potreba cistenia po 37 cykloch'."""
        desc = next(e for e in washer.WASHER_COURSE.entities
                    if e.key == 'drum_clean_cycles_remaining')
        rep = {'x.com.samsung.da.options': ['WashingTimes_3', 'DrumCleanProposal_40']}
        assert desc.rep_fn(rep) == 37

    def test_cycles_remaining_never_negative(self):
        desc = next(e for e in washer.WASHER_COURSE.entities
                    if e.key == 'drum_clean_cycles_remaining')
        rep = {'x.com.samsung.da.options': ['WashingTimes_50', 'DrumCleanProposal_40']}
        assert desc.rep_fn(rep) == 0

    def test_cycles_remaining_missing_fields(self):
        desc = next(e for e in washer.WASHER_COURSE.entities
                    if e.key == 'drum_clean_cycles_remaining')
        assert desc.rep_fn({'x.com.samsung.da.options': []}) is None

    def test_cycles_remaining_exists_only_when_computable(self):
        desc = next(e for e in washer.WASHER_COURSE.entities
                    if e.key == 'drum_clean_cycles_remaining')
        assert desc.exists_fn({'x.com.samsung.da.options': []}, {}) is False
        rep = {'x.com.samsung.da.options': ['WashingTimes_3', 'DrumCleanProposal_40']}
        assert desc.exists_fn(rep, {}) is True

    def test_last_cleaned(self):
        """DrumCleanLog_2026-07-01T20:18:07 -> a UTC-aware datetime,
        matching the same screenshot's '10 days ago' (as of 2026-07-11)."""
        desc = next(e for e in washer.WASHER_COURSE.entities
                    if e.key == 'drum_clean_last_cleaned')
        rep = {'x.com.samsung.da.options': ['DrumCleanLog_2026-07-01T20:18:07']}
        from datetime import datetime, timezone
        assert desc.rep_fn(rep) == datetime(2026, 7, 1, 20, 18, 7, tzinfo=timezone.utc)

    def test_last_cleaned_missing(self):
        desc = next(e for e in washer.WASHER_COURSE.entities
                    if e.key == 'drum_clean_last_cleaned')
        assert desc.rep_fn({'x.com.samsung.da.options': []}) is None
        assert desc.exists_fn({'x.com.samsung.da.options': []}, {}) is False


# Raw options array from issue #9's diagnostic dump (trimmed to the fields
# relevant to detergent/softener dosing).
_DOSING_OPTIONS = [
    'Course_1C',
    'DetergentAlarm_Off',
    'SoftenerAlarm_Off',
    'DetergentLevelCtrl_3',
    'SoftenerLevelCtrl_3',
    'SupportedDetergentLevelCtrl_00010203',
    'SupportedSoftenerLevelCtrl_00010203',
    'DetergentLevel2Ctrl_2',
    'SoftenerLevel2Ctrl_2',
    'SupportedDetergentLevel2Ctrl_010203',
    'SupportedSoftenerLevel2Ctrl_010203',
]
_DOSING_RESOURCES = {'/course/vs/0': {'x.com.samsung.da.options': _DOSING_OPTIONS}}


class TestDetergentSoftenerDosing:
    @staticmethod
    def _desc(key):
        return next(e for e in washer.WASHER_COURSE.entities if e.key == key)

    def test_quantity_and_hardness_read(self):
        rep = {'x.com.samsung.da.options': _DOSING_OPTIONS}
        assert self._desc('detergent_quantity').rep_fn(rep) == '3'
        assert self._desc('detergent_water_hardness').rep_fn(rep) == '2'
        assert self._desc('softener_quantity').rep_fn(rep) == '3'
        assert self._desc('softener_concentration').rep_fn(rep) == '2'

    def test_quantity_and_hardness_options_decode_supported_list(self):
        assert self._desc('detergent_quantity').options(_DOSING_RESOURCES) == ['00', '01', '02', '03']
        assert self._desc('softener_quantity').options(_DOSING_RESOURCES) == ['00', '01', '02', '03']
        assert self._desc('detergent_water_hardness').options(_DOSING_RESOURCES) == ['01', '02', '03']
        assert self._desc('softener_concentration').options(_DOSING_RESOURCES) == ['01', '02', '03']

    def test_exists_only_when_supported_list_present(self):
        for key in ('detergent_quantity', 'detergent_water_hardness',
                    'softener_quantity', 'softener_concentration'):
            desc = self._desc(key)
            assert desc.exists_fn({}, {}) is False
            assert desc.exists_fn({}, _DOSING_RESOURCES) is True

    def test_quantity_write(self):
        rep = {'x.com.samsung.da.options': list(_DOSING_OPTIONS)}
        path, body = self._desc('detergent_quantity').write_fn('01', rep)
        assert path == ['course', 'vs', '0']
        assert 'DetergentLevelCtrl_01' in body['x.com.samsung.da.options']
        assert 'DetergentLevelCtrl_3' not in body['x.com.samsung.da.options']

    def test_hardness_write(self):
        rep = {'x.com.samsung.da.options': list(_DOSING_OPTIONS)}
        path, body = self._desc('softener_concentration').write_fn('03', rep)
        assert path == ['course', 'vs', '0']
        assert 'SoftenerLevel2Ctrl_03' in body['x.com.samsung.da.options']

    def test_low_reservoir_off_when_alarm_off(self):
        rep = {'x.com.samsung.da.options': _DOSING_OPTIONS}
        assert self._desc('detergent_low').rep_fn(rep) is False
        assert self._desc('softener_low').rep_fn(rep) is False

    def test_low_reservoir_on_when_alarm_active(self):
        opts = ['DetergentAlarm_On', 'SoftenerAlarm_On']
        rep = {'x.com.samsung.da.options': opts}
        assert self._desc('detergent_low').rep_fn(rep) is True
        assert self._desc('softener_low').rep_fn(rep) is True

    def test_low_reservoir_exists_only_when_alarm_field_present(self):
        assert self._desc('detergent_low').exists_fn({'x.com.samsung.da.options': []}, {}) is False
        rep = {'x.com.samsung.da.options': _DOSING_OPTIONS}
        assert self._desc('detergent_low').exists_fn(rep, {}) is True


class TestBuzzerSound:
    def test_href(self):
        assert washer.BUZZER_SOUND.href == '/buzzersound/vs/0'

    def test_buzzer_sound_write(self):
        desc = next(e for e in washer.BUZZER_SOUND.entities if e.key == 'buzzer_sound')
        path, body = desc.write_fn('Volume_High', {})
        assert path == ['buzzersound', 'vs', '0']
        assert body == {'setBuzzerSound': 'Volume_High'}

    def test_finish_sound_exists_only_when_supported(self):
        desc = next(e for e in washer.BUZZER_SOUND.entities if e.key == 'finish_sound')
        assert desc.exists_fn({'setBuzzerSound': 'On'}, {}) is False
        assert desc.exists_fn({'supportedFinishSound': ['FinishSound_1']}, {}) is True

    def test_finish_sound_write(self):
        desc = next(e for e in washer.BUZZER_SOUND.entities if e.key == 'finish_sound')
        path, body = desc.write_fn('FinishSound_2', {})
        assert path == ['buzzersound', 'vs', '0']
        assert body == {'setFinishSound': 'FinishSound_2'}


class TestJobBeginningStatus:
    def test_href_and_field(self):
        assert washer.WASHER_JOB_BEGINNING_STATUS.href == '/wm/jobbeginingstatus/vs/0'
        desc = washer.WASHER_JOB_BEGINNING_STATUS.entities[0]
        assert desc.field == 'x.com.samsung.da.currentStatus'


class TestPowerFallback:
    def test_generic_href(self):
        assert washer.POWER_GENERIC.href == '/power/0'

    def test_generic_read_write(self):
        desc = washer.POWER_GENERIC.entities[0]
        assert desc.value_fn(True) is True
        assert desc.value_fn(False) is False
        path, body = desc.write_fn('On', {})
        assert path == ['power', '0']
        assert body == {'value': True}
        path, body = desc.write_fn('Off', {})
        assert body == {'value': False}

    def test_vs_fallback_binds_only_when_generic_absent(self):
        assert washer.POWER_VS_FALLBACK.href == '/power/vs/0'
        assert washer.POWER_VS_FALLBACK.match_fn({}, {'/power/vs/0': {}}) is True
        assert washer.POWER_VS_FALLBACK.match_fn({}, {'/power/0': {}, '/power/vs/0': {}}) is False

    def test_vs_fallback_read_write(self):
        desc = washer.POWER_VS_FALLBACK.entities[0]
        assert desc.value_fn('On') is True
        assert desc.value_fn('Off') is False
        path, body = desc.write_fn('On', {})
        assert path == ['power', 'vs', '0']
        assert body == {'x.com.samsung.da.power': 'On'}


class TestKidsLockFallback:
    def test_generic_read_write(self):
        assert washer.KIDS_LOCK_GENERIC.href == '/kidslock/0'
        desc = washer.KIDS_LOCK_GENERIC.entities[0]
        assert desc.value_fn(True) is True
        path, body = desc.write_fn('On', {})
        assert path == ['kidslock', '0']
        assert body == {'value': True}

    def test_vs_fallback_gated(self):
        assert washer.KIDS_LOCK_VS_FALLBACK.match_fn({}, {'/kidslock/vs/0': {}}) is True
        assert washer.KIDS_LOCK_VS_FALLBACK.match_fn(
            {}, {'/kidslock/0': {}, '/kidslock/vs/0': {}}) is False


class TestRemoteControlFallback:
    def test_generic_read(self):
        assert washer.REMOTE_CONTROL_GENERIC.href == '/remotectrl/0'
        desc = washer.REMOTE_CONTROL_GENERIC.entities[0]
        assert desc.value_fn(True) is True
        assert desc.value_fn(False) is False

    def test_vs_fallback_gated(self):
        assert washer.REMOTE_CONTROL_VS_FALLBACK.match_fn({}, {'/remotectrl/vs/0': {}}) is True
        assert washer.REMOTE_CONTROL_VS_FALLBACK.match_fn(
            {}, {'/remotectrl/0': {}, '/remotectrl/vs/0': {}}) is False


class TestWasherEnergyMeter:
    """Issue #6: instantaneousPower is a dead sentinel ('-500', unchanged
    across off/idle-on/running-eco/running-fabrics states and across 3
    physical devices) on every TP1-class washer dump collected so far;
    cumulativePower is outright absent on at least one washer model."""

    def test_href(self):
        assert washer.WASHER_ENERGY_METER.href == '/energy/consumption/vs/0'

    def test_power_watts_hidden_for_dead_sentinel(self):
        desc = next(e for e in washer.WASHER_ENERGY_METER.entities if e.key == 'power_watts')
        assert desc.exists_fn({'x.com.samsung.da.instantaneousPower': '-500'}, {}) is False

    def test_power_watts_shown_for_real_value(self):
        desc = next(e for e in washer.WASHER_ENERGY_METER.entities if e.key == 'power_watts')
        assert desc.exists_fn({'x.com.samsung.da.instantaneousPower': '150'}, {}) is True

    def test_energy_kwh_hidden_when_cumulative_power_absent(self):
        desc = next(e for e in washer.WASHER_ENERGY_METER.entities if e.key == 'energy_kwh')
        assert desc.exists_fn({'x.com.samsung.da.instantaneousPower': '-500'}, {}) is False

    def test_energy_kwh_shown_when_present(self):
        desc = next(e for e in washer.WASHER_ENERGY_METER.entities if e.key == 'energy_kwh')
        assert desc.exists_fn({'x.com.samsung.da.cumulativePower': '58900'}, {}) is True
