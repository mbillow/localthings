"""Cloud "Download" programs on laundry devices (issue #342).

The store and blob parsing live in cloudcourse.py; how they reach the cycle
select lives in registry/capabilities/laundry.py. Both are covered here,
against the two real dumps in the corpus that carry these tokens:

  washer_ww5000c_cloud  the issue #342 reporter's WW5000C, sitting on
                        Download with a one-time Jeans override loaded over
                        a saved Sports default. Advertises nine programs.
  washer_wa55a7700av    a WA55A7700AV sitting on an ordinary local course,
                        with a saved program and the FFFF "none" sentinel in
                        the one-time slot. Advertises two programs.
"""

import pytest

from custom_components.localthings import cloudcourse
from custom_components.localthings.registry.capabilities import laundry, washer
from custom_components.localthings.registry.entities import SelectDesc
from tests.conftest import _load_device

# The reporter's nine captured programs, keyed by the slot byte the device
# itself advertises. Only used to drive the tests -- nothing in the shipped
# code carries a table of these (see cloudcourse.py's module docstring).
SPORTS = "0021550449284D134AA04C0035F004F005F0AC00"
JEANS = "001F6B0449284D134AA04C0035F004F005F0AC00"
TOWELS = "00040A0449404D134AB84C0035F004F005F0AC00"
# Issue #780 (WA54CG7150): its downloaded Eco Cold, a slot CloudExtraCourse_
# doesn't list.
WA54_ECO_COLD = "0013860449124D114A224C2037F0"


def _cycle_desc():
    return next(
        e for e in washer.WASHER_COURSE.entities if e.key == "cycle" and isinstance(e, SelectDesc)
    )


def _rep(options, cloud=None):
    rep = {"x.com.samsung.da.options": list(options)}
    if cloud is not None:
        rep[cloudcourse.FIELD] = cloud
    return rep


class TestBlobParsing:
    def test_slot_is_byte_two(self):
        """Confirmed against every program in both dumps: byte 2 of a blob
        is the slot id its own CloudExtraCourse_ token advertises."""
        assert cloudcourse.slot_of(SPORTS) == "55"
        assert cloudcourse.slot_of(JEANS) == "6B"
        assert cloudcourse.slot_of(TOWELS) == "0A"

    def test_ffff_prefix_is_not_a_program(self):
        """WA55A7700AV reports this while sitting on a local course -- it
        means 'no one-time override', not a program, and its byte 2 is not
        one of the slots that device advertises."""
        sentinel = "FFFF010049004D004A804C0037F0AC00"
        assert cloudcourse.is_loaded(sentinel) is False
        assert cloudcourse.slot_of(sentinel) is None

    @pytest.mark.parametrize(
        "bad",
        [None, "", "zz", "0021", "0021550449284D134AA04C0035F004F005F0AC0"],
    )
    def test_malformed_blobs_yield_no_slot(self, bad):
        assert cloudcourse.slot_of(bad) is None

    def test_advertised_slots_preserve_device_order(self):
        rep = _rep(["CloudExtraCourse_0A5C286B2D0C55301A"])
        assert cloudcourse.advertised_slots(rep) == [
            "0A",
            "5C",
            "28",
            "6B",
            "2D",
            "0C",
            "55",
            "30",
            "1A",
        ]

    def test_no_cloud_tokens_means_unsupported(self):
        assert cloudcourse.supports_cloud_courses(_rep(["Course_1C"]), ["1C"]) is False


