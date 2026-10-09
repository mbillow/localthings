"""Tests for dishwasher-specific capabilities.

The shared /course/vs/0 cycle-select machinery (parse_edit_course_list,
cycle_options, cycle_write) is tested in test_laundry_capabilities.py; here we
check the dishwasher wiring and its device-specific options.
"""

import pytest

from custom_components.localthings.registry.capabilities import dishwasher
from custom_components.localthings.registry.entities import SensorDesc, SwitchDesc


class TestCycleOptions:
    def _cycle(self):
        return next(e for e in dishwasher.CYCLE_OPTIONS.entities if e.key == "cycle")

    def test_cycle_desc_uses_shared_cycle_options(self):
        desc = self._cycle()
        live = {"/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": "EditCourseList_0E82"}}
        assert desc.options(live) == ["0E", "82"]
        assert desc.translation_key == "dishwasher_cycle"

    def test_exists_only_when_edit_course_list_is_live(self):
        desc = self._cycle()
        assert desc.exists_fn({}, {}) is False
        assert desc.exists_fn({}, {"/wm/editcourse/vs/0": {}}) is False
        live = {"/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": "EditCourseList_0E"}}
        assert desc.exists_fn({}, live) is True

    def test_cycle_write_uses_raw_code_directly(self):
        desc = self._cycle()
        rep = {"x.com.samsung.da.options": ["DeviceType_0001", "Course_0E", "GMT_04"]}
        path, body = desc.write_fn("90", rep)
        assert path == ["course", "vs", "0"]
        assert body == {"x.com.samsung.da.options": ["Course_90"]}


class TestDishwasherOptions:
    def test_storm_wash_read_and_write(self):
        desc = next(
            e
            for e in dishwasher.CYCLE_OPTIONS.entities
            if e.key == "storm_wash" and isinstance(e, SwitchDesc)
        )
        assert desc.rep_fn is not None
        assert desc.rep_fn({"x.com.samsung.da.options": ["StormWashZone_On"]}) is True
        assert desc.rep_fn({"x.com.samsung.da.options": ["StormWashZone_Off"]}) is False
        assert desc.write_fn is not None
        result = desc.write_fn("Off", {"x.com.samsung.da.options": ["StormWashZone_On"]})
        assert result is not None
        path, body = result
        assert path == ["course", "vs", "0"]
        assert "StormWashZone_Off" in body["x.com.samsung.da.options"]

    def test_auto_release_exists_only_when_field_present(self):
        desc = next(
            e
            for e in dishwasher.CYCLE_OPTIONS.entities
            if e.key == "auto_release_dry" and isinstance(e, SwitchDesc)
        )
        assert desc.exists_fn is not None
        assert desc.exists_fn({"x.com.samsung.da.options": []}, {}) is False
        assert desc.exists_fn({"x.com.samsung.da.options": ["AutoDoorRelease_On"]}, {}) is True


class TestDrumClean:
    """Drum Clean+ maintenance tracking shares washer.py's (issue #9) /
    dryer.py's (issue #258) options[]-array readers -- a live dishwasher
    dump confirmed the same WashingTimes_/DrumCleanProposal_/DrumCleanLog_
    trio, so drum_clean_cycles_remaining is wired the same way here.

    drum_clean_last_cleaned is deliberately not (issue #398): a live dump
    showed DrumCleanLog_'s newest entry moving every 30-90s on its own,
    including well after a cycle had finished -- unlike the washer/dryer
    reports this reader was built from (issues #9, #258), it never settles
    on a value worth showing."""

    def test_cycles_remaining(self):
        desc = next(
            e for e in dishwasher.CYCLE_OPTIONS.entities if e.key == "drum_clean_cycles_remaining"
        )
        assert desc.rep_fn is not None
        rep = {"x.com.samsung.da.options": ["WashingTimes_18", "DrumCleanProposal_20"]}
        assert desc.rep_fn(rep) == 2

    def test_cycles_remaining_exists_only_when_computable(self):
        desc = next(
            e for e in dishwasher.CYCLE_OPTIONS.entities if e.key == "drum_clean_cycles_remaining"
        )
        assert desc.exists_fn is not None
        assert desc.exists_fn({"x.com.samsung.da.options": []}, {}) is False
        rep = {"x.com.samsung.da.options": ["WashingTimes_18", "DrumCleanProposal_20"]}
        assert desc.exists_fn(rep, {}) is True

    def test_last_cleaned_is_not_wired(self):
        assert not any(
            e.key == "drum_clean_last_cleaned" for e in dishwasher.CYCLE_OPTIONS.entities
        )


def test_diagnosis_status_is_a_translatable_enum():
    desc = next(
        e
        for e in dishwasher.DIAGNOSIS.entities
        if e.key == "diagnosis_status" and isinstance(e, SensorDesc)
    )
    assert desc.device_class == "enum"
    assert desc.options == ("ready",)
    assert desc.value_fn("Ready") == "ready"


class TestDishwasherSettings:
    """/dishwasher/vs/0 on the DW60BG750 (issue #538)."""

    @staticmethod
    def _desc(key):
        return next(e for e in dishwasher.DISHWASHER_SETTINGS.entities if e.key == key)

    def test_speed_booster_reads_and_writes(self):
        desc = self._desc("speed_booster")
        assert desc.value_fn("On") is True
        assert desc.value_fn("Off") is False
        assert desc.write_fn("On", {}) == (
            ["dishwasher", "vs", "0"],
            {"x.com.samsung.da.speedBooster": "On"},
        )

    def test_wash_zone_writes_the_raw_code(self):
        assert self._desc("wash_zone").write_fn("OFF_ON", {}) == (
            ["dishwasher", "vs", "0"],
            {"x.com.samsung.da.selectedZone": "OFF_ON"},
        )

    @pytest.mark.parametrize("key", ["sanitize", "speed_booster", "heated_dry", "wash_zone"])
    def test_absent_field_binds_nothing(self, key):
        assert self._desc(key).exists_fn({}, {}) is False

    @pytest.mark.parametrize("key", ["sanitize", "speed_booster", "heated_dry", "wash_zone"])
    def test_not_yet_fetched_stub_still_binds(self, key):
        assert self._desc(key).exists_fn({"href": "/dishwasher/vs/0"}, {}) is True
