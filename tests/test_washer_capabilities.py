"""Tests for washer-specific capabilities.

Shared laundry capabilities (cycle-select machinery, buzzer, job status) are
tested in test_laundry_capabilities.py; the OCF power/kids-lock/remote-control
fallback pairs and the energy meter in test_common_capabilities.py.
"""

from datetime import UTC

from custom_components.localthings.registry.capabilities import washer
from custom_components.localthings.registry.entities import SelectDesc
from tests.conftest import _load_device


def _rep_by_href(cap, href):
    assert cap.href == href
    return cap


class TestWasherSettings:
    def test_href(self):
        assert washer.WASHER_SETTINGS.href == "/washer/vs/0"

    def test_wash_temperature_read(self):
        desc = next(
            e
            for e in washer.WASHER_SETTINGS.entities
            if e.key == "wash_temperature" and isinstance(e, SelectDesc)
        )
        assert desc.field == "x.com.samsung.da.waterTemperature"
        assert callable(desc.options)

    def test_wash_temperature_write(self):
        desc = next(
            e
            for e in washer.WASHER_SETTINGS.entities
            if e.key == "wash_temperature" and isinstance(e, SelectDesc)
        )
        assert desc.write_fn is not None
        result = desc.write_fn("60", {})
        assert result is not None
        path, body = result
        assert path == ["washer", "vs", "0"]
        assert body == {"x.com.samsung.da.waterTemperature": "60"}

    def test_spin_speed_write(self):
        desc = next(
            e
            for e in washer.WASHER_SETTINGS.entities
            if e.key == "spin_speed" and isinstance(e, SelectDesc)
        )
        assert desc.write_fn is not None
        result = desc.write_fn("1400", {})
        assert result is not None
        path, body = result
        assert path == ["washer", "vs", "0"]
        assert body == {"x.com.samsung.da.spinLevel": "1400"}

    def test_rinse_cycles_write(self):
        desc = next(
            e
            for e in washer.WASHER_SETTINGS.entities
            if e.key == "rinse_cycles" and isinstance(e, SelectDesc)
        )
        assert desc.write_fn is not None
        result = desc.write_fn("3", {})
        assert result is not None
        path, body = result
        assert path == ["washer", "vs", "0"]
        assert body == {"x.com.samsung.da.rinseCycles": "3"}


class TestDryLevel:
    """Washer/dryer combo units carry a writable dryLevel field on
    /washer/vs/0 itself, self-gated off on plain washers -- issue #22."""

    def test_exists_only_when_supported_dry_level_present(self):
        desc = next(e for e in washer.WASHER_SETTINGS.entities if e.key == "dry_level")
        assert desc.exists_fn is not None
        assert desc.exists_fn({"x.com.samsung.da.supportedDryLevel": ["None", "30"]}, {}) is True
        assert desc.exists_fn({}, {}) is False

    def test_write(self):
        desc = next(
            e
            for e in washer.WASHER_SETTINGS.entities
            if e.key == "dry_level" and isinstance(e, SelectDesc)
        )
        assert desc.write_fn is not None
        result = desc.write_fn("Cupboard", {})
        assert result is not None
        path, body = result
        assert path == ["washer", "vs", "0"]
        assert body == {"x.com.samsung.da.dryLevel": "Cupboard"}