class TestRealDumps:
    def test_reporter_dump_advertises_nine_and_learns_both_loaded_blobs(self):
        """One poll teaches two programs: the saved default and the loaded
        one-time override are different programs on this dump."""
        rep = _load_device("washer_ww5000c_cloud")["/course/vs/0"]
        assert len(cloudcourse.advertised_slots(rep)) == 9

        store = cloudcourse.CloudCourses()
        assert store.observe(rep) is True
        assert store.snapshot()["slots"]["55"]["blob"] == SPORTS
        assert store.snapshot()["slots"]["6B"]["blob"] == JEANS
        # Learned but unnamed -- nothing is offerable yet.
        assert store.named() == {}
        assert store.view() == {}

    def test_reporter_dump_proposes_its_download_course(self):
        """Only once a load has actually been watched. A single observation
        can't tell "just loaded" from "left over", so the dump alone proposes
        nothing -- see test_a_single_observation_proposes_nothing."""
        rep = _load_device("washer_ww5000c_cloud")["/course/vs/0"]
        store = cloudcourse.CloudCourses()
        store.observe(_rep(["CloudExtraCourse_556B", "Course_87", f"OneTimeCloudCourse_{SPORTS}"]))
        store.observe(rep)  # one-time token moves to Jeans while on Course_87
        assert store.download_candidates() == ["87"]
        # A candidate is never used until confirmed.
        assert store.snapshot()["download_course"] is None

    def test_a_single_observation_proposes_nothing(self):
        """The case a user hits after any restart: the appliance has cloud
        payloads from previous runs and is sitting on an ordinary course. If
        the board doesn't clear its one-time token, believing the first
        observation would propose that ordinary course as the Download one --
        and accepting the prefill would start a real wash cycle."""
        store = cloudcourse.CloudCourses()
        store.observe(_rep(["CloudExtraCourse_55", "Course_1B", f"OneTimeCloudCourse_{SPORTS}"]))
        assert store.download_candidates() == []
        # The payload is still learned -- that part is device fact.
        assert store.snapshot()["slots"]["55"]["blob"] == SPORTS

    def test_wa55_learns_its_saved_program_but_not_the_sentinel(self):
        rep = _load_device("washer_wa55a7700av")["/course/vs/0"]
        assert cloudcourse.advertised_slots(rep) == ["59", "58"]

        store = cloudcourse.CloudCourses()
        store.observe(rep)
        assert store.snapshot()["slots"]["59"]["blob"] == "001C590549164D114A224C2037F0AC22"
        assert "01" not in store.snapshot()["slots"]  # the FFFF sentinel's byte 2

    def test_wa55_proposes_no_download_course(self):
        """It is sitting on an ordinary local course with no override
        loaded, so there is nothing to infer -- exactly the case where
        guessing a code would start the wrong cycle."""
        rep = _load_device("washer_wa55a7700av")["/course/vs/0"]
        store = cloudcourse.CloudCourses()
        store.observe(rep)
        assert store.download_candidates() == []

    def test_a_dishwasher_tags_its_own_courses_not_payload_slots(self):
        """DW5000C (issues #113/#123). Its CloudExtraCourse_ names four
        bytes, and all four are course codes in its *own* course list --
        8E/8D/8F/02, three of them already translated (Plastic, Pots and
        pans, Baby Care). There it marks which ordinary courses came from
        the cloud; they select with a plain Course_ write and need nothing
        from this module. It carries no payload token at all, consistent
        with that.

        So none of this feature applies to it, and offering its owner a
        naming flow for programs that already work would be nonsense. The
        washers are the other shape: zero overlap with their course lists,
        and a payload required to select one."""
        resources = _load_device("dishwasher_dw5000c_cloud")
        rep = resources["/course/vs/0"]
        courses = laundry.cycle_options(resources)

        assert cloudcourse.advertised_slots(rep) == ["8E", "8D", "8F", "02"]
        assert set(cloudcourse.advertised_slots(rep)) <= set(courses)
        assert cloudcourse.cloud_slots(rep, courses) == []
        assert cloudcourse.supports_cloud_courses(rep, courses) is False
        assert cloudcourse.undiscovered(rep, cloudcourse.CloudCourses().snapshot(), courses) == []

    def test_washer_slots_share_nothing_with_the_course_list(self):
        """The distinguishing property, on both washers -- which is what
        makes subtracting the course list a safe way to tell the two
        meanings of CloudExtraCourse_ apart."""
        for name in ("washer_ww5000c_cloud", "washer_wa55a7700av"):
            resources = _load_device(name)
            rep = resources["/course/vs/0"]
            courses = laundry.cycle_options(resources)
            assert set(cloudcourse.advertised_slots(rep)).isdisjoint(courses), name
            assert cloudcourse.supports_cloud_courses(rep, courses) is True, name

    def test_byte_three_is_not_part_of_a_programs_identity(self):
        """Two WW5000C units on different firmware (issue #342's _B06C and
        issues #259/#343's _B048) report the same program -- same id, same
        slot, same field values, same tail -- differing only at byte 3.

        This is the evidence against a shipped catalog of payloads: keyed on
        program id, one unit's capture would have carried the other unit's
        byte 3. Both are recorded and replayed per device instead, so
        whatever byte 3 tracks never has to be understood."""
        b06c = "00040A0449404D134AB84C0035F004F005F0AC00"
        b048 = "00040A0649404D134AB84C0035F004F005F0AC00"
        assert b06c != b048
        # Same program by every identifier the device gives us.
        assert cloudcourse.slot_of(b06c) == cloudcourse.slot_of(b048) == "0A"
        assert b06c[:6] == b048[:6]
        assert b06c[8:] == b048[8:]
        # Learned separately per device, and each replays what it saw.
        for blob in (b06c, b048):
            store = cloudcourse.CloudCourses()
            store.observe(_rep(["CloudExtraCourse_0A", f"CloudCourse_{blob}"]))
            assert store.snapshot()["slots"]["0A"]["blob"] == blob

    def test_a_sentinel_slot_byte_is_not_the_current_course(self):
        """The two sentinels in the corpus disagree about this -- WA55's byte
        2 happens to equal its selected course, the WW5000C _B048's does not
        (1B against Course_1C). Nothing keys off it: a sentinel is rejected
        on its FFFF prefix, and its byte 2 is not an advertised slot either
        way."""
        wa55 = "FFFF010049004D004A804C0037F0AC00"
        b048 = "FFFF1B0049004D004A804C0035F004F005F0AC00"
        assert cloudcourse.slot_of(wa55) is None
        assert cloudcourse.slot_of(b048) is None
        # Different board widths, same sentinel rule.
        assert len(wa55) != len(b048)

    def test_undiscovered_counts_against_what_the_device_advertises(self):
        rep = _load_device("washer_ww5000c_cloud")["/course/vs/0"]
        store = cloudcourse.CloudCourses()
        store.observe(rep)
        # Both learned slots are still unnamed, so all nine are outstanding.
        courses = laundry.cycle_options(_load_device("washer_ww5000c_cloud"))
        assert len(cloudcourse.undiscovered(rep, store.snapshot(), courses)) == 9
        store.set_name("55", "Sports")
        assert "55" not in cloudcourse.undiscovered(rep, store.snapshot(), courses)
        assert len(cloudcourse.undiscovered(rep, store.snapshot(), courses)) == 8


