"""Tests for samsung_appliance/registry/by_type."""

from typing import cast

import pytest

from custom_components.localthings.registry.by_type import (
    _REGISTRY_BY_KEY,
    DeviceRegistry,
)


class TestDeviceRegistries:
    """Tests for device registries themselves."""

    def test_every_key_maps_to_a_device_registry(self):
        for key, registry in _REGISTRY_BY_KEY.items():
            assert isinstance(registry, DeviceRegistry), key

    def test_no_registry_has_ambiguous_hrefs(self):
        """An href carrying more than one capability needs every one of them
        to declare a discriminator, or discovery would bind both."""
        for key, registry in _REGISTRY_BY_KEY.items():
            for href, caps in registry.capabilities.items():
                if len(caps) > 1:
                    for cap in caps:
                        assert cap.rt_filter is not None or cap.match_fn is not None, (
                            f"{key}: href {href!r} has multiple caps but {cap!r} "
                            f"lacks rt_filter and match_fn"
                        )


class TestWasherRegistry:
    def test_washer_registry_registered(self):
        from custom_components.localthings.registry.by_type import _REGISTRY_BY_KEY

        assert "washer" in _REGISTRY_BY_KEY
        assert _REGISTRY_BY_KEY["washer"].name == "washer"

    def test_washer_registry_has_no_dup_hrefs(self):
        from custom_components.localthings.registry.by_type import _REGISTRY_BY_KEY

        registry = _REGISTRY_BY_KEY["washer"]
        for href, caps in registry.capabilities.items():
            if len(caps) > 1:
                for cap in caps:
                    assert cap.rt_filter is not None or cap.match_fn is not None, (
                        f"href {href!r} has multiple caps but {cap!r} lacks rt_filter and match_fn"
                    )

    def test_washer_registry_covers_known_hrefs(self):
        from custom_components.localthings.registry.by_type import _REGISTRY_BY_KEY

        registry = _REGISTRY_BY_KEY["washer"]
        for href in (
            "/power/0",
            "/power/vs/0",
            "/kidslock/0",
            "/kidslock/vs/0",
            "/remotectrl/0",
            "/remotectrl/vs/0",
            "/alarms/vs/0",
            "/energy/consumption/vs/0",
            "/water/consumption/vs/0",
            "/operational/state/vs/0",
            "/washer/vs/0",
            "/course/vs/0",
            "/buzzersound/vs/0",
            "/wm/jobbeginingstatus/vs/0",
            "/diagnosis/vs/0",
            "/otninformation/vs/0",
        ):
            assert href in registry.capabilities, f"{href} missing from washer registry"


class TestBoardTokens:
    def test_splits_pipe_prefix_into_whole_tokens(self):
        from custom_components.localthings.registry.by_type import _board_tokens

        assert _board_tokens("ARTIK051_DONGLE_REF|00127641|000800200014", "|") == [
            "ARTIK051",
            "DONGLE",
            "REF",
        ]

    def test_ignores_everything_after_the_cut(self):
        from custom_components.localthings.registry.by_type import _board_tokens

        assert _board_tokens("TP2X_RAC_20K|abc|REF_should_not_appear", "|") == [
            "TP2X",
            "RAC",
            "20K",
        ]

    def test_both_delimiters_produce_the_same_tokens(self):
        """The whole point of tokenizing: Samsung spells one board family
        with either delimiter, and both must reduce to the same tokens."""
        from custom_components.localthings.registry.by_type import _board_tokens

        assert (
            _board_tokens("TP1X_DA-AC-RAC-01001_0000", "|")
            == _board_tokens("TP1X_DA_AC_RAC_01001_0000", "|")
            == ["TP1X", "DA", "AC", "RAC", "01001", "0000"]
        )

    def test_upper_cases_and_drops_empty_runs(self):
        from custom_components.localthings.registry.by_type import _board_tokens

        assert _board_tokens("a--b__c", "|") == ["A", "B", "C"]

    def test_empty_for_none_or_empty_input(self):
        from custom_components.localthings.registry.by_type import _board_tokens

        assert _board_tokens("", "|") == []
        # value is annotated `str`, but the implementation is defensive
        # against a caller passing a genuinely missing modelNum/description
        # (see the `value or ""` in _board_tokens) -- exercise that directly.
        assert _board_tokens(cast(str, None), "|") == []


