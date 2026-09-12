"""Tests for dryer support and washer/dryer consistency (issue #14)."""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model, resolve
from custom_components.localthings.registry.capabilities import dryer, ignored
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc
from tests.conftest import _load_device


def _dryer():
    resources = _load_device("dryer")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def _state():
    reg, resources = _dryer()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_dryer_model_resolves_to_dryer_registry():
    reg, _ = _dryer()
    assert reg is not None and reg.name == "dryer"


def test_no_unbound_hrefs():
    """Every resource in the issue #14 dump binds or is ignored -- clears the
    coverage-gap repair."""
    reg, resources = _dryer()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_entities_present():
    state = _state()
    for key in (
        "buzzer_sound",
        "cycle",
        "power_switch",
        "child_lock",
        "remote_control",
        "dry_level",
        "wrinkle_prevent",
        "energy_kwh",
    ):
        assert key in state, key


def test_dry_level_presence_is_field_gated_not_narrowed_by_an_exists_fn():
    """dry_level must appear wherever the sensor it replaced did.

    washer.py gates its combo dry_level on supportedDryLevel to tell a combo
    from a plain washer; every dryer has a dry level, so the same gate here
    would only ever suppress the entity -- including on a rep that is merely
    a stub at discovery, which entity._is_included deliberately admits so a
    sub-poll can populate it.

    A missing entity would be permanent and silent: __init__'s
    _drop_sensors_superseded_by_selects removes the old sensor row on every
    setup, unconditionally, so there is nothing left to fall back to.
    """
    desc = next(e for e in dryer.DRYER_SETTINGS.entities if e.key == "dry_level")
    assert isinstance(desc, SelectDesc)
    assert desc.exists_fn is None
    assert desc.field == "x.com.samsung.da.dryLevel"


def test_job_beginning_status_reads_current_status():
    """The dump carries x.com.samsung.da.currentStatus (not the old
    jobBeginingStatus field the dryer descriptor used to read), so the sensor
    is populated instead of blank."""
    assert _state()["job_beginning_status"] == "None"  # the dump's currentStatus value


def test_power_watts_gated_for_dead_sentinel():
    """instantaneousPower is the dead '-500' sentinel on this dryer, so the
    misleading 0 W power sensor is suppressed; cumulative energy still shows."""
    state = _state()
    assert "power_watts" not in state
    assert "energy_kwh" in state


def test_course_bound_to_shared_course_vs_0():
    """Dryer course uses the shared /course/vs/0 cycle select, with the
    translation key built from the device's own course table (see
    laundry.cycle_select) -- confirmed dryers report Table_03, matching
    the shipped dryer_cycle_table_03 translations, consistent with
    washer/dishwasher."""
    assert dryer.DRYER_COURSE.href == "/course/vs/0"
    desc = next(
        e for e in dryer.DRYER_COURSE.entities if e.key == "cycle" and isinstance(e, SelectDesc)
    )
    assert callable(desc.translation_key)
    table_03 = {"/st/dryercourse/vs/0": {"x.com.samsung.da.st.courseTable": "Table_03"}}
    assert desc.translation_key(table_03) == "dryer_cycle_table_03"
    assert desc.translation_key({}) == "cycle"
    live = {"/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": "EditCourseList_1620"}}
    assert desc.options(live) == ["16", "20"]
    rep = {"x.com.samsung.da.options": ["Course_16", "GMT_02"]}
    assert desc.rep_fn is not None
    assert desc.rep_fn(rep) == "16"


def test_reported_table_00_course_codes_are_translated():
    """The DVE45R6300W/A3 reporter confirmed these codes by selecting each
    cycle and reading back the raw course code (issue #357). A DV6800N --
    same DA_WM_A51_20_COMMON board, also Table_00 -- later confirmed 14 more
    (issue #394): a different subset of the same table, not a conflicting
    code family (its one code in common with #357, 'a5', means Bedding on
    both), so both sets share the one dryer_cycle_table_00 catalog entry."""
    from custom_components.localthings.catalog import translated_states

    desc = next(
        e for e in dryer.DRYER_COURSE.entities if e.key == "cycle" and isinstance(e, SelectDesc)
    )
    table_00 = {"/st/dryercourse/vs/0": {"x.com.samsung.da.st.courseTable": "Table_00"}}
    assert desc.translation_key(table_00) == "dryer_cycle_table_00"
    confirmed = {
        "01",
        "9c",
        "a5",
        "9e",
        "9b",
        "27",
        "a0",
        "a4",
        "a6",
        "a3",
        "a2",  # issue #357
        "9a",
        "ca",
        "db",
        "99",
        "93",
        "b5",
        "d7",
        "96",
        "97",
        "7f",
        "98",
        "eb",
        "b6",  # issue #394
    }
    assert confirmed <= translated_states("select", "dryer_cycle_table_00")


