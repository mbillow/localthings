"""Tests for the shared laundry capabilities (washer/dryer/dishwasher)."""

from typing import ClassVar

from custom_components.localthings.registry.capabilities import laundry
from custom_components.localthings.registry.entities import SelectDesc
from tests.conftest import FIXTURES, _load_device


class TestCourseHelpers:
    def test_parses_edit_course_list(self):
        raw = "EditCourseList_1C1D211B1E29243328262722202325322F2E30662D8F962B2A"
        assert laundry.parse_edit_course_list(raw) == [
            "1C",
            "1D",
            "21",
            "1B",
            "1E",
            "29",
            "24",
            "33",
            "28",
            "26",
            "27",
            "22",
            "20",
            "23",
            "25",
            "32",
            "2F",
            "2E",
            "30",
            "66",
            "2D",
            "8F",
            "96",
            "2B",
            "2A",
        ]

    def test_parse_edit_course_list_handles_missing_or_malformed(self):
        assert laundry.parse_edit_course_list(None) == []
        assert laundry.parse_edit_course_list("") == []
        assert laundry.parse_edit_course_list("no underscore") == []

    def test_cycle_options_reads_live_edit_course_list(self):
        """The device's own course list -- including a code ('65') never seen
        on the primary dump -- is used as-is; there is no hardcoded table."""
        resources = {
            "/wm/editcourse/vs/0": {
                "x.com.samsung.da.editCourseList": "EditCourseList_651C",
            },
        }
        assert laundry.cycle_options(resources) == ["65", "1C"]

    def test_cycle_options_empty_when_resource_absent_or_empty(self):
        assert laundry.cycle_options({}) == []
        assert laundry.cycle_options({"/wm/editcourse/vs/0": {}}) == []

    def test_decodes_reported_washer_course_table_including_personal_slots(self):
        """Real DA_WM_TP1_21_COMMON diagnostics, with every selectable
        standard and personal-course code preserved in the device's order.
        F1/F3 are part of the packed list; their labels come from the
        companion personal-course resource tested below.
        """
        resources = {
            "/wm/editcourse/vs/0": {
                "x.com.samsung.da.editCourseList": (
                    "EditCourseList_696F73757801719688706D6A76726C6E6B777479F1F3"
                ),
            },
        }

        assert laundry.cycle_options(resources) == [
            "69",
            "6F",
            "73",
            "75",
            "78",
            "01",
            "71",
            "96",
            "88",
            "70",
            "6D",
            "6A",
            "76",
            "72",
            "6C",
            "6E",
            "6B",
            "77",
            "74",
            "79",
            "F1",
            "F3",
        ]

    def test_option_value(self):
        opts = ["DeviceType_0167", "Course_1C", "GMT_04"]
        assert laundry.option_value(opts, "Course") == "1C"
        assert laundry.option_value(opts, "Missing") is None

    def test_decodes_device_provided_personal_course_names(self):
        """Real /wm/personalcourse/vs/0 entries from the reported washer.

        The first TLV field contains a byte-counted UTF-8 name. It is the only
        course-name metadata in the diagnostics and is safe to display without
        assigning an inferred meaning to any standard course code.
        """
        resources = {
            "/wm/personalcourse/vs/0": {
                "x.com.samsung.da.courses": [
                    "F1_0106EC868DEC98B7021EED8CACED8BB020EB93B1",
                    "F2_00",
                    "F3_0109EC9A94EAB8B0EBB3B40220ECA084EC9AA9",
                ],
            },
        }
        first_name = bytes.fromhex("EC868DEC98B7").decode("utf-8")
        second_name = bytes.fromhex("EC9A94EAB8B0EBB3B4").decode("utf-8")
        assert laundry.personal_course_labels(resources) == {
            "F1": first_name,
            "F3": second_name,
        }

    def test_personal_course_names_reject_malformed_payloads(self):
        resources = {
            "/wm/personalcourse/vs/0": {
                "x.com.samsung.da.courses": [
                    "F1_not-hex",
                    "F2_0106AA",
                    "F3_020141",
                    None,
                ],
            },
        }
        assert laundry.personal_course_labels(resources) == {}

    def test_washer_cycle_fallback_only_labels_personal_courses(self):
        """No invented English label for an unrecognized code (PR #251 review)."""
        resources = {
            "/wm/personalcourse/vs/0": {
                "x.com.samsung.da.courses": ["F1_0106EC868DEC98B7"],
            },
        }
        expected = bytes.fromhex("EC868DEC98B7").decode("utf-8")
        assert laundry.washer_cycle_fallback("F1", resources) == expected
        assert laundry.washer_cycle_fallback("69", resources) is None
        assert laundry.washer_cycle_fallback("6f", resources) is None
        assert laundry.washer_cycle_fallback("Normal", resources) is None


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
        "31C8410923FA67F1B847E923FA67F25843E933FA57F20857E943FA67F"
        "088000913FA67F7485209204A5208780009000A00006841E930FA30F"
        "7F841E920FA30F65841E943FA57F8F8102923FA57F96841E920FA37F"
        "34841E923FA67FA0811E933FA33F"
    )

    def test_derives_codes_when_edit_course_list_is_empty(self):
        resources = {
            "/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": ""},
            "/course/vs/0": {
                "x.com.samsung.da.options": ["Course_1C"],
                "x.com.samsung.da.supportedOptions": [self._REAL_SUPPORTED_OPTIONS],
            },
        }
        assert laundry.cycle_options(resources) == [
            "1C",
            "1B",
            "25",
            "20",
            "08",
            "74",
            "87",
            "06",
            "7F",
            "65",
            "8F",
            "96",
            "34",
            "A0",
        ]

    def test_edit_course_list_still_takes_priority(self):
        """A live editCourseList wins even with supportedOptions present --
        no reason to prefer a derived list over the authoritative one."""
        resources = {
            "/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": "EditCourseList_651C"},
            "/course/vs/0": {
                "x.com.samsung.da.options": ["Course_1C"],
                "x.com.samsung.da.supportedOptions": [self._REAL_SUPPORTED_OPTIONS],
            },
        }
        assert laundry.cycle_options(resources) == ["65", "1C"]

    def test_rejects_a_table_missing_the_current_course(self):
        """The device's own currently-selected course must be a member of
        its derived list -- a mismatch means the guess is wrong, not that
        the device selected something outside its own supported set."""
        resources = {
            "/course/vs/0": {
                "x.com.samsung.da.options": ["Course_FF"],
                "x.com.samsung.da.supportedOptions": [self._REAL_SUPPORTED_OPTIONS],
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
            "/course/vs/0": {
                "x.com.samsung.da.options": ["Course_AA"],
                "x.com.samsung.da.supportedOptions": ["0AABBCCDD"],
            },
        }
        assert laundry.cycle_options(resources) == ["AA", "BB", "CC", "DD"]

    def test_empty_without_supported_options_or_course_href(self):
        assert laundry.cycle_options({}) == []
        assert laundry.cycle_options({"/course/vs/0": {}}) == []

    def test_smallest_wins_even_when_a_larger_pass_is_not_a_multiple(self):
        """Real dishwasher dump (K=7, 10 courses): K=10, 14, and 35 also
        pass both checks here, and none of them are multiples of 7 --
        position 0 lands on the same real course code ('0e') regardless of
        K, which alone satisfies the current-course guard for several
        unrelated splits. Smallest-K-wins is a heuristic that matches every
        real dump checked so far, not a proven guarantee -- see
        _course_codes_from_supported_options's docstring."""
        resources = {
            "/course/vs/0": {
                "x.com.samsung.da.options": ["Course_0E"],
                "x.com.samsung.da.supportedOptions": [
                    "30E5434B102D102835034B002D002845034B002D002805034B000D000"
                    "865034B002D002075000B000D000905000B000D0008D5034B002D0028"
                    "E5034B000D0008F5034B000D000"
                ],
            },
        }
        assert laundry.cycle_options(resources) == [
            "0E",
            "83",
            "84",
            "80",
            "86",
            "07",
            "90",
            "8D",
            "8E",
            "8F",
        ]

    def test_decodes_reported_dishwasher_course_table_without_editcourse(self):
        """Real DA_DW_A51_20_COMMON diagnostics: the board has no
        /wm/editcourse/vs/0, so every selectable code must come from its
        packed supportedOptions table, including the newer 82/8A/A7/A8/8C
        family that previously appeared as raw UI text.
        """
        resources = {
            "/course/vs/0": {
                "x.com.samsung.da.options": ["Course_82"],
                "x.com.samsung.da.supportedOptions": [
                    "482E0026002F00270028AE0026002F0027002A7E0026002F00270028"
                    "0E0006000F0027000A8E0026002F002700288E0006000F00070008C"
                    "E0026002F00270028DE0026002F00270028EE0006000F00270028FE"
                    "0006002F0027002"
                ],
            },
        }

        assert laundry.cycle_options(resources) == [
            "82",
            "8A",
            "A7",
            "80",
            "A8",
            "88",
            "8C",
            "8D",
            "8E",
            "8F",
        ]


class TestCourseOptionGroups:
    """The payload behind each supportedOptions record: 2-byte groups of
    <kind nibble><default nibble> <mask>, the mask indexing that option's
    own supported<Option> list.

    Four kinds are named -- 0xD dry off a DV5000T's per-course panel
    report, 0x8/0x9/0xA off a WW6500's; both readings are checked against
    the corpus in TestCourseOptionGroupsAcrossCorpus. The evidence, and the
    WW6600R combo that carries its dry dial on 0xB instead, is written up
    in docs/investigations/course-option-groups.md.
    """

    # DA_WM_TP1_21_COMMON DV9400B, from dryer_tp1_21_drum_clean: K=3, so a
    # single dry group per record. 16 (Cotton) allows Damp/Less/Normal/More
    # by mask 0x1E and defaults to Normal; 23 (Quick Dry 35') allows none.
    _DRYER: ClassVar[dict] = {
        "/course/vs/0": {
            "x.com.samsung.da.options": ["Course_16"],
            "x.com.samsung.da.supportedOptions": ["116D31E29D31E23D00017D31E"],
        }
    }

    def test_decodes_the_selected_course_by_default(self):
        assert laundry.course_option_mask(self._DRYER, laundry.OPTION_KIND_DRY) == (3, [1, 2, 3, 4])

    def test_decodes_a_named_course(self):
        mask = laundry.course_option_mask(self._DRYER, laundry.OPTION_KIND_DRY, course="29")
        assert mask == (3, [1, 2, 3, 4])

    def test_a_course_allowing_nothing_reports_an_empty_list(self):
        """Distinct from None: the device does have an opinion here, and it
        is that this course takes no dry setting at all."""
        assert laundry.course_option_mask(self._DRYER, laundry.OPTION_KIND_DRY, course="23") == (
            0,
            [],
        )

    def test_none_when_the_course_is_not_in_the_table(self):
        assert laundry.course_option_mask(self._DRYER, laundry.OPTION_KIND_DRY, course="ZZ") is None

    def test_none_when_the_record_has_no_group_of_that_kind(self):
        assert laundry.course_option_mask(self._DRYER, 0x9) is None

    def test_none_without_supported_options(self):
        assert laundry.course_option_mask({}, laundry.OPTION_KIND_DRY) is None
        assert laundry.course_option_mask({"/course/vs/0": {}}, laundry.OPTION_KIND_DRY) is None

    def test_non_hex_payload_says_nothing_rather_than_raising(self):
        """The width guards alone can't tell a record table from arbitrary
        text, so a payload that divides evenly but isn't hex still reaches
        the group parse. It has to decline, not raise into whichever entity
        happened to ask."""
        resources = {
            "/course/vs/0": {
                "x.com.samsung.da.options": ["Course_16"],
                "x.com.samsung.da.supportedOptions": ["016GG000017GG0000"],
            }
        }
        assert laundry.course_option_mask(resources, laundry.OPTION_KIND_DRY) is None

    def test_default_index_is_into_the_list_not_the_allowed_set(self):
        """A dishwasher reports default 0 while allowing only index 1, so a
        caller cannot treat the default as a member of the allowed set."""
        resources = _load_device("dishwasher")
        found = laundry.course_option_mask(resources, laundry.OPTION_KIND_DRY, course="83")
        assert found == (0, [1])

    def test_edit_course_list_overrides_a_narrower_passing_split(self):
        """The editCourseList guard only earns its place where the smallest
        otherwise-valid width is the wrong one -- on every shipped dump the
        smallest already covers the list, so a corpus fixture cannot tell
        the guard from its absence.

        Built to be exactly that case: twelve bytes split six ways gives
        unique first bytes that include the selected course, so the old
        guards accept it, but two of the four codes the device lists as
        selectable are then nowhere to be found.
        """
        course_rep = {
            "x.com.samsung.da.options": ["Course_11"],
            "x.com.samsung.da.supportedOptions": ["1110022334400550066778800"],
        }
        assert list(laundry._course_records(course_rep)) == ["11", "22", "44", "55", "66", "88"]

        listed = {"11", "33", "55", "77"}
        records = laundry._course_records(course_rep, must_cover=listed)
        assert list(records) == ["11", "33", "55", "77"]
        assert len(next(iter(records.values()))) == 6  # three bytes, not two

    def test_groups_are_read_on_their_own_boundaries(self):
        """Groups are two bytes each after the course byte, so the scan has
        to step four hex digits at a time. A window landing half a group
        out would still parse -- and answer for a kind the record does not
        carry -- rather than failing, so nothing else in the corpus would
        notice the stride being wrong.

        Here every record is <course><A8 9D><31 E2>. Read correctly there
        is no rinse (0x9) group at all; read two digits out of step, the
        bytes '9D 31' look exactly like one.
        """
        course_rep = {
            "x.com.samsung.da.options": ["Course_01"],
            "x.com.samsung.da.supportedOptions": [
                "1" + "".join(f"{code:02X}A89D31E2" for code in range(1, 7))
            ],
        }
        resources = {"/course/vs/0": course_rep}
        assert laundry._course_records(course_rep)["01"] == "01A89D31E2"

        assert laundry.course_option_mask(resources, 0x9) is None
        # The real group, whose mask also reaches bit 7 -- the top of the
        # byte, unexercised anywhere in the corpus.
        assert laundry.course_option_mask(resources, 0xA) == (8, [0, 2, 3, 4, 7])

    def test_reads_past_a_group_of_another_kind(self):
        """A DVE50A8600 record is <course><kind-8 group><dry group>, so the
        dry mask is only reachable by scanning past a group of another kind.

        Driven off the real dump rather than a trimmed copy of it: the
        record width is recovered from how the whole table divides, so a
        two-record excerpt legitimately resolves to a different width than
        the nineteen-record original.
        """
        resources = _load_device("dryer_dve50a8600")
        levels = resources["/washer/vs/0"]["x.com.samsung.da.supportedDryLevel"]
        assert levels == ["None", "Damp", "Less", "Normal", "More", "Very"]

        default, allowed = laundry.course_option_mask(resources, laundry.OPTION_KIND_DRY)
        assert [levels[i] for i in allowed] == ["Damp", "Less", "Normal", "More", "Very"]
        assert levels[default] == "Normal"
        # The kind-8 group ahead of it decodes too, unnamed on purpose.
        assert laundry.course_option_mask(resources, 0x8) is not None


class TestCourseOptionGroupsAcrossCorpus:
    """Structure invariants over every shipped dump, so a future fixture
    that decodes differently fails here rather than silently mis-gating an
    entity -- and, alongside them, the individual dumps the kind naming
    rests on, which are what those invariants are checking is still true."""

    def _dumps(self):
        for path in sorted(FIXTURES.glob("*_device.json")):
            yield path.name, _load_device(path.name[: -len("_device.json")])

    def test_every_record_is_a_course_byte_plus_whole_groups(self):
        odd = []
        for name, resources in self._dumps():
            records = laundry._course_records(resources.get("/course/vs/0") or {})
            for code, record in records.items():
                # hex chars: 2 for the course byte, then 4 per group.
                if (len(record) - 2) % 4:
                    odd.append((name, code, record))
        assert odd == []

    def test_confirmed_ww6500_course_matches_its_panel(self):
        """A WW6500 owner read one course's three dials off the panel:
        Cold/20/30/40 with no "None" offered, every rinse count including
        none, and every spin including rinse-hold. Two of its fourteen
        courses carry exactly those sets (they differ only in defaults), so
        this pins the sets rather than the course code -- which is all the
        kind naming rests on.
        """
        resources = _load_device("washer_ww6500")
        washer = resources["/washer/vs/0"]
        lists = (
            (laundry.OPTION_KIND_WATER_TEMPERATURE, "supportedWaterTemperature"),
            (laundry.OPTION_KIND_RINSE, "supportedRinseCycles"),
            (laundry.OPTION_KIND_SPIN, "supportedSpinLevel"),
        )
        reported = (
            ["Cold", "20", "30", "40"],
            ["0", "1", "2", "3", "4", "5"],
            ["RinseHold", "NoSpin", "400", "800", "1200", "1400"],
        )
        matching = []
        for code in laundry.cycle_options(resources):
            decoded = []
            for kind, field in lists:
                found = laundry.course_option_mask(resources, kind, course=code)
                supported = washer[f"x.com.samsung.da.{field}"]
                decoded.append([supported[i] for i in found[1]] if found else None)
            if tuple(decoded) == reported:
                matching.append(code)
        assert matching == ["5C", "61"]

    def test_spin_cannot_be_rinse_on_a_list_length_argument(self):
        """The panel reading above cannot in fact tell rinse from spin: on
        both courses that match it the two masks are 0x3F alike, so swapping
        the constants leaves it passing. This is the independent structural
        half -- on the `washer` dump 0xA addresses index 6, which a
        six-entry supportedRinseCycles cannot hold, so 0xA is not rinse
        whatever an owner reports. 0x9 tops out at index 5 there, exactly
        what that list allows."""
        resources = _load_device("washer")
        washer = resources["/washer/vs/0"]
        assert len(washer["x.com.samsung.da.supportedRinseCycles"]) == 6
        top = {}
        for code in laundry._course_records(resources["/course/vs/0"]):
            for kind in (laundry.OPTION_KIND_RINSE, laundry.OPTION_KIND_SPIN):
                found = laundry.course_option_mask(resources, kind, course=code)
                if found and found[1]:
                    top[kind] = max(top.get(kind, 0), max(found[1]))
        assert top[laundry.OPTION_KIND_SPIN] == 6
        assert top[laundry.OPTION_KIND_RINSE] == 5

    def test_the_combo_carries_its_dry_dial_on_0xb_not_0xd(self):
        """Recorded so the next change does not re-derive it: the WW6600R
        combo is the only board here with a real multi-entry
        supportedDryLevel, and it has no 0xD group at all -- its dry dial is
        0xB, dry-only courses dropping "None" and defaulting to Cupboard.
        A gate keyed on 0xD alone would silently no-op on the one board
        family that already ships a writable dry_level select.

        0xB stays unnamed: washer_dryer_onebody_awm is also a combo and
        uses 0xD, so this is not a rule the corpus can state yet. See
        docs/investigations/course-option-groups.md.
        """
        resources = _load_device("washer_dryer_combo")
        levels = resources["/washer/vs/0"]["x.com.samsung.da.supportedDryLevel"]
        assert len(levels) == 8

        def decode(kind, course):
            found = laundry.course_option_mask(resources, kind, course=course)
            return None if found is None else (levels[found[0]], [levels[i] for i in found[1]])

        # Pinned, so "no course carries 0xD" cannot pass by decoding nothing.
        records = laundry._course_records(resources["/course/vs/0"])
        assert len(records) == 24
        assert all(decode(laundry.OPTION_KIND_DRY, code) is None for code in records)
        wash_and_dry = ["None", "Cupboard", "30", "60", "90"]
        assert decode(0xB, "1C") == ("None", wash_and_dry)
        assert decode(0xB, "36") == ("Cupboard", wash_and_dry[1:])  # dry-only
        assert decode(0xB, "24") == ("None", [])  # wash-only

    def test_dv6800n_dry_policy_agrees_with_the_other_board_family(self):
        """The DV6800N is the one dump carrying both a dry-level (0xD) and a
        dry-time (0xE) group, and its courses are labelled -- so it is where
        the decode can be sanity-checked against meaning rather than bytes.

        Its policy lands course-for-course on what a DV5000T owner reported
        from their panel, despite a different board family and a different
        course-code space (Table_00 here, Table_03 there): full range on
        cottons/mixed/synthetics, a single fixed level on wool and iron dry,
        a different single level on bedding/delicates, a timed dry instead
        of a level on the air and time courses, and neither on Quick Dry.
        Two unrelated code spaces would not agree like that on a mask read
        at the wrong offset.
        """
        resources = _load_device("dryer_dv6800n")
        levels = resources["/washer/vs/0"]["x.com.samsung.da.supportedDryLevel"]
        times = resources["/washer/vs/0"]["x.com.samsung.da.supportedDryTime"]

        policy = {}
        for code in laundry.cycle_options(resources):
            dry = laundry.course_option_mask(resources, laundry.OPTION_KIND_DRY, course=code)
            timed = laundry.course_option_mask(resources, 0xE, course=code)
            policy[code] = (
                [levels[i] for i in dry[1]] if dry else [],
                [times[i] for i in timed[1]] if timed else [],
            )

        # No course offers both -- the mutual exclusion the DV5000T owner
        # described, corroborated here on a board they have never seen.
        assert [c for c, (lv, tm) in policy.items() if lv and tm] == []

        assert policy["9A"][0] == ["1", "2", "3"]  # Cotton
        assert policy["B5"][0] == ["1"]  # Wool
        assert policy["93"][0] == ["1"]  # Iron Dry
        assert policy["A5"][0] == ["2"]  # Bedding
        assert policy["EB"][0] == ["2"]  # Delicates
        assert policy["98"] == ([], [])  # Quick Dry 35 -- neither
        assert policy["7F"][1] == ["00:30:00", "01:00:00", "01:30:00", "02:00:00", "02:30:00"]

    def test_live_values_sit_inside_their_courses_decoded_set(self):
        """The strongest cross-check available without hardware: whatever
        each device currently reports for temperature/rinse/spin/dry has to
        be something its selected course's mask actually permits. A mask
        decoded at the wrong offset would put a live value outside its own
        allowed set almost immediately.

        An empty mask is skipped rather than failed -- a cloud Download
        slot declares no ranges for any of its kinds while still holding
        live values, because the downloaded program supplies them.
        """
        pairs = (
            (laundry.OPTION_KIND_WATER_TEMPERATURE, "waterTemperature"),
            (laundry.OPTION_KIND_RINSE, "rinseCycles"),
            (laundry.OPTION_KIND_SPIN, "spinLevel"),
            (laundry.OPTION_KIND_DRY, "dryLevel"),
        )
        checked, outside, overflowing = 0, [], []
        for name, resources in self._dumps():
            washer = resources.get("/washer/vs/0") or {}
            for kind, field in pairs:
                supported = washer.get(f"x.com.samsung.da.supported{field[0].upper()}{field[1:]}")
                live = washer.get(f"x.com.samsung.da.{field}")
                found = laundry.course_option_mask(resources, kind)
                if not supported or live is None or not found or not found[1]:
                    continue
                checked += 1
                # A bit past the end of the list is the failure, not
                # something to filter away: dropping those would turn a
                # wide garbage mask into "everything allowed", which any
                # live value then trivially satisfies.
                if max(found[1]) >= len(supported):
                    overflowing.append((name, field, found[1], len(supported)))
                    continue
                allowed = [supported[i] for i in found[1]]
                if live not in allowed:
                    outside.append((name, field, live, allowed))
        assert overflowing == []
        assert outside == []
        assert checked >= 12, f"expected the corpus to exercise this, saw {checked}"

    def test_dry_masks_stay_inside_the_devices_own_supported_list(self):
        """The mask indexes supportedDryLevel, so a bit past its end would
        mean the record is being split at the wrong width."""
        out_of_range = []
        for name, resources in self._dumps():
            levels = (resources.get("/washer/vs/0") or {}).get("x.com.samsung.da.supportedDryLevel")
            if not levels:
                continue
            for code in laundry._course_records(resources.get("/course/vs/0") or {}):
                found = laundry.course_option_mask(resources, laundry.OPTION_KIND_DRY, course=code)
                if found and found[1] and max(found[1]) >= len(levels):
                    out_of_range.append((name, code, found, len(levels)))
        assert out_of_range == []


class TestCycleSelect:
    def test_builds_labelled_cycle_select(self):
        desc = laundry.cycle_select(translation_key="dryer_cycle", icon="mdi:tumble-dryer")
        assert desc.key == "cycle"
        assert desc.translation_key == "dryer_cycle"
        assert desc.icon == "mdi:tumble-dryer"
        # Behavior, not identity: the option list is the device's own live
        # course list (plus any named cloud programs -- see cycle_select).
        live = {"/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": "EditCourseList_161C"}}
        assert desc.options(live) == ["16", "1C"]

    def test_reads_raw_course_code_from_options(self):
        desc = laundry.cycle_select(translation_key="dryer_cycle", icon="x")
        rep = {"x.com.samsung.da.options": ["DeviceType_0167", "Course_16", "GMT_04"]}
        assert desc.rep_fn(rep) == "16"
        assert desc.rep_fn({"x.com.samsung.da.options": ["GMT_04"]}) is None

    def test_exists_only_when_edit_course_list_is_live(self):
        desc = laundry.cycle_select(translation_key="dryer_cycle", icon="x")
        assert desc.exists_fn({}, {}) is False
        assert desc.exists_fn({}, {"/wm/editcourse/vs/0": {}}) is False
        live = {"/wm/editcourse/vs/0": {"x.com.samsung.da.editCourseList": "EditCourseList_16"}}
        assert desc.exists_fn({}, live) is True

    def test_cycle_write_is_single_token(self):
        """Confirmed on real hardware (issue #54): the device merges a
        single-token options[] write by prefix itself, so the write only
        needs to carry the changed token, not the whole rewritten array."""
        desc = laundry.cycle_select(translation_key="dryer_cycle", icon="x")
        rep = {"x.com.samsung.da.options": ["DeviceType_0167", "Course_16", "GMT_04"]}
        path, body = desc.write_fn("1D", rep)
        assert path == ["course", "vs", "0"]
        assert body == {"x.com.samsung.da.options": ["Course_1D"]}

    def test_cycle_write_noop_without_options(self):
        desc = laundry.cycle_select(translation_key="dryer_cycle", icon="x")
        assert desc.write_fn("1D", {}) is None


