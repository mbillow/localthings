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

    def test_reads_course_code_from_options_array(self):
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == 'cycle')
        rep = {'x.com.samsung.da.options': ['DeviceType_0167', 'Course_1C', 'GMT_04']}
        assert desc.rep_fn(rep) == '1C'

    def test_missing_course_option_returns_none(self):
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == 'cycle')
        assert desc.rep_fn({'x.com.samsung.da.options': ['GMT_04']}) is None

    def test_no_write_fn_yet(self):
        """Read-only until a verified course-name table exists."""
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == 'cycle')
        assert getattr(desc, 'write_fn', None) is None


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
        assert desc.exists_fn({'setBuzzerSound': 'On'}) is False
        assert desc.exists_fn({'supportedFinishSound': ['FinishSound_1']}) is True

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