class TestWasherCourse:
    def test_href(self):
        assert washer.WASHER_COURSE.href == "/course/vs/0"

    def test_translation_key(self):
        """Table-scoped (issue: course codes aren't guaranteed consistent
        across board generations sharing /course/vs/0 -- a device reporting
        an unrecognized table id must not borrow another board generation's
        labels) -- see laundry.cycle_select. Only a verified table gets
        table-specific state translations."""
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == "cycle")
        assert callable(desc.translation_key)
        table_02 = {"/st/washercourse/vs/0": {"x.com.samsung.da.st.courseTable": "Table_02"}}
        assert desc.translation_key(table_02) == "washer_cycle_table_02"
        table_00 = {"/st/washercourse/vs/0": {"x.com.samsung.da.st.courseTable": "Table_00"}}
        assert desc.translation_key(table_00) == "washer_cycle_table_00"
        unrecognized = {"/st/washercourse/vs/0": {"x.com.samsung.da.st.courseTable": "Table_99"}}
        assert desc.translation_key(unrecognized) == "cycle"
        assert desc.translation_key({}) == "cycle"

    def test_reads_raw_course_code_from_options_array(self):
        """rep_fn returns the raw device code; display names come from
        translations/en.json via translation_key, not Python (see select.py's
        _display())."""
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == "cycle")
        assert desc.rep_fn is not None
        rep = {"x.com.samsung.da.options": ["DeviceType_0167", "Course_1C", "GMT_04"]}
        assert desc.rep_fn(rep) == "1C"

    def test_reported_table_02_course_codes_are_translated(self):
        """The owner confirmed this washer's newer Table_02 course family."""
        from custom_components.localthings.catalog import translated_states

        confirmed = {
            "69",
            "6a",
            "6b",
            "6c",
            "6d",
            "6e",
            "6f",
            "70",
            "71",
            "72",
            "73",
            "74",
            "75",
            "76",
            "77",
            "78",
            "79",
            "88",
        }
        assert confirmed <= translated_states("select", "washer_cycle_table_02")

    def test_reported_table_00_course_codes_are_translated(self):
        """The reporter confirmed these codes on a WF45R6300AW/US by
        selecting each cycle and reading back the raw course code
        (issue #357)."""
        from custom_components.localthings.catalog import translated_states

        confirmed = {"01", "70", "55", "71", "72", "77", "57", "73", "74", "75", "78"}
        assert confirmed <= translated_states("select", "washer_cycle_table_00")

    def test_ww6500_table_00_course_codes_are_translated(self):
        """A disjoint 5B-6B slice of the same table as the WF45R6300AW
        above, derived from the shipped dump rather than retyped here: a
        course list that grows, or picks up a code the catalog has no label
        for, fails here instead of rendering as raw hex. This dump's
        editCourseList is empty (issue #1), which is why the codes come from
        supportedOptions."""
        from custom_components.localthings.catalog import translated_states
        from custom_components.localthings.registry.capabilities.laundry import cycle_options

        resources = _load_device("washer_ww6500")
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == "cycle")
        assert desc.translation_key(resources) == "washer_cycle_table_00"
        codes = cycle_options(resources)
        assert len(codes) == 14
        assert {code.lower() for code in codes} <= translated_states(
            "select", "washer_cycle_table_00"
        )

    def test_missing_course_option_returns_none(self):
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == "cycle")
        assert desc.rep_fn is not None
        assert desc.rep_fn({"x.com.samsung.da.options": ["GMT_04"]}) is None

    def test_cycle_desc_uses_cycle_options_callable(self):
        desc = next(
            e
            for e in washer.WASHER_COURSE.entities
            if e.key == "cycle" and isinstance(e, SelectDesc)
        )
        # Behavior, not identity: cycle_select wraps both hooks to fold in
        # cloud "Download" programs (issue #342), so the plain functions are
        # no longer handed through as-is.
        live = {"/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": "EditCourseList_1C1D"}}
        assert desc.options(live) == ["1C", "1D"]
        # display_fn still resolves a personal course name through
        # washer_cycle_fallback for a non-cloud value.
        assert desc.display_fn is not None
        personal = {
            "/wm/personalcourse/vs/0": {
                "x.com.samsung.da.courses": ["A1_01044D79436F"],
            }
        }
        assert desc.display_fn("A1", personal) == "MyCo"

    def test_exists_only_when_edit_course_list_is_live(self):
        """No hardcoded course table is kept -- the selector only appears
        when a device actually populates editCourseList (see
        _cycle_options's docstring for why MostUsed_ isn't used either)."""
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == "cycle")
        assert desc.exists_fn is not None
        assert desc.exists_fn({}, {}) is False
        assert desc.exists_fn({}, {"/wm/editcourse/vs/0": {}}) is False
        live = {"/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": "EditCourseList_1C"}}
        assert desc.exists_fn({}, live) is True

    def test_cycle_write(self):
        """Confirmed on real hardware (issue #54): the write only needs to
        carry the changed token -- the device matches by prefix, evicts the
        stale token, and merges the result into the array itself."""
        desc = next(
            e
            for e in washer.WASHER_COURSE.entities
            if e.key == "cycle" and isinstance(e, SelectDesc)
        )
        assert desc.write_fn is not None
        rep = {"x.com.samsung.da.options": ["DeviceType_0167", "Course_1C", "GMT_04"]}
        result = desc.write_fn("1D", rep)
        assert result is not None
        path, body = result
        assert path == ["course", "vs", "0"]
        assert body == {"x.com.samsung.da.options": ["Course_1D"]}