class TestCycleSelectTableGating:
    """translation_key becomes a resolver, not a plain string, once
    table_href is given -- washer/dryer's real call sites (issue: course
    codes aren't guaranteed consistent across board generations sharing
    the same /course/vs/0 contract; FlexWash's older board reports a
    different course table than every device the shipped translations
    were confirmed against). The resolved key is built from a reported
    table id. Devices without one use the generic translated Cycle entity
    name while preserving raw option codes as a safe fallback."""

    def _desc(self):
        return laundry.cycle_select(
            translation_key="washer_cycle",
            icon="x",
            table_href="/st/washercourse/vs/0",
        )

    def test_static_string_when_no_table_href_given(self):
        """dishwasher's call site -- no equivalent table-id resource in any
        dump seen, no evidence of the same cross-board risk -- keeps the
        plain static key unconditionally."""
        desc = laundry.cycle_select(translation_key="dishwasher_cycle", icon="x")
        assert desc.translation_key == "dishwasher_cycle"

    def test_resolved_key_is_built_from_the_reported_table(self):
        desc = self._desc()
        resources = {"/st/washercourse/vs/0": {"x.com.samsung.da.st.courseTable": "Table_02"}}
        assert callable(desc.translation_key)
        assert desc.translation_key(resources) == "washer_cycle_table_02"

    def test_untranslated_table_uses_generic_cycle_key(self):
        """An unknown table does not claim another board's state labels."""
        desc = self._desc()
        resources = {"/st/washercourse/vs/0": {"x.com.samsung.da.st.courseTable": "Table_99"}}
        assert desc.translation_key(resources) == "cycle"

    def test_resolves_to_generic_cycle_when_table_id_is_unknown(self):
        """An absent table id still gets a translated generic entity name."""
        desc = self._desc()
        assert desc.translation_key({}) == "cycle"
        assert desc.translation_key({"/st/washercourse/vs/0": {}}) == "cycle"