class TestBoardTokenTable:
    def test_no_board_family_token_shadows_a_specific_type(self):
        """'DA-AC-' prefixes RAC/WAC/DHM/AIR alike -- a bare 'AC' entry would
        type the dehumidifier and the air purifier as air conditioners."""
        from custom_components.localthings.registry.by_type import _BOARD_TOKEN_TO_KEY

        for family_token in ("AC", "DA", "KS", "WM", "TP1X", "TP2X", "ARTIK051"):
            assert family_token not in _BOARD_TOKEN_TO_KEY

    def test_every_token_resolves_to_a_real_registry(self):
        from custom_components.localthings.registry.by_type import (
            _BOARD_TOKEN_TO_KEY,
            _CONSUMER_PREFIX_TO_KEY,
            _REGISTRY_BY_KEY,
        )

        for token, key in _BOARD_TOKEN_TO_KEY.items():
            assert key in _REGISTRY_BY_KEY, f"{token!r} -> unknown registry {key!r}"
        for prefix, key in _CONSUMER_PREFIX_TO_KEY.items():
            assert key in _REGISTRY_BY_KEY, f"{prefix!r} -> unknown registry {key!r}"

    def test_tokens_are_upper_case(self):
        """`_board_tokens` upper-cases before lookup, so a lower-case entry
        would be dead."""
        from custom_components.localthings.registry.by_type import _BOARD_TOKEN_TO_KEY

        for token in _BOARD_TOKEN_TO_KEY:
            assert token == token.upper()


class TestConsumerModelKey:
    def test_finds_key_in_last_segment(self):
        from custom_components.localthings.registry.by_type import _consumer_model_key

        assert _consumer_model_key("DA_WM_TP1_21_COMMON_WW5000C") == "washer"

    def test_finds_key_before_a_trailing_unrecognized_segment(self):
        """Issue #79: 'DVE50A8800_8600' pairs two model numbers -- the real
        consumer token is the second-to-last segment, not the last."""
        from custom_components.localthings.registry.by_type import _consumer_model_key

        assert (
            _consumer_model_key("DA_WM_TP1_21_COMMON_DVE50A8800_8600/DC92-02835A_0080") == "dryer"
        )

    def test_ignores_everything_after_first_slash(self):
        from custom_components.localthings.registry.by_type import _consumer_model_key

        assert _consumer_model_key("DA_WM_TP1_21_COMMON_WW5000C/DW9000_board") == "washer"

    def test_none_when_no_segment_matches(self):
        from custom_components.localthings.registry.by_type import _consumer_model_key

        assert _consumer_model_key("ARTIK051_DONGLE_REF") is None


class TestForDeviceByOicType:
    """Primary device-type detection from /oic/d's `rt`."""

    def test_every_oic_type_resolves_to_a_real_registry(self):
        from custom_components.localthings.registry.by_type import (
            _OIC_TYPE_TO_KEY,
            _REGISTRY_BY_KEY,
        )

        for oic_type, key in _OIC_TYPE_TO_KEY.items():
            assert key in _REGISTRY_BY_KEY, f"{oic_type!r} -> unknown registry {key!r}"

    @pytest.mark.parametrize(
        "oic_type, expected",
        [
            ("oic.d.airconditioner", "airconditioner"),
            ("oic.d.airpurifier", "air_purifier"),
            ("oic.d.dishwasher", "dishwasher"),
            ("oic.d.dryer", "dryer"),
            ("oic.d.microwave", "microwave"),
            ("oic.d.oven", "oven"),
            ("oic.d.refrigerator", "refrigerator"),
            ("oic.d.washer", "washer"),
            ("x.com.st.d.hood", "range_hood"),
            ("x.com.st.d.stickcleaner", "vacuum_station"),
            ("x.com.st.d.steamcloset", "air_dresser"),
            ("x.com.st.d.airqualitysensor", "air_monitor"),
        ],
    )
    def test_known_oic_types_resolve(self, oic_type, expected):
        from custom_components.localthings.registry.by_type import for_device_by_oic_type

        reg = for_device_by_oic_type((oic_type,))
        assert reg is not None
        assert reg.name == expected

    def test_generic_wk_d_type_alone_resolves_nothing(self):
        """The generic 'oic.wk.d' base type every OCF device carries
        alongside its concrete type isn't itself a device type."""
        from custom_components.localthings.registry.by_type import for_device_by_oic_type

        assert for_device_by_oic_type(("oic.wk.d",)) is None

    def test_unrecognized_type_returns_none(self):
        from custom_components.localthings.registry.by_type import for_device_by_oic_type

        assert for_device_by_oic_type(("oic.d.somethingnew",)) is None

    def test_robotcleaner_is_not_mapped_to_the_vacuum_station_registry(self):
        """'oic.d.robotcleaner' names an actual robot vacuum, a different
        product from the clean/auto-empty station vacuum_station covers (no
        vacuum-body capabilities at all) -- mapping it there would misroute
        a genuine robot-vacuum dump."""
        from custom_components.localthings.registry.by_type import for_device_by_oic_type

        assert for_device_by_oic_type(("oic.d.robotcleaner",)) is None

    def test_cooktop_is_not_mapped_to_either_cooktop_registry(self):
        """'oic.d.cooktop' cannot tell the two cooktop families apart.

        A TP1X_DA-KS-COOKTOP induction reports it, but `cooktop` is the
        unrelated NA9300K gas family (burner state in /mode/vs/0's options
        array, a different OCF surface -- see by_type/cooktop.py). Mapping the
        type to either key would misroute the other, and as the primary signal
        it would override a board token that had it right.
        """
        from custom_components.localthings.registry.by_type import for_device_by_oic_type

        assert for_device_by_oic_type(("oic.d.cooktop",)) is None

    def test_empty_returns_none(self):
        from custom_components.localthings.registry.by_type import for_device_by_oic_type

        assert for_device_by_oic_type(()) is None

    def test_finds_the_concrete_type_alongside_the_generic_one(self):
        """A real /oic/d `rt` carries both the generic base type and the
        concrete one, order unspecified -- either position must resolve."""
        from custom_components.localthings.registry.by_type import for_device_by_oic_type

        reg = for_device_by_oic_type(("oic.wk.d", "oic.d.washer"))
        assert reg is not None
        assert reg.name == "washer"