class TestDrumClean:
    def test_cycles_remaining(self):
        """DrumCleanProposal_40 - WashingTimes_3 == 37, matching a live
        app screenshot's 'Potreba cistenia po 37 cykloch'."""
        desc = next(
            e for e in washer.WASHER_COURSE.entities if e.key == "drum_clean_cycles_remaining"
        )
        assert desc.rep_fn is not None
        rep = {"x.com.samsung.da.options": ["WashingTimes_3", "DrumCleanProposal_40"]}
        assert desc.rep_fn(rep) == 37

    def test_cycles_remaining_never_negative(self):
        desc = next(
            e for e in washer.WASHER_COURSE.entities if e.key == "drum_clean_cycles_remaining"
        )
        assert desc.rep_fn is not None
        rep = {"x.com.samsung.da.options": ["WashingTimes_50", "DrumCleanProposal_40"]}
        assert desc.rep_fn(rep) == 0

    def test_cycles_remaining_missing_fields(self):
        desc = next(
            e for e in washer.WASHER_COURSE.entities if e.key == "drum_clean_cycles_remaining"
        )
        assert desc.rep_fn is not None
        assert desc.rep_fn({"x.com.samsung.da.options": []}) is None

    def test_cycles_remaining_exists_only_when_computable(self):
        desc = next(
            e for e in washer.WASHER_COURSE.entities if e.key == "drum_clean_cycles_remaining"
        )
        assert desc.exists_fn is not None
        assert desc.exists_fn({"x.com.samsung.da.options": []}, {}) is False
        rep = {"x.com.samsung.da.options": ["WashingTimes_3", "DrumCleanProposal_40"]}
        assert desc.exists_fn(rep, {}) is True

    def test_last_cleaned(self):
        """DrumCleanLog_2026-07-01T20:18:07 -> a UTC-aware datetime,
        matching the same screenshot's '10 days ago' (as of 2026-07-11)."""
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == "drum_clean_last_cleaned")
        assert desc.rep_fn is not None
        rep = {"x.com.samsung.da.options": ["DrumCleanLog_2026-07-01T20:18:07"]}
        from datetime import datetime

        assert desc.rep_fn(rep) == datetime(2026, 7, 1, 20, 18, 7, tzinfo=UTC)

    def test_last_cleaned_missing(self):
        desc = next(e for e in washer.WASHER_COURSE.entities if e.key == "drum_clean_last_cleaned")
        assert desc.rep_fn is not None
        assert desc.rep_fn({"x.com.samsung.da.options": []}) is None
        assert desc.exists_fn is not None
        assert desc.exists_fn({"x.com.samsung.da.options": []}, {}) is False


# Raw options array from issue #9's diagnostic dump (trimmed to the fields
# relevant to detergent/softener dosing).
_DOSING_OPTIONS = [
    "Course_1C",
    "DetergentAlarm_Off",
    "SoftenerAlarm_Off",
    "DetergentLevelCtrl_3",
    "SoftenerLevelCtrl_3",
    "SupportedDetergentLevelCtrl_00010203",
    "SupportedSoftenerLevelCtrl_00010203",
    "DetergentLevel2Ctrl_2",
    "SoftenerLevel2Ctrl_2",
    "SupportedDetergentLevel2Ctrl_010203",
    "SupportedSoftenerLevel2Ctrl_010203",
]
_DOSING_RESOURCES = {"/course/vs/0": {"x.com.samsung.da.options": _DOSING_OPTIONS}}