def test_st_dryercourse_is_ignored():
    """/st/dryercourse/vs/0 re-encodes the course exposed via /course/vs/0 and
    is globally ignored -- the mirror of /st/washercourse/vs/0."""
    ignored_hrefs = {c.href for c in ignored.IGNORED}
    assert "/st/dryercourse/vs/0" in ignored_hrefs
    assert "/st/washercourse/vs/0" in ignored_hrefs


def _dv6800n():
    resources = _load_device("dryer_dv6800n")
    reg = resolve(resources, device_types=("oic.wk.d", "oic.d.dryer"))
    return reg, resources


def test_dv6800n_no_unbound_hrefs():
    """Every resource in the issue #394 dump binds or is ignored."""
    reg, resources = _dv6800n()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_dv6800n_course_codes_read_from_dump():
    """A DV6800N (DA_WM_A51_20_COMMON, issue #394) reports 'Table_00' same
    as #357's DVE45R6300W/A3, so it resolves to the same catalog entry --
    its /course/vs/0 supportedOptions just advertises a different subset of
    the same table (see test_reported_table_00_course_codes_are_translated)."""
    _, resources = _dv6800n()
    desc = next(
        e for e in dryer.DRYER_COURSE.entities if e.key == "cycle" and isinstance(e, SelectDesc)
    )
    assert desc.translation_key(resources) == "dryer_cycle_table_00"
    confirmed = ["9A", "CA", "DB", "99", "93", "B5", "D7", "A5", "96", "97", "7F", "98", "EB", "B6"]
    assert desc.options(resources) == confirmed


def _settings_desc(key):
    return next(e for e in dryer.DRYER_SETTINGS.entities if e.key == key)


def _on_course(resources, code):
    """The same dump with a different course selected."""
    rep = dict(resources["/course/vs/0"])
    rep["x.com.samsung.da.options"] = [
        f"Course_{code}" if o.startswith("Course_") else o for o in rep["x.com.samsung.da.options"]
    ]
    return {**resources, "/course/vs/0": rep}


def test_dv6800n_dry_time_and_dry_level_narrow_to_complementary_courses():
    """The two dials are mutually exclusive per course on this board (see
    test_dv6800n_dry_policy_agrees_with_the_other_board_family), so narrowing
    both collapses whichever one the selected course does not use down to its
    live value -- rather than offering a dial the appliance will ignore.

    Course 9A (Cotton) is a dry-level course and 7F (Time Dry) a timed one.
    The live dryTime is 00:00:00 throughout this dump, which is why it
    survives on 7F despite sitting outside that course's mask: the union
    that keeps a live value addressable is what stops the entity reading as
    unknown mid-course-change.
    """
    _, resources = _dv6800n()
    level, timed = _settings_desc("dry_level"), _settings_desc("dry_time")

    cotton = _on_course(resources, "9A")
    assert level.options(cotton) == ["1", "2", "3"]
    assert timed.options(cotton) == ["00:00:00"]

    time_dry = _on_course(resources, "7F")
    assert level.options(time_dry) == ["2"]
    assert timed.options(time_dry) == [
        "00:00:00",
        "00:30:00",
        "01:00:00",
        "01:30:00",
        "02:00:00",
        "02:30:00",
    ]


def test_dry_time_keeps_its_full_list_where_the_board_carries_no_0xe_group():
    """Every board here but the DV6800N reports a supportedDryTime with no
    0xE group anywhere in supportedOptions. Absence there is not a refusal --
    course_option_mask returns None and the full list stands, so naming 0xE
    narrows nothing on a board that never spoke about it.

    All four are checked rather than one, because they are also the boards
    whose lists run past the eight entries a one-byte mask can address: if a
    later dump pairs a long list with a 0xE group, this is where it surfaces.
    """
    desc = _settings_desc("dry_time")
    for name in ("dryer", "dryer_dv80h", "dryer_tp1_21_drum_clean", "washer_dryer_onebody_awm"):
        resources = _load_device(name)
        supported = resources["/washer/vs/0"]["x.com.samsung.da.supportedDryTime"]
        assert len(supported) >= 11, name
        assert desc.options(resources) == supported, name