class TestForDeviceByModel:
    """Fallback device-type detection for hardware without oneUiVersion."""

    def test_washer_ww_prefix(self):
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "DA_WM_TP1_21_COMMON|20375141|20010002001811424AA30217008A0000",
            "DA_WM_TP1_21_COMMON_WW5000C/DC92-03495A_B048",
        )
        assert reg is not None
        assert reg.name == "washer"

    def test_washer_wd_prefix(self):
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "DA_WM_TP1_21_COMMON|20375141|20010002001811424AA30217008A0000",
            "DA_WM_TP1_21_COMMON_WD7000B/DC92-03724A_004D",
        )
        assert reg is not None
        assert reg.name == "washer"

    def test_dryer_not_misdetected_as_washer(self):
        """Dryer shares the DA_WM_ board prefix with washer -- must not
        be misrouted despite the shared prefix."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "DA_WM_TP2_20_COMMON_DV5000T",
            "DA_WM_TP2_20_COMMON_DV5000T",
        )
        assert reg is not None
        assert reg.name == "dryer"

    def test_dryer_dve50a8600_paired_model_numbers_in_description(self):
        """Issue #79: description pairs two model numbers
        ('..._DVE50A8800_8600/DC92-...'), so the 'DV' consumer token is one
        segment before the literal last segment ('8600', which has no
        recognizable prefix on its own). The old last-segment-only check
        fell through to 'unknown' here."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "DA_WM_TP1_21_COMMON|20286441|300000010015110002A3031700000000",
            "DA_WM_TP1_21_COMMON_DVE50A8800_8600/DC92-02835A_0080",
        )
        assert reg is not None
        assert reg.name == "dryer"

    def test_dishwasher_not_misdetected_as_washer(self):
        """Dishwasher's modelNum contains the substring 'WW' -- must not
        be misrouted by a naive substring match."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "ADW-WW-RTL-24-AILITE|90000541|400002010019130059C1000500E10000",
            "ADW-WW-RTL-24-AILITE_DW9000F/DD91-00002A_0002",
        )
        assert reg is not None
        assert reg.name == "dishwasher"

    def test_refrigerator_via_modelnum_ref_token(self):
        """Refrigerator's description has no consumer-model suffix; falls
        back to the '_REF_' token in modelNum."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "TP1X_REF_21K|00176141|00000850031813294103010041030000",
            "TP1X_REF_21K",
        )
        assert reg is not None
        assert reg.name == "refrigerator"

    def test_refrigerator_rl_series_via_ref_token(self):
        """Issue #7: RL38C6B0CWW/EG (a bottom-freezer RL-series fridge, not
        the RF9000-style french-door this module was originally verified
        against) reports description/modelNum 'TP1X_REF_21K' -- same
        internal platform code as any other TP1X-based fridge, so the
        existing '_REF_' fallback already resolves it correctly."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "TP1X_REF_21K|00156941|00050126001611304100000031010000",
            "TP1X_REF_21K",
        )
        assert reg is not None
        assert reg.name == "refrigerator"

    def test_refrigerator_dongle_ref_pipe_delimited_modelnum(self):
        """Issues #77/#83: the ARTIK051_DONGLE_REF family's modelNum is
        '<board>_DONGLE_REF|<rest>' -- REF is the last underscore segment
        before the pipe, with no trailing underscore, so the plain '_REF_'
        substring check used to miss it entirely and the device fell back
        to 'unknown' with only common capabilities."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "ARTIK051_DONGLE_REF|00127641|00080020001430300100000000000000",
            "ARTIK_REF_17K",
        )
        assert reg is not None
        assert reg.name == "refrigerator"

    def test_airconditioner_via_prac_token(self):
        """Issue #17: a room AC (ARTIK051_PRAC_20K) reports no oneUiVersion and
        an unrecognized consumer token ('20K'); it falls back to the '_PRAC_'
        (Package Room Air Conditioner) token in modelNum."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "ARTIK051_PRAC_20K|10217841|60010532001411004200003000000000",
            "ARTIK051_PRAC_20K",
        )
        assert reg is not None
        assert reg.name == "airconditioner"

    def test_airconditioner_via_cac_token(self):
        """Issue #191: a cassette AC (TP1X_DA-AC-CAC-01001_0000) regressed to
        'unknown'/common-caps in 0.16.0 when oneUiVersion detection was
        dropped -- 'CAC' had never been added to the modelNum board-token
        table, only reachable before via oneUiVersion's '7.0 Air conditioner'."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "TP1X_DA-AC-CAC-01001_0000|10255541|60030748171811DF42005F2A00F2ED00",
            "TP1X_DA-AC-CAC-01001_0000",
        )
        assert reg is not None
        assert reg.name == "airconditioner"

    def test_dehumidifier_via_dhm_token(self):
        """Issue #88: a dehumidifier (AY18CG7500GED) shares the DA_AC_ board
        family with the room-AC models but reports no oneUiVersion and
        carries the '_DHM_' (DeHuMidifier) token instead of
        '_RAC_'/'_PRAC_'/'_WAC_'; falls back to the '_DHM_' token in
        modelNum."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "TP1X_DA_AC_DHM_01001_0000|10253841|77000000001700000A00000000000000",
            "TP1X_DA_AC_DHM_01001_0000",
        )
        assert reg is not None
        assert reg.name == "dehumidifier"

    def test_ehs_via_ehs_token(self):
        """A Samsung EHS air-to-water heat pump (TP1X_DA_AC_EHS_01001_0000)
        shares the DA_AC_ board family with the room-AC models but reports
        no oneUiVersion and carries the '_EHS_' (Eco Heating System) token
        instead of '_RAC_'/'_PRAC_'/'_WAC_'/'_DHM_'; falls back to the
        '_EHS_' token in modelNum."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "TP1X_DA_AC_EHS_01001_0000|10250141|60070105001711034A00010000002000",
            "TP1X_DA_AC_EHS_01001_0000",
        )
        assert reg is not None
        assert reg.name == "ehs"

    def test_water_purifier_via_waterpurifier_token(self):
        """Issue #90: a water purifier (TP2X_WATERPURIFIER_20K) reports no
        oneUiVersion and no consumer-prefix match; falls back to the
        'WATERPURIFIER' token shared by modelNum and description."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "TP2X_WATERPURIFIER_20K|00132341|900000000215130001060F0000020000",
            "TP2X_WATERPURIFIER_20K",
        )
        assert reg is not None
        assert reg.name == "water_purifier"

    def test_water_purifier_wins_over_ref_when_both_tokens_present(self):
        """Issue #196: an AILITE water purifier (RWP70F15ANW) spells its
        modelNum '...-REF-WATERPURIFIER-...', which would otherwise match the
        bare 'REF' board token (refrigerator) before ever reaching
        'WATERPURIFIER' -- misrouting it to the refrigerator registry, whose
        resource surface shares almost nothing with a water purifier."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "AILITE_DA-REF-WATERPURIFIER-01011|70674641|900100000219130081088700001E0000",
            "AILITE_WATERPURIFIER_25K",
        )
        assert reg is not None
        assert reg.name == "water_purifier"

    def test_bare_ref_token_still_resolves_refrigerator(self):
        """Regression guard for the #196 carve-out above: a genuine
        refrigerator modelNum with no 'WATERPURIFIER' token must still
        resolve to 'refrigerator'."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "TP1X_REF_21K|00175941|00050126001811344100000020090000",
            "TP1X_REF_21K",
        )
        assert reg is not None
        assert reg.name == "refrigerator"

    def test_airconditioner_via_wac_token(self):
        """Issue #87: a Bespoke Window AC (AW06C7155EWAZ) reports no
        oneUiVersion and a modelNum carrying the '_WAC_' (Window Air
        Conditioner) token instead of '_RAC_'/'_PRAC_'; falls back to the
        '_WAC_' token in modelNum."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "TP1X_DA_AC_WAC_01001_0000|40460041|50030018001611020A00000000000000",
            "AW06C7155EWAZ",
        )
        assert reg is not None
        assert reg.name == "airconditioner"

    def test_wac_token_not_shadowed_by_wa_washer_prefix(self):
        """Regression: adding the 'WA' consumer-model prefix (issue #106)
        must not hijack devices whose description literally equals their
        modelNum (e.g. the Window AC family, issue #87) and so contains a
        'WAC' segment -- 'WAC'[:2] == 'WA' would otherwise match the washer
        prefix before the more specific '_WAC_' modelNum check ever runs."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "TP1X_DA_AC_WAC_01001_0000|40460041|50030018001611020A00000000000000",
            "TP1X_DA_AC_WAC_01001_0000",
        )
        assert reg is not None
        assert reg.name == "airconditioner"

    def test_airconditioner_via_ara_ww_token(self):
        """Issues #115/#116/#117/#120: ARA-WW-TP1-22-COMMON wall-mount RACs
        (AR10/13/18BYEAAWKNME) report no oneUiVersion and no
        '_RAC_'/'-RAC-'/'_PRAC_' token at all -- falls back to the
        'ARA-WW-' token in modelNum, reusing the same airconditioner
        registry as every other TP1X-class room AC rather than a new
        device type."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "ARA-WW-TP1-22-COMMON|10229641|6001051A001511014600083200800000",
            "ARA-WW-TP1-22-COMMON",
        )
        assert reg is not None
        assert reg.name == "airconditioner"

    def test_microwave_via_microwave_token(self):
        """Issue #121/#66: a combi microwave (MW7300B-/EU1) reports no
        oneUiVersion and an unrecognized consumer token; falls back to the
        '-MICROWAVE-' token in modelNum onto its own microwave registry
        (initially folded into 'oven' for issue #121, split out into a
        distinct device type per user feedback)."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "TP1X_DA-KS-MICROWAVE-01041|40475341|50040100021811000A00000000000000",
            "MW7300B-/EU1",
        )
        assert reg is not None
        assert reg.name == "microwave"

    def test_vacuum_station_via_vskr_token(self):
        """Issue #131: a stick-vacuum clean/auto-empty station
        (A-VSKR-TP1-22-VS9500AL) reports no oneUiVersion and no
        washer/dryer/dishwasher consumer prefix; falls back to the
        '-VSKR-' token in modelNum onto a new vacuum_station registry (its
        dump has no vacuum-body state at all, only station-specific
        dustbag/dustbin/UV-sanitize resources -- see
        capabilities/vacuum_station.py's module docstring)."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "A-VSKR-TP1-22-VS9500AL|50023541|80030100001611000800000000000000",
            "A-VSKR-TP1-22-VS9500AL",
        )
        assert reg is not None
        assert reg.name == "vacuum_station"

    def test_air_monitor_via_asm_token(self):
        """Issue #210: a standalone air-quality sensor puck
        (ASM-KR-TP1-22-ACMB1M) reports no oneUiVersion and no
        washer/dryer/dishwasher consumer prefix; falls back to the 'ASM'
        token in modelNum onto the air_monitor registry."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "ASM-KR-TP1-22-ACMB1M|10243041|75000000001611C40800020000080000",
            "ASM-KR-TP1-22-ACMB1M",
        )
        assert reg is not None
        assert reg.name == "air_monitor"

    def test_cooktop_via_legacy_model_description(self):
        """Older cooktops identify themselves as ARTIK051_GLOBAL_COOKTOP."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "ARTIK051_GB_CT_001|40424141|50000204001211000200000000000000",
            "ARTIK051_GLOBAL_COOKTOP",
        )
        assert reg is not None
        assert reg.name == "gas_cooktop"

    def test_range_hood_via_ahd_model(self):
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "AHD-WW-TP1-22-COMMON|20136141|7800006B001713C44D00090001030000",
            "AHD-WW-TP1-22-COMMON",
        )
        assert reg is not None
        assert reg.name == "range_hood"

    def test_washer_wf_prefix(self):
        """US front-load washers use the WF consumer-model prefix."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "DA_WM_TP1_21_COMMON|20313741|20010001001611244AA3021700000000",
            "DA_WM_TP1_21_COMMON_WF8900B/DC92-03129A_A0AE",
        )
        assert reg is not None
        assert reg.name == "washer"

    def test_washer_wv_prefix(self):
        """FlexWash twin washers use the WV consumer-model prefix -- issue #19."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "DA_WM_A51_20_COMMON|20198042|20020001001111400203000000000000",
            "DA_WM_A51_20_COMMON_WV9600M/DC92-01980B_0014",
        )
        assert reg is not None
        assert reg.name == "washer"

    def test_washer_wa_prefix(self):
        """Top-load washers (e.g. WA8000T) use the WA consumer-model prefix
        -- issue #106."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "DA_WM_TP2_20_COMMON|20233741|200000010014110002A3020200000000",
            "DA_WM_TP2_20_COMMON_WA8000T/DC92-02810A_0002",
        )
        assert reg is not None
        assert reg.name == "washer"

    def test_oven_via_oven_token(self):
        """Issue #55: a wall oven (NV7000BS/ET5) reports no oneUiVersion and
        an unrecognized consumer token ('NV'); it falls back to the '-OVEN-'
        token in modelNum, mirroring the '-RANGE-' fallback for issue #44."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "TP1X_DA-KS-OVEN-0107X|40460041|50030018001611020A00000000000000",
            "NV7000BS/ET5",
        )
        assert reg is not None
        assert reg.name == "oven"

    def test_induction_cooktop_via_hyphenated_cooktop_token(self):
        """Issue #86: a standalone induction cooktop (NV8500T-/KO4) reports
        no oneUiVersion and an unrecognized consumer token ('NV'); it falls
        back to the hyphenated '-COOKTOP-' token in modelNum -- distinct
        from the underscore-delimited '_COOKTOP'/'_GB_CT_' check, which is
        the unrelated older NA9300K gas-cooktop family."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(
            "TP1X_DA-KS-COOKTOP-01011|40459741|50000203001711000A00000000000000",
            "NV8500T-/KO4",
        )
        assert reg is not None
        assert reg.name == "induction_cooktop"

    def test_hyphenated_cooktop_token_not_confused_with_range(self):
        """'-COOKTOP-' and '-RANGE-' must route to distinct registries even
        though both are TP1X_DA-KS- board-family siblings."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model("TP1X_DA-KS-COOKTOP-01011", "NV8500T-/KO4")
        assert reg is not None
        assert reg.name != "range"

    def test_unknown_model_returns_none(self):
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model("SOME-UNKNOWN-BOARD", "SOME-UNKNOWN-BOARD")
        assert reg is None

    def test_empty_inputs_return_none(self):
        from custom_components.localthings.registry.by_type import for_device_by_model

        assert for_device_by_model("", "") is None

    @pytest.mark.parametrize(
        "model_num",
        [
            "TP1X_DA-AC-RAC-01001_0000",  # hyphenated (issue #91)
            "TP1X_DA_AC_RAC_01001_0000",  # underscored
            "TP2X_RAC_20K",  # bare, no board-family prefix (issue #37)
            "TP2X-RAC-20K",
        ],
    )
    def test_delimiter_spelling_does_not_change_the_answer(self, model_num):
        """One board family, four spellings, one table entry."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model(model_num, "")
        assert reg is not None
        assert reg.name == "airconditioner"

    def test_model_num_wins_when_the_two_fields_disagree(self):
        """The legacy gas cooktop is the one known device whose fields
        conflict: modelNum says CT (gas), description says COOKTOP (which
        otherwise means induction). The board is right, so modelNum is
        consulted first."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model("ARTIK051_GB_CT_001", "ARTIK051_GLOBAL_COOKTOP")
        assert reg is not None
        assert reg.name == "gas_cooktop"

    def test_board_token_in_description_used_when_model_num_has_none(self):
        """Some units report a placeholder modelNum and carry the board token
        only in `description`."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model("TEST-MODEL", "TP1X_REF_21K")
        assert reg is not None
        assert reg.name == "refrigerator"

    def test_board_token_beats_consumer_prefix(self):
        """'WAC' (window AC, issue #87) starts with 'WA' (top-load washer,
        issue #106). The board table runs first, so the AC wins."""
        from custom_components.localthings.registry.by_type import for_device_by_model

        reg = for_device_by_model("TP1X_DA_AC_WAC_01001_0000", "TP1X_DA_AC_WAC_01001_0000")
        assert reg is not None
        assert reg.name == "airconditioner"


class TestBoardTokenAmbiguity:
    """`_board_family_key` returns the first matching token, which is only
    safe while no real model string contains two tokens naming different
    device types. Guard that against every dump we have."""

    # Issue #196: AILITE water-purifier boards spell their modelNum
    # '...-REF-WATERPURIFIER-...' -- 'REF' names the shared cooling-subsystem
    # board, not a refrigerator. `_board_family_key` carries an explicit
    # carve-out resolving this one pair to 'water_purifier'; this is the one
    # documented exception to the "no two board tokens ever co-occur"
    # invariant the rest of this test enforces.
    _ALLOWED_CONFLICTS = frozenset({frozenset({"refrigerator", "water_purifier"})})

    def test_no_fixture_model_string_yields_two_conflicting_keys(self, all_device_fixtures):
        from custom_components.localthings.registry.by_type import (
            _BOARD_TOKEN_TO_KEY,
            _board_tokens,
        )

        for name, resources in all_device_fixtures.items():
            info = resources.get("/information/vs/0", {})
            for field, cut in (
                (info.get("x.com.samsung.da.modelNum", ""), "|"),
                (info.get("x.com.samsung.da.description", ""), "/"),
            ):
                keys = {
                    _BOARD_TOKEN_TO_KEY[t]
                    for t in _board_tokens(field, cut)
                    if t in _BOARD_TOKEN_TO_KEY
                }
                assert len(keys) <= 1 or frozenset(keys) in self._ALLOWED_CONFLICTS, (
                    f"{name}: {field!r} matches conflicting board tokens {keys}"
                )


class TestOneUiVersionIsNotConsulted:
    """oneUiVersion used to be the first detection stage. It named the type
    directly ('7.0 Dishwasher'), but only a minority of hardware reports it,
    every device that does is already typed by its modelNum board token, and
    no device-support issue was ever fixed by adding a mapping for it. It is
    still reported in diagnostics as a firmware-generation marker."""

    def test_resolve_ignores_a_recognizable_one_ui_version(self):
        from custom_components.localthings.registry.by_type import resolve

        resources = {
            "/otninformation/vs/0": {"swVersionInfo": {"oneUiVersion": "7.0 Dishwasher"}},
            "/information/vs/0": {
                "x.com.samsung.da.modelNum": "SOME-UNKNOWN-BOARD",
                "x.com.samsung.da.description": "SOME-UNKNOWN-BOARD",
            },
        }
        assert resolve(resources) is None

    def test_resolve_types_every_one_ui_reporting_fixture_without_it(self, all_device_fixtures):
        """The claim above, checked: for every dump that reports a
        oneUiVersion, the model strings alone reach a registry."""
        from custom_components.localthings.registry.by_type import resolve

        seen = 0
        for name, resources in all_device_fixtures.items():
            one_ui = (
                resources.get("/otninformation/vs/0", {})
                .get("swVersionInfo", {})
                .get("oneUiVersion", "")
            )
            if not one_ui:
                continue
            seen += 1
            assert resolve(resources) is not None, (
                f"{name} reports oneUiVersion {one_ui!r} and nothing else types it"
            )
        assert seen, "no fixture reports oneUiVersion -- has the corpus changed?"


class TestResolve:
    def test_oic_type_wins_over_model_strings(self):
        """The device naming its own type via /oic/d beats board-token
        parsing -- an unrecognizable modelNum with a known oic.d type must
        still resolve, and a *conflicting* modelNum must lose to it."""
        from custom_components.localthings.registry.by_type import resolve

        resources = {
            "/information/vs/0": {
                "x.com.samsung.da.modelNum": "SOME-UNKNOWN-BOARD",
                "x.com.samsung.da.description": "SOME-UNKNOWN-BOARD",
            },
        }
        reg = resolve(resources, device_types=("oic.d.washer",))
        assert reg is not None
        assert reg.name == "washer"

        conflicting = resolve(
            resources={
                "/information/vs/0": {
                    "x.com.samsung.da.modelNum": "TP1X_REF_21K",
                    "x.com.samsung.da.description": "TP1X_REF_21K",
                },
            },
            device_types=("oic.d.washer",),
        )
        assert conflicting is not None
        assert conflicting.name == "washer"

    def test_empty_device_types_falls_back_to_model_strings(self):
        from custom_components.localthings.registry.by_type import resolve

        resources = {
            "/information/vs/0": {
                "x.com.samsung.da.modelNum": "TP1X_REF_21K",
                "x.com.samsung.da.description": "TP1X_REF_21K",
            },
        }
        reg = resolve(resources, device_types=())
        assert reg is not None
        assert reg.name == "refrigerator"

    def test_unmapped_device_types_falls_back_to_model_strings(self):
        from custom_components.localthings.registry.by_type import resolve

        resources = {
            "/information/vs/0": {
                "x.com.samsung.da.modelNum": "TP1X_REF_21K",
                "x.com.samsung.da.description": "TP1X_REF_21K",
            },
        }
        reg = resolve(resources, device_types=("oic.wk.d", "oic.d.somethingnew"))
        assert reg is not None
        assert reg.name == "refrigerator"

    def test_prefers_resource_signatures_over_model_strings(self, all_device_fixtures):
        """A strong live-resource signature wins over model metadata.

        Across the fixture corpus Qooker is the only intentional disagreement:
        its OVEN model token says oven while its /oven + MicroWave surface says
        microwave. Locking the disagreement set keeps resource-first routing
        from silently becoming greedy as new signatures or fixtures land.
        """
        from custom_components.localthings.registry.by_type import (
            for_device_by_model,
            for_device_by_resources,
            resolve,
        )

        disagreements = {}
        for name, resources in all_device_fixtures.items():
            info = resources.get("/information/vs/0", {})
            by_resources = for_device_by_resources(resources)
            by_model = for_device_by_model(
                info.get("x.com.samsung.da.modelNum", ""),
                info.get("x.com.samsung.da.description", ""),
            )
            if by_resources is None or by_model is None:
                continue
            assert resolve(resources) is by_resources, name
            if by_resources is not by_model:
                disagreements[name] = (by_resources.name, by_model.name)

        assert disagreements == {
            "qooker_mw7500a": ("microwave", "oven"),
        }

    def test_qooker_microwave_surface_overrides_generic_oven_oic_type(self):
        """MW7500A declares oic.d.oven and carries an OVEN board token, but
        its verified local API exposes MicroWave mode on an oven cavity. That
        strong two-resource signature must win ahead of generic OIC metadata."""
        from custom_components.localthings.registry.by_type import (
            for_device_by_model,
            for_device_by_oic_type,
            for_device_by_resources,
            resolve,
        )
        from tests.conftest import _load_device

        resources = _load_device("qooker_mw7500a")
        info = resources["/information/vs/0"]
        by_resources = for_device_by_resources(resources)
        by_oic = for_device_by_oic_type(("oic.wk.d", "oic.d.oven"))
        by_model = for_device_by_model(
            info["x.com.samsung.da.modelNum"],
            info["x.com.samsung.da.description"],
        )
        reg = resolve(resources, device_types=("oic.wk.d", "oic.d.oven"))

        assert by_resources is not None and by_resources.name == "microwave"
        assert by_oic is not None and by_oic.name == "oven"
        assert by_model is not None and by_model.name == "oven"
        assert reg is by_resources

    def test_resource_signature_types_dumps_without_information(self, all_device_fixtures):
        """The three dumps with no /information/vs/0 still type."""
        from custom_components.localthings.registry.by_type import resolve

        for name in ("cooktop", "range_ne63a6511", "range_no_info"):
            resources = all_device_fixtures[name]
            assert "/information/vs/0" not in resources, name
            assert resolve(resources) is not None, name

    def test_returns_none_for_an_unrecognizable_dump(self):
        from custom_components.localthings.registry.by_type import resolve

        assert resolve({"/some/unknown/vs/0": {}}) is None


class TestForDeviceByResources:
    def test_na9300k_without_one_ui_or_information_is_cooktop(self):
        from custom_components.localthings.registry.by_type import for_device_by_resources
        from tests.conftest import _load_device

        reg = for_device_by_resources(_load_device("cooktop"))

        assert reg is not None
        assert reg.name == "gas_cooktop"

    def test_unrelated_mode_options_are_not_cooktop(self):
        from custom_components.localthings.registry.by_type import for_device_by_resources

        resources = {
            "/mode/vs/0": {
                "x.com.samsung.da.options": [
                    "DeviceType_SOME_OVEN",
                    "UpperLamp_Off",
                ],
            },
        }

        assert for_device_by_resources(resources) is None

    def test_hood_resource_signature(self):
        from custom_components.localthings.registry.by_type import for_device_by_resources

        resources = {
            "/hood/fanspeed/vs/0": {},
            "/hood/lamp/vs/0": {},
        }
        reg = for_device_by_resources(resources)
        assert reg is not None
        assert reg.name == "range_hood"

    def test_ne63b8411ss_without_information_or_burner_status_is_range(self):
        """Issue #74: no oneUiVersion, no /information/vs/0 at all, and no
        /cooktop/status/vs/0 burner array -- only /cooktopmonitoring/vs/0.
        'Bake' in supportedModes plus that monitoring resource must still
        route this to the range registry, not plain oven or unknown."""
        from custom_components.localthings.registry.by_type import for_device_by_resources

        resources = {
            "/mode/vs/0": {
                "x.com.samsung.da.supportedModes": ["Bake", "Broil", "SelfClean"],
                "x.com.samsung.da.options": ["DeviceType_NE8411B-/AC0"],
            },
            "/oven/vs/0": {"x.com.samsung.da.state": "Ready"},
            "/cooktopmonitoring/vs/0": {"x.com.samsung.da.cooktopRunningState": "Ready"},
        }
        reg = for_device_by_resources(resources)
        assert reg is not None
        assert reg.name == "range"

    def test_bake_without_cooktop_resource_is_plain_oven(self):
        from custom_components.localthings.registry.by_type import for_device_by_resources

        resources = {
            "/mode/vs/0": {
                "x.com.samsung.da.supportedModes": ["Bake", "Broil"],
                "x.com.samsung.da.options": ["DeviceType_SOME_OVEN"],
            },
            "/oven/vs/0": {"x.com.samsung.da.state": "Ready"},
        }
        reg = for_device_by_resources(resources)
        assert reg is not None
        assert reg.name == "oven"

    def test_bake_without_oven_cavity_resource_is_not_matched(self):
        """'Bake' alone isn't enough -- the oven cavity resource must also
        be present, or this falls through to None like any other unknown
        shape."""
        from custom_components.localthings.registry.by_type import for_device_by_resources

        resources = {
            "/mode/vs/0": {
                "x.com.samsung.da.supportedModes": ["Bake", "Broil"],
            },
        }
        assert for_device_by_resources(resources) is None

    def test_microwave_without_information_is_microwave(self):
        """Issue #172: Samsung Microwave (ME8000T-/AA0) has no /information/vs/0
        resource and empty oneUiVersion; 'MicroWave' in supportedModes alongside
        /oven/vs/0 must route to the microwave registry."""
        from custom_components.localthings.registry.by_type import for_device_by_resources

        resources = {
            "/mode/vs/0": {
                "x.com.samsung.da.supportedModes": ["MicroWave", "Autocook", "Convection"],
                "x.com.samsung.da.options": ["DeviceType_ME8000T-/AA0", "Lamp_Off"],
            },
            "/oven/vs/0": {"x.com.samsung.da.state": "Ready"},
            "/hood/fanspeed/vs/0": {"x.com.samsung.da.hood.fanSpeed": "0"},
        }
        reg = for_device_by_resources(resources)
        assert reg is not None
        assert reg.name == "microwave"

    def test_microwave_mode_without_oven_cavity_is_not_matched(self):
        """The mode vocabulary alone is too common to preempt metadata."""
        from custom_components.localthings.registry.by_type import for_device_by_resources

        resources = {
            "/mode/vs/0": {
                "x.com.samsung.da.supportedModes": ["MicroWave"],
            },
        }

        assert for_device_by_resources(resources) is None

    def test_scalar_supported_modes_is_not_a_microwave_signature(self):
        """Malformed scalar data must not gain resource-first precedence."""
        from custom_components.localthings.registry.by_type import for_device_by_resources

        resources = {
            "/mode/vs/0": {
                "x.com.samsung.da.supportedModes": "MicroWave",
            },
            "/oven/vs/0": {"x.com.samsung.da.state": "Ready"},
        }

        assert for_device_by_resources(resources) is None