class TestDetergentSoftenerDosing:
    @staticmethod
    def _desc(key):
        return next(e for e in washer.WASHER_COURSE.entities if e.key == key)

    def test_quantity_and_hardness_read(self):
        """The device reports the level un-padded ('3'), but the options and
        translation keys are zero-padded supported codes ('03'); rep_fn
        normalizes to the supported code so the value is a valid option
        (issue #9 -- otherwise HA renders the select 'unknown')."""
        rep = {"x.com.samsung.da.options": _DOSING_OPTIONS}
        assert self._desc("detergent_quantity").rep_fn(rep) == "03"
        assert self._desc("detergent_water_hardness").rep_fn(rep) == "02"
        assert self._desc("softener_quantity").rep_fn(rep) == "03"
        assert self._desc("softener_concentration").rep_fn(rep) == "02"

    def test_current_value_is_a_valid_option_for_every_dosing_select(self):
        """The core regression: HA shows a select 'unknown' when current_option
        is not in options. Each dosing select's value must be one of its own
        options."""
        rep = {"x.com.samsung.da.options": _DOSING_OPTIONS}
        for key in (
            "detergent_quantity",
            "detergent_water_hardness",
            "softener_quantity",
            "softener_concentration",
        ):
            desc = self._desc(key)
            assert desc.rep_fn(rep) in desc.options(_DOSING_RESOURCES), key

    def test_read_passes_through_when_no_supported_match(self):
        """A value with no matching supported code is returned as-is rather than
        dropped, so an unexpected device stays visible instead of blank."""
        rep = {
            "x.com.samsung.da.options": [
                "DetergentLevelCtrl_7",
                "SupportedDetergentLevelCtrl_00010203",
            ]
        }
        assert self._desc("detergent_quantity").rep_fn(rep) == "7"

    def test_translation_keys(self):
        """Each entity gets its own translated name and state vocabulary."""
        assert self._desc("detergent_quantity").translation_key == "detergent_quantity"
        assert self._desc("softener_quantity").translation_key == "softener_quantity"
        assert self._desc("detergent_water_hardness").translation_key == "detergent_water_hardness"
        assert self._desc("softener_concentration").translation_key == "softener_concentration"

    def test_quantity_and_hardness_options_decode_supported_list(self):
        assert self._desc("detergent_quantity").options(_DOSING_RESOURCES) == [
            "00",
            "01",
            "02",
            "03",
        ]
        assert self._desc("softener_quantity").options(_DOSING_RESOURCES) == [
            "00",
            "01",
            "02",
            "03",
        ]
        assert self._desc("detergent_water_hardness").options(_DOSING_RESOURCES) == [
            "01",
            "02",
            "03",
        ]
        assert self._desc("softener_concentration").options(_DOSING_RESOURCES) == ["01", "02", "03"]

    def test_exists_only_when_supported_list_present(self):
        for key in (
            "detergent_quantity",
            "detergent_water_hardness",
            "softener_quantity",
            "softener_concentration",
        ):
            desc = self._desc(key)
            assert desc.exists_fn({}, {}) is False
            assert desc.exists_fn({}, _DOSING_RESOURCES) is True

    def test_quantity_write(self):
        """The UI selects a padded supported code ('01'); the write posts the
        un-padded device code ('1'), mirroring how the device reports it.

        Confirmed on real hardware (issue #54): the write only needs to carry
        the changed token -- the device matches by prefix, evicts the stale
        token, and merges the result into the array itself. No need to read
        the current array back and rewrite it whole."""
        rep = {"x.com.samsung.da.options": list(_DOSING_OPTIONS)}
        path, body = self._desc("detergent_quantity").write_fn("01", rep)
        assert path == ["course", "vs", "0"]
        assert body == {"x.com.samsung.da.options": ["DetergentLevelCtrl_1"]}

    def test_hardness_write(self):
        rep = {"x.com.samsung.da.options": list(_DOSING_OPTIONS)}
        path, body = self._desc("softener_concentration").write_fn("03", rep)
        assert path == ["course", "vs", "0"]
        assert body == {"x.com.samsung.da.options": ["SoftenerLevel2Ctrl_3"]}

    def test_low_reservoir_off_when_alarm_off(self):
        rep = {"x.com.samsung.da.options": _DOSING_OPTIONS}
        assert self._desc("detergent_low").rep_fn(rep) is False
        assert self._desc("softener_low").rep_fn(rep) is False

    def test_low_reservoir_on_when_alarm_active(self):
        opts = ["DetergentAlarm_On", "SoftenerAlarm_On"]
        rep = {"x.com.samsung.da.options": opts}
        assert self._desc("detergent_low").rep_fn(rep) is True
        assert self._desc("softener_low").rep_fn(rep) is True

    def test_low_reservoir_exists_only_when_alarm_field_present(self):
        assert self._desc("detergent_low").exists_fn({"x.com.samsung.da.options": []}, {}) is False
        rep = {"x.com.samsung.da.options": _DOSING_OPTIONS}
        assert self._desc("detergent_low").exists_fn(rep, {}) is True