class TestStoreRules:
    def test_a_program_outside_the_advertised_list_is_learned(self):
        """Issue #780: a WA54CG7150 lists only 59/58 but downloads any of
        five programs, one at a time, and runs whichever one is selected.
        Its downloaded Eco Cold sits in slot 86."""
        store = cloudcourse.CloudCourses()
        assert store.observe(_rep(["CloudExtraCourse_5958", f"CloudCourse_{WA54_ECO_COLD}"]))
        assert store.snapshot()["slots"]["86"]["blob"] == WA54_ECO_COLD

    def test_known_slots_are_advertised_then_learned(self):
        rep = _rep(["CloudExtraCourse_5958", f"CloudCourse_{WA54_ECO_COLD}"])
        store = cloudcourse.CloudCourses()
        store.observe(rep)
        record = store.snapshot()
        assert cloudcourse.known_slots(rep, [], record) == ["59", "58", "86"]
        assert cloudcourse.undiscovered(rep, record, []) == ["59", "58", "86"]
        store.set_name("86", "Eco Cold")
        assert cloudcourse.undiscovered(rep, store.snapshot(), []) == ["59", "58"]

    def test_known_slots_need_an_advertised_list(self):
        """Without one the options menu is hidden, so nothing may be counted
        that only it could resolve."""
        rep = _rep([f"CloudCourse_{WA54_ECO_COLD}"])
        store = cloudcourse.CloudCourses()
        store.observe(rep)
        assert cloudcourse.known_slots(rep, [], store.snapshot()) == []
        assert cloudcourse.undiscovered(rep, store.snapshot(), []) == []

    def test_a_learned_slot_that_matches_a_course_code_still_counts(self):
        """The WA54CG7150's course list includes 86 too; slots and course
        codes are separate namespaces, and a payload proves the program."""
        rep = _rep(["CloudExtraCourse_5958", f"CloudCourse_{WA54_ECO_COLD}"])
        store = cloudcourse.CloudCourses()
        store.observe(rep)
        assert cloudcourse.known_slots(rep, ["01", "86"], store.snapshot()) == ["59", "58", "86"]

    def test_the_sentinel_is_still_not_learned_outside_the_list(self):
        store = cloudcourse.CloudCourses()
        store.observe(
            _rep(["CloudExtraCourse_5958", "OneTimeCloudCourse_FFFF010049004D004A804C0037F0"])
        )
        assert store.snapshot()["slots"] == {}

    def test_a_relearned_blob_replaces_the_old_payload(self):
        store = cloudcourse.CloudCourses()
        store.observe(_rep(["CloudExtraCourse_55", f"CloudCourse_{SPORTS}"]))
        rewritten = SPORTS.replace("F005F0AC00", "F005F0AC11")
        assert store.observe(_rep(["CloudExtraCourse_55", f"CloudCourse_{rewritten}"])) is True
        assert store.snapshot()["slots"]["55"]["blob"] == rewritten

    def test_observing_the_same_rep_twice_changes_nothing(self):
        store = cloudcourse.CloudCourses()
        rep = _rep(["CloudExtraCourse_55", f"CloudCourse_{SPORTS}"])
        assert store.observe(rep) is True
        assert store.observe(rep) is False

    def test_candidates_rank_by_program_loads_not_by_dwell_time(self):
        """A stale OneTimeCloudCourse_ token outlives the run it belonged to,
        so it is still reported through every poll the appliance spends on
        whatever ordinary course comes next. Counting polls would rank that
        course first and suggest it as the Download course -- and accepting
        the suggestion would start a real wash cycle. Only the poll where the
        payload actually changed is evidence."""
        store = cloudcourse.CloudCourses()
        loaded = _rep(["CloudExtraCourse_55", "Course_87", f"OneTimeCloudCourse_{SPORTS}"])
        stale = _rep(["CloudExtraCourse_55", "Course_1B", f"OneTimeCloudCourse_{SPORTS}"])
        store.observe(_rep(["CloudExtraCourse_55", "Course_87"]))  # baseline
        store.observe(loaded)
        for _ in range(200):  # ~100 minutes sitting on a cotton cycle
            store.observe(stale)
        assert store.download_candidates() == ["87"]

    def test_a_second_distinct_load_is_counted(self):
        store = cloudcourse.CloudCourses()
        store.observe(_rep(["CloudExtraCourse_556B", "Course_87"]))  # baseline
        store.observe(_rep(["CloudExtraCourse_556B", "Course_87", f"OneTimeCloudCourse_{SPORTS}"]))
        store.observe(_rep(["CloudExtraCourse_556B", "Course_87", f"OneTimeCloudCourse_{JEANS}"]))
        assert store.download_candidates() == ["87"]

    def test_view_is_empty_until_a_download_course_is_confirmed(self):
        """Both halves are required: without the course code there is no
        write to build, so nothing should reach the select."""
        store = cloudcourse.CloudCourses()
        store.observe(_rep(["CloudExtraCourse_55", f"CloudCourse_{SPORTS}"]))
        store.set_name("55", "Sports")
        assert store.view() == {}
        store.set_download_course("87")
        assert store.view()["programs"] == {"55": {"blob": SPORTS, "name": "Sports"}}

    def test_round_trips_through_the_entry(self):
        store = cloudcourse.CloudCourses()
        store.observe(_rep(["CloudExtraCourse_55", f"CloudCourse_{SPORTS}"]))
        store.set_name("55", "Sports")
        store.set_download_course("87")
        restored = cloudcourse.CloudCourses(store.snapshot())
        assert restored.snapshot() == store.snapshot()
        assert restored.view() == store.view()

    @pytest.mark.parametrize(
        "junk",
        [
            "not a dict",
            {"slots": "nope"},
            {"slots": {"55": {"blob": "garbage", "name": "Sports"}}},
            # blob's own byte 2 disagrees with the key it is filed under
            {"slots": {"99": {"blob": SPORTS, "name": "Sports"}}},
        ],
    )
    def test_a_hand_edited_entry_cannot_crash_setup(self, junk):
        assert cloudcourse.CloudCourses(junk).snapshot()["slots"] == {}


