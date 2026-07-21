"""Tests for washer-specific capabilities.

Shared laundry capabilities (cycle-select machinery, buzzer, job status) are
tested in test_laundry_capabilities.py; the OCF power/kids-lock/remote-control
fallback pairs and the energy meter in test_common_capabilities.py.
"""
from custom_components.localthings.registry.capabilities import laundry, washer


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
        assert desc.options is laundry.cycle_options

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

    def test_translation_keys(self):
        """detergent_quantity and softener_quantity share one translation_key
        (same 00-03 -> None/Low/Medium/High vocabulary on both dispensers,
        same shape as fridge.py's shared 'brightness_level' key); hardness
        and concentration each have their own since their labels differ."""
        assert self._desc('detergent_quantity').translation_key == 'washer_dosing_quantity'
        assert self._desc('softener_quantity').translation_key == 'washer_dosing_quantity'
        assert self._desc('detergent_water_hardness').translation_key == 'washer_detergent_water_hardness'
        assert self._desc('softener_concentration').translation_key == 'washer_softener_concentration'

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