class TestWashOptionToggles:
    """Bubble soak / pre-wash / intensive-wash switches, from the same
    options[] array as the cycle select (issue #22 follow-up). Confirmed
    On/Off shape from a dump with Bubble Soak toggled on in the app."""

    @staticmethod
    def _desc(key):
        return next(e for e in washer.WASHER_COURSE.entities if e.key == key)

    @staticmethod
    def _keys():
        return ("bubble_soak", "pre_wash", "intensive")

    @staticmethod
    def _prefix(key):
        return {
            "bubble_soak": "BubbleSoak",
            "pre_wash": "PreWashSetting",
            "intensive": "IntensiveSetting",
        }[key]

    def test_exists_only_when_field_present(self):
        for key in self._keys():
            desc = self._desc(key)
            assert desc.exists_fn({"x.com.samsung.da.options": []}, {}) is False
            prefix = self._prefix(key)
            rep = {"x.com.samsung.da.options": [f"{prefix}_Off"]}
            assert desc.exists_fn(rep, {}) is True

    def test_reads_off(self):
        for key in self._keys():
            prefix = self._prefix(key)
            rep = {"x.com.samsung.da.options": [f"{prefix}_Off"]}
            assert self._desc(key).rep_fn(rep) is False

    def test_reads_on(self):
        for key in self._keys():
            prefix = self._prefix(key)
            rep = {"x.com.samsung.da.options": [f"{prefix}_On"]}
            assert self._desc(key).rep_fn(rep) is True

    def test_write_on_and_off(self):
        """write_fn receives the same 'On'/'Off' string switch.py sends
        (not a bool) -- covers a bug where an earlier `'On' if p else 'Off'`
        implementation always wrote 'On', since any non-empty string
        (including 'Off') is truthy.

        The write body carries only the changed token (issue #54: confirmed
        the device merges by prefix itself), not the whole options array."""
        for key in self._keys():
            prefix = self._prefix(key)
            rep = {"x.com.samsung.da.options": [f"{prefix}_Off", "GMT_02"]}
            path, body = self._desc(key).write_fn("On", rep)
            assert path == ["course", "vs", "0"]
            assert body == {"x.com.samsung.da.options": [f"{prefix}_On"]}

            rep = {"x.com.samsung.da.options": [f"{prefix}_On"]}
            path, body = self._desc(key).write_fn("Off", rep)
            assert body == {"x.com.samsung.da.options": [f"{prefix}_Off"]}
            assert f"{prefix}_On" not in body["x.com.samsung.da.options"]

    def test_write_rejects_non_on_off_payload(self):
        for key in self._keys():
            prefix = self._prefix(key)
            rep = {"x.com.samsung.da.options": [f"{prefix}_Off"]}
            assert self._desc(key).write_fn("bogus", rep) is None


# editCourseList and availability bitmaps from the reporter's issue #22
# follow-up dump (WD90T654DBN/S1, course '30' selected, Bubble Soak just
# turned on in the app): 24 courses, course '30' at position 1 reads 'F0'
# (available) on all three bitmaps; course '1C' at position 0 reads '00' on
# BubbleSoakSet (matching the app graying that control out for Eco 40-60).
_EDIT_COURSE_RESOURCES = {
    "/wm/editcourse/vs/0": {
        "x.com.samsung.da.editCourseList": "EditCourseList_1C301E26361B1D1F253324322022232F212D272838393729",  # noqa: E501
    },
}
_BUBBLE_SOAK_SET = "BubbleSoakSet_00F000F000F000F0F0F0F00000F000F0F00000F000000000"
_PRE_WASH_AVAILABLE_SET = "PreWashAvailableSet_F0F000F0F0F000F0F0F0F00000F0F0F0F00000F000000000"
_INTENSIVE_AVAILABLE_SET = "IntensiveAvailableSet_F0F000F0F0F000F0F0F0F00000F0F0F0F00000F000000000"