class TestCycleSelectIntegration:
    """The cloud programs ride in the ordinary cycle select -- on the
    appliance's dial, Download is one course among the rest."""

    def _cloud(self):
        return {
            "download_course": "87",
            "programs": {"55": {"blob": SPORTS, "name": "Sports"}},
        }

    def test_local_courses_are_unchanged_without_cloud_data(self):
        desc = _cycle_desc()
        live = {"/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": "EditCourseList_1C1D"}}
        assert desc.options(live) == ["1C", "1D"]

    def test_named_programs_join_the_option_list_after_local_courses(self):
        desc = _cycle_desc()
        live = {
            "/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": "EditCourseList_1C87"},
            "/course/vs/0": _rep(["Course_87"], self._cloud()),
        }
        assert desc.options(live) == ["1C", "87", "cloud:55"]

    def test_an_unnamed_program_is_never_offered(self):
        desc = _cycle_desc()
        live = {
            "/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": "EditCourseList_1C"},
            "/course/vs/0": _rep(
                ["Course_87"],
                {"download_course": "87", "programs": {}},
            ),
        }
        assert desc.options(live) == ["1C"]

    def test_label_is_the_users_own_name(self):
        desc = _cycle_desc()
        resources = {"/course/vs/0": _rep(["Course_87"], self._cloud())}
        assert desc.display_fn("cloud:55", resources) == "Sports"

    def test_unknown_cloud_value_gets_no_invented_label(self):
        desc = _cycle_desc()
        resources = {"/course/vs/0": _rep(["Course_87"], self._cloud())}
        assert desc.display_fn("cloud:99", resources) is None

    def test_state_reports_the_loaded_program_while_on_download(self):
        desc = _cycle_desc()
        rep = _rep(["Course_87", f"OneTimeCloudCourse_{SPORTS}"], self._cloud())
        assert desc.rep_fn(rep) == "cloud:55"

    def test_state_falls_back_to_the_saved_program(self):
        """No one-time override loaded: the appliance runs the saved default,
        which the reporter confirmed by leaving Download and returning."""
        desc = _cycle_desc()
        rep = _rep(
            ["Course_87", f"CloudCourse_{SPORTS}", "OneTimeCloudCourse_FFFF010049004D004A804C00"],
            self._cloud(),
        )
        assert desc.rep_fn(rep) == "cloud:55"

    def test_a_stale_program_token_is_not_reported_on_a_local_course(self):
        """Tokens are replaced by prefix and never evicted, so a one-time
        program outlives its run. Reporting 'Sports' while a cotton cycle
        runs would be a live lie about what the machine is doing."""
        desc = _cycle_desc()
        rep = _rep(["Course_1C", f"OneTimeCloudCourse_{SPORTS}"], self._cloud())
        assert desc.rep_fn(rep) == "1C"

    def test_selecting_a_program_switches_course_and_loads_it_in_one_write(self):
        """Confirmed on hardware: writing the program token alone while
        another course is selected is silently ignored."""
        desc = _cycle_desc()
        rep = _rep(["Course_1C"], self._cloud())
        path, body = desc.write_fn("cloud:55", rep)
        assert path == ["course", "vs", "0"]
        assert body == {"x.com.samsung.da.options": ["Course_87", f"OneTimeCloudCourse_{SPORTS}"]}

    def test_selecting_a_local_course_still_writes_one_token(self):
        desc = _cycle_desc()
        rep = _rep(["Course_87"], self._cloud())
        _, body = desc.write_fn("1C", rep)
        assert body == {"x.com.samsung.da.options": ["Course_1C"]}

    def test_no_write_without_a_confirmed_download_course(self):
        desc = _cycle_desc()
        rep = _rep(["Course_1C"], {"programs": {"55": {"blob": SPORTS, "name": "Sports"}}})
        assert desc.write_fn("cloud:55", rep) is None

    def test_no_write_for_an_unknown_program(self):
        desc = _cycle_desc()
        rep = _rep(["Course_1C"], self._cloud())
        assert desc.write_fn("cloud:99", rep) is None