class TestBuzzerSound:
    def test_href(self):
        assert laundry.BUZZER_SOUND.href == "/buzzersound/vs/0"

    def test_buzzer_sound_write(self):
        desc = next(
            e
            for e in laundry.BUZZER_SOUND.entities
            if e.key == "buzzer_sound" and isinstance(e, SelectDesc)
        )
        assert desc.options_field == "supportedBuzzerSound"
        assert desc.write_fn is not None
        result = desc.write_fn("On", {})
        assert result is not None
        path, body = result
        assert path == ["buzzersound", "vs", "0"]
        assert body == {"setBuzzerSound": "On"}

    def test_finish_sound_exists_only_when_supported(self):
        desc = next(e for e in laundry.BUZZER_SOUND.entities if e.key == "finish_sound")
        assert desc.exists_fn is not None
        assert desc.exists_fn({"setBuzzerSound": "On"}, {}) is False
        assert desc.exists_fn({"supportedFinishSound": ["FinishSound_1"]}, {}) is True


class TestJobBeginningStatus:
    def test_href_and_field(self):
        assert laundry.JOB_BEGINNING_STATUS.href == "/wm/jobbeginingstatus/vs/0"
        desc = laundry.JOB_BEGINNING_STATUS.entities[0]
        assert desc.field == "x.com.samsung.da.currentStatus"
        assert desc.entity_category == "diagnostic"