class TestWashOptionToggleValidation:
    """validate_fn rejects turning a toggle on for a course whose byte in
    its availability bitmap isn't 'F0', with a user-facing message switch.py
    raises as ServiceValidationError -- distinct from write_fn's silent
    no-op for a malformed payload."""

    @staticmethod
    def _desc(key):
        return next(e for e in washer.WASHER_COURSE.entities if e.key == key)

    def test_allowed_on_a_supported_course(self):
        rep = {"x.com.samsung.da.options": ["Course_30", _BUBBLE_SOAK_SET]}
        assert self._desc("bubble_soak").validate_fn("On", rep, _EDIT_COURSE_RESOURCES) is None

    def test_rejected_on_an_unsupported_course(self):
        rep = {"x.com.samsung.da.options": ["Course_1C", _BUBBLE_SOAK_SET]}
        translation_key = self._desc("bubble_soak").validate_fn("On", rep, _EDIT_COURSE_RESOURCES)
        assert translation_key == "bubble_soak_unavailable_for_cycle"

    def test_pre_wash_and_intensive_use_their_own_availableset_field(self):
        rep = {"x.com.samsung.da.options": ["Course_30", _PRE_WASH_AVAILABLE_SET]}
        assert self._desc("pre_wash").validate_fn("On", rep, _EDIT_COURSE_RESOURCES) is None
        rep = {"x.com.samsung.da.options": ["Course_30", _INTENSIVE_AVAILABLE_SET]}
        assert self._desc("intensive").validate_fn("On", rep, _EDIT_COURSE_RESOURCES) is None

    def test_turning_off_is_never_blocked(self):
        rep = {"x.com.samsung.da.options": ["Course_1C", _BUBBLE_SOAK_SET]}
        assert self._desc("bubble_soak").validate_fn("Off", rep, _EDIT_COURSE_RESOURCES) is None

    def test_allows_write_when_course_unresolvable(self):
        """No editCourseList, no Course_ token, or a bitmap whose length
        doesn't match editCourseList -- in every case, fail open rather than
        block a write we can't actually verify."""
        desc = self._desc("bubble_soak")
        rep = {"x.com.samsung.da.options": ["Course_1C", _BUBBLE_SOAK_SET]}
        assert desc.validate_fn("On", rep, {}) is None

        rep = {"x.com.samsung.da.options": [_BUBBLE_SOAK_SET]}
        assert desc.validate_fn("On", rep, _EDIT_COURSE_RESOURCES) is None

        rep = {"x.com.samsung.da.options": ["Course_1C", "BubbleSoakSet_00F0"]}
        assert desc.validate_fn("On", rep, _EDIT_COURSE_RESOURCES) is None


class TestAiEnergyLevel:
    """Issue #40 -- /energy/ailevel/vs/0 was unbound on a plain washer.

    The capability itself (common.AI_ENERGY_LEVEL) is tested in
    test_common_capabilities.py; this just confirms it's wired into the
    washer registry and that the fixture's single-entry supportedAiLevel
    (['1'], matching the issue's dump) surfaces as a switch, not a select."""

    def test_fixture_has_complete_coverage(self):
        from custom_components.localthings.registry.adapter import flatten
        from custom_components.localthings.registry.by_type import washer as washer_registry
        from custom_components.localthings.registry.discovery import discover
        from tests.conftest import _load_device

        resources = _load_device("washer")
        unbound = []
        bound = discover(
            resources,
            washer_registry.REGISTRY.capabilities,
            washer_registry.REGISTRY.pattern_capabilities,
            log=unbound.append,
        )
        assert unbound == []
        state = flatten(bound, resources)
        assert state["ai_energy_level"] is False  # aiLevel '0' -- off


class TestFlexWashAndComboFixturesHaveCompleteCoverage:
    """FlexWash (issue #19, previously unrecognized entirely) and
    washer/dryer combo (issue #22, dry_level) dumps must both resolve to
    zero unbound hrefs."""

    def test_flexwash(self):
        from custom_components.localthings.registry.adapter import flatten
        from custom_components.localthings.registry.by_type import washer as washer_registry
        from custom_components.localthings.registry.discovery import discover
        from tests.conftest import _load_device

        resources = _load_device("washer_flexwash")
        unbound = []
        bound = discover(
            resources,
            washer_registry.REGISTRY.capabilities,
            washer_registry.REGISTRY.pattern_capabilities,
            log=unbound.append,
        )
        assert unbound == []
        state = flatten(bound, resources)
        assert "dry_level" not in state  # plain washer -- no supportedDryLevel field

    def test_dryer_combo(self):
        from custom_components.localthings.registry.adapter import flatten
        from custom_components.localthings.registry.by_type import washer as washer_registry
        from custom_components.localthings.registry.discovery import discover
        from tests.conftest import _load_device

        resources = _load_device("washer_dryer_combo")
        unbound = []
        bound = discover(
            resources,
            washer_registry.REGISTRY.capabilities,
            washer_registry.REGISTRY.pattern_capabilities,
            log=unbound.append,
        )
        assert unbound == []
        state = flatten(bound, resources)
        # the device's own "off" sentinel is the literal string 'None', not absence
        assert state["dry_level"] == "None"


def _settings(key):
    return next(
        e for e in washer.WASHER_SETTINGS.entities if e.key == key and isinstance(e, SelectDesc)
    )