class TestOptionsMergePreservesSiblingTokens:
    def test_two_token_write_merges_like_the_device_does(self):
        """The write carries only the changed tokens; merge_options_field is
        what keeps the optimistic cache entry complete during the settle
        window. Both tokens must land, and unrelated ones must survive."""
        from custom_components.localthings.registry.capabilities.common import merge_options_field

        cached = [
            "DeviceType_0167",
            "Course_1C",
            f"CloudCourse_{SPORTS}",
            "CloudExtraCourse_0A5C286B2D0C55301A",
            f"OneTimeCloudCourse_{TOWELS}",
            "GMT_04",
        ]
        merged = merge_options_field(cached, ["Course_87", f"OneTimeCloudCourse_{SPORTS}"])
        assert "Course_87" in merged
        assert f"OneTimeCloudCourse_{SPORTS}" in merged
        # The saved default and the device's own slot list are untouched.
        assert f"CloudCourse_{SPORTS}" in merged
        assert "CloudExtraCourse_0A5C286B2D0C55301A" in merged
        assert "DeviceType_0167" in merged

    def test_cloud_prefixes_do_not_poison_the_course_lookup(self):
        """'Course_' is a prefix of neither 'CloudCourse_' nor
        'OneTimeCloudCourse_' only because option_value anchors at position
        0 -- one character away from being wrong, so it is pinned."""
        options = [f"CloudCourse_{SPORTS}", f"OneTimeCloudCourse_{JEANS}", "Course_1C"]
        assert laundry.option_value(options, "Course") == "1C"
        assert cloudcourse.option_value(options, "Course") == "1C"

    def test_a_payload_appearing_while_watching_is_a_real_transition(self):
        """Absent-then-loaded is the user selecting a program, and must count
        -- only "never observed at all" suppresses a candidate."""
        store = cloudcourse.CloudCourses()
        store.observe(_rep(["CloudExtraCourse_55", "Course_87"]))
        store.observe(_rep(["CloudExtraCourse_55", "Course_87", f"OneTimeCloudCourse_{SPORTS}"]))
        assert store.download_candidates() == ["87"]

    def test_a_stale_payload_across_a_restart_proposes_nothing(self):
        """A restored store starts unobserved again on purpose: the first rep
        after a restart is indistinguishable from a stale one, whatever was
        persisted."""
        seeded = cloudcourse.CloudCourses()
        seeded.observe(_rep(["CloudExtraCourse_55", "Course_87", f"OneTimeCloudCourse_{SPORTS}"]))
        restored = cloudcourse.CloudCourses(seeded.snapshot())
        restored.observe(_rep(["CloudExtraCourse_55", "Course_1B", f"OneTimeCloudCourse_{SPORTS}"]))
        assert restored.download_candidates() == []