def _ww6500(course=None, **washer_fields):
    """The WW6500 dump, optionally on another course or with other live
    wash settings."""
    resources = _load_device("washer_ww6500")
    course_rep = dict(resources["/course/vs/0"])
    if course is not None:
        course_rep["x.com.samsung.da.options"] = [
            f"Course_{course}" if o.startswith("Course_") else o
            for o in course_rep["x.com.samsung.da.options"]
        ]
    washer_rep = dict(resources["/washer/vs/0"])
    washer_rep.update({f"x.com.samsung.da.{k}": v for k, v in washer_fields.items()})
    return {**resources, "/course/vs/0": course_rep, "/washer/vs/0": washer_rep}


class TestCourseNarrowedWashSettings:
    """The three wash dials narrow to the selected course, from the same
    supportedOptions decode the dryer uses. The WW6500's records are the ones
    0x8/0x9/0xA were named on."""

    def test_extra_speed_offers_only_what_its_course_allows(self):
        resources = _ww6500("5C")

        assert _settings("wash_temperature").options(resources) == ["Cold", "20", "30", "40"]
        assert _settings("rinse_cycles").options(resources) == ["0", "1", "2", "3", "4", "5"]
        assert _settings("spin_speed").options(resources) == [
            "RinseHold",
            "NoSpin",
            "400",
            "800",
            "1200",
            "1400",
        ]

    def test_delicates_tops_out_at_400_rpm(self):
        resources = _ww6500("5E", spinLevel="400")

        assert _settings("spin_speed").options(resources) == ["RinseHold", "NoSpin", "400"]

    def test_a_live_value_the_course_does_not_allow_stays_selectable(self):
        """Mid course change the board can still report the last course's
        setting; dropping it would read the select as unknown."""
        resources = _ww6500("63", waterTemperature="30")

        assert _settings("wash_temperature").options(resources) == ["30", "60"]


class TestHotWashRinses:
    """A 95C wash rinses at least twice on these device types, which is why
    a WW6500 refuses 0 and 1 rinses on Baby Care although its mask allows
    them."""

    def test_95c_hides_fewer_than_two_rinses(self):
        resources = _ww6500("5F", waterTemperature="95", rinseCycles="4")

        assert _settings("rinse_cycles").options(resources) == ["2", "3", "4", "5"]

    def test_a_cooler_wash_on_the_same_course_keeps_every_count(self):
        resources = _ww6500("5F", waterTemperature="40", rinseCycles="4")

        assert _settings("rinse_cycles").options(resources) == ["0", "1", "2", "3", "4", "5"]

    def test_the_live_count_is_never_hidden(self):
        resources = _ww6500("5F", waterTemperature="95", rinseCycles="1")

        assert _settings("rinse_cycles").options(resources) == ["1", "2", "3", "4", "5"]

    def test_other_device_types_are_left_alone(self):
        resources = _ww6500("5F", waterTemperature="95", rinseCycles="4")
        course_rep = dict(resources["/course/vs/0"])
        course_rep["x.com.samsung.da.options"] = [
            "DeviceType_0146" if o.startswith("DeviceType_") else o
            for o in course_rep["x.com.samsung.da.options"]
        ]
        resources["/course/vs/0"] = course_rep

        assert _settings("rinse_cycles").options(resources) == ["0", "1", "2", "3", "4", "5"]


class TestDrumCleanTemperature:
    """Drum Clean reports and takes 60C, but runs at the 70C Samsung's app
    shows on a board that heats past 60C."""

    def test_drum_clean_shows_70(self):
        resources = _ww6500("63", waterTemperature="60")

        assert _settings("wash_temperature").display_fn("60", resources) == "70"

    def test_another_course_at_60_shows_60(self):
        resources = _ww6500("5B", waterTemperature="60")

        assert _settings("wash_temperature").display_fn("60", resources) is None

    def test_a_board_that_heats_no_higher_than_60_shows_60(self):
        resources = _ww6500("63", waterTemperature="60")
        washer_rep = dict(resources["/washer/vs/0"])
        washer_rep["x.com.samsung.da.supportedWaterTemperature"] = ["Cold", "20", "40", "60"]
        resources["/washer/vs/0"] = washer_rep

        assert _settings("wash_temperature").display_fn("60", resources) is None

    def test_the_same_code_on_another_course_table_shows_60(self):
        resources = _ww6500("63", waterTemperature="60")
        resources["/st/washercourse/vs/0"] = {"x.com.samsung.da.st.courseTable": "Table_02"}

        assert _settings("wash_temperature").display_fn("60", resources) is None


def _course_desc(key):
    return next(e for e in washer.WASHER_COURSE.entities if e.key == key)


class TestLaundryOutTime:
    def test_reads_the_reminder_interval(self):
        desc = _course_desc("laundry_out_time")
        rep = {"x.com.samsung.da.options": ["Course_5C", "LaundryOutTime_60"]}

        assert desc.exists_fn(rep, {}) is True
        assert desc.rep_fn(rep) == "60"

    def test_off_is_zero(self):
        rep = {"x.com.samsung.da.options": ["LaundryOutTime_0"]}

        assert _course_desc("laundry_out_time").rep_fn(rep) == "0"

    def test_a_value_outside_the_four_intervals_binds_nothing(self):
        """A onebody combo reports LaundryOutTime_158, which is not this."""
        rep = {"x.com.samsung.da.options": ["LaundryOutTime_158"]}

        assert _course_desc("laundry_out_time").exists_fn(rep, {}) is False

    def test_write_is_one_token(self):
        rep = {"x.com.samsung.da.options": ["LaundryOutTime_0"]}

        assert _course_desc("laundry_out_time").write_fn("90", rep) == (
            ["course", "vs", "0"],
            {"x.com.samsung.da.options": ["LaundryOutTime_90"]},
        )

    def test_write_refuses_another_interval(self):
        rep = {"x.com.samsung.da.options": ["LaundryOutTime_0"]}

        assert _course_desc("laundry_out_time").write_fn("45", rep) is None


class TestQuickWash:
    def test_not_used_is_its_own_state(self):
        rep = {"x.com.samsung.da.options": ["QuickWash_Not_Used"]}

        assert _course_desc("quick_wash").rep_fn(rep) == "not_used"

    def test_off(self):
        rep = {"x.com.samsung.da.options": ["QuickWash_Off", "QuickWashSet_5B847E933FA53F"]}

        assert _course_desc("quick_wash").rep_fn(rep) == "off"

    def test_absent_binds_nothing(self):
        rep = {"x.com.samsung.da.options": ["Course_5C"]}

        assert _course_desc("quick_wash").exists_fn(rep, {}) is False


def _operational_desc(key):
    return next(e for e in washer.WASHER_OPERATIONAL_STATE.entities if e.key == key)


class TestSupportedProgressChoices:
    """supportedProgress gains Prewash and Delaywash while those are chosen."""

    def test_pre_wash_selected(self):
        desc = _operational_desc("pre_wash_selected")
        rep = {"x.com.samsung.da.supportedProgress": ["None", "Prewash", "Wash", "Finish"]}

        assert desc.exists_fn(rep, {}) is True
        assert desc.rep_fn(rep) is True

    def test_nothing_chosen(self):
        rep = {"x.com.samsung.da.supportedProgress": ["None", "Wash", "Rinse", "Spin", "Finish"]}

        assert _operational_desc("pre_wash_selected").rep_fn(rep) is False
        assert _operational_desc("delay_wash_set").rep_fn(rep) is False

    def test_delay_wash_set(self):
        rep = {"x.com.samsung.da.supportedProgress": ["None", "Delaywash", "Wash", "Finish"]}

        assert _operational_desc("delay_wash_set").rep_fn(rep) is True

    def test_a_board_with_the_pre_wash_switch_gets_no_duplicate(self):
        rep = {"x.com.samsung.da.supportedProgress": ["None", "Wash"]}
        resources = {"/course/vs/0": {"x.com.samsung.da.options": ["PreWashSetting_Off"]}}

        assert _operational_desc("pre_wash_selected").exists_fn(rep, resources) is False

    def test_a_board_reporting_its_delay_gets_no_duplicate(self):
        rep = {
            "x.com.samsung.da.supportedProgress": ["None", "Wash"],
            "x.com.samsung.da.delayEndTime": "00:00:00",
        }

        assert _operational_desc("delay_wash_set").exists_fn(rep, {}) is False

    def test_the_shared_capability_is_unchanged(self):
        from custom_components.localthings.registry.capabilities.operational import (
            OPERATIONAL_STATE,
        )

        keys = {e.key for e in OPERATIONAL_STATE.entities}
        assert "pre_wash_selected" not in keys
        assert keys < {e.key for e in washer.WASHER_OPERATIONAL_STATE.entities}

    def test_the_microfiber_filter_gets_neither(self):
        rep = {"x.com.samsung.da.supportedProgress": ["None", "Filtering", "Bypassing"]}

        assert _operational_desc("pre_wash_selected").exists_fn(rep, {}) is False
        assert _operational_desc("delay_wash_set").exists_fn(rep, {}) is False
