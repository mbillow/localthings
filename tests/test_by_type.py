"""Tests for samsung_appliance/registry/by_type."""
import pytest
from custom_components.localthings.registry.by_type import _type_key, for_device, DeviceRegistry


class TestTypeKey:
    """Tests for _type_key() function."""

    def test_type_key_strips_version_prefix(self):
        """'7.0 Dishwasher' -> 'dishwasher'"""
        assert _type_key("7.0 Dishwasher") == "dishwasher"

    def test_type_key_preserves_spaces_as_underscores(self):
        """'7.0 French Door Refrigerator' -> 'french_door_refrigerator'"""
        assert _type_key("7.0 French Door Refrigerator") == "french_door_refrigerator"

    def test_type_key_no_space_returns_lowercase(self):
        """'Oven' -> 'oven' (no space in string)"""
        assert _type_key("Oven") == "oven"


class TestForDevice:
    """Tests for for_device() function."""

    def test_for_device_returns_dishwasher_registry(self):
        """for_device("7.0 Dishwasher") returns a non-None DeviceRegistry."""
        registry = for_device("7.0 Dishwasher")
        assert registry is not None
        assert isinstance(registry, DeviceRegistry)
        assert registry.name == "dishwasher"

    def test_for_device_unknown_returns_none(self):
        """for_device("7.0 Toaster") returns None for unknown device type."""
        registry = for_device("7.0 Toaster")
        assert registry is None

    def test_for_device_suffix_fallback(self):
        """for_device("7.0 French Door Refrigerator") resolves via suffix fallback."""
        registry = for_device("7.0 French Door Refrigerator")
        assert registry is not None
        assert isinstance(registry, DeviceRegistry)
        assert registry.name == 'refrigerator'

    def test_for_device_returns_cooktop_registry(self):
        """The registry's own .name is 'gas_cooktop' (disambiguated from
        induction_cooktop), but the lookup key devices route through stays
        'cooktop' -- oneUiVersion "Cooktop" still resolves here."""
        registry = for_device('7.0 Cooktop')
        assert registry is not None
        assert registry.name == 'gas_cooktop'

    def test_for_device_returns_range_hood_registry(self):
        registry = for_device('7.0 Range Hood')
        assert registry is not None
        assert registry.name == 'range_hood'


class TestDeviceRegistries:
    """Tests for device registries themselves."""

    def test_dishwasher_registry_has_no_dup_hrefs(self):
        """All caps in dishwasher registry have unique hrefs (or meet disambiguation rule)."""
        registry = for_device("7.0 Dishwasher")
        assert registry is not None

        # Each href should map to exactly one cap (or multiple with rt_filter/match_fn)
        for href, caps in registry.capabilities.items():
            if len(caps) > 1:
                # If multiple caps share an href, all must have rt_filter or match_fn
                for cap in caps:
                    assert cap.rt_filter is not None or cap.match_fn is not None, \
                        f"href {href!r} has multiple caps but {cap!r} lacks rt_filter and match_fn"

    def test_refrigerator_registry_has_no_dup_hrefs(self):
        """All caps in refrigerator registry have unique hrefs (or meet disambiguation rule)."""
        registry = for_device("7.0 Refrigerator")
        assert registry is not None

        # Each href should map to exactly one cap (or multiple with rt_filter/match_fn)
        for href, caps in registry.capabilities.items():
            if len(caps) > 1:
                # If multiple caps share an href, all must have rt_filter or match_fn
                for cap in caps:
                    assert cap.rt_filter is not None or cap.match_fn is not None, \
                        f"href {href!r} has multiple caps but {cap!r} lacks rt_filter and match_fn"


class TestWasherRegistry:
    def test_washer_registry_registered(self):
        from custom_components.localthings.registry.by_type import _REGISTRY_BY_KEY
        assert 'washer' in _REGISTRY_BY_KEY
        assert _REGISTRY_BY_KEY['washer'].name == 'washer'

    def test_washer_registry_has_no_dup_hrefs(self):
        from custom_components.localthings.registry.by_type import _REGISTRY_BY_KEY
        registry = _REGISTRY_BY_KEY['washer']
        for href, caps in registry.capabilities.items():
            if len(caps) > 1:
                for cap in caps:
                    assert cap.rt_filter is not None or cap.match_fn is not None, \
                        f"href {href!r} has multiple caps but {cap!r} lacks rt_filter and match_fn"

    def test_washer_registry_covers_known_hrefs(self):
        from custom_components.localthings.registry.by_type import _REGISTRY_BY_KEY
        registry = _REGISTRY_BY_KEY['washer']
        for href in (
            '/power/0', '/power/vs/0', '/kidslock/0', '/kidslock/vs/0',
            '/remotectrl/0', '/remotectrl/vs/0', '/alarms/vs/0',
            '/energy/consumption/vs/0', '/water/consumption/vs/0',
            '/operational/state/vs/0', '/washer/vs/0', '/course/vs/0',
            '/buzzersound/vs/0', '/wm/jobbeginingstatus/vs/0',
            '/diagnosis/vs/0', '/otninformation/vs/0',
        ):
            assert href in registry.capabilities, f"{href} missing from washer registry"


class TestModelNumSegments:
    def test_splits_pipe_prefix_on_underscore(self):
        from custom_components.localthings.registry.by_type import _model_num_segments
        assert _model_num_segments('ARTIK051_DONGLE_REF|00127641|000800200014') == [
            'ARTIK051', 'DONGLE', 'REF',
        ]

    def test_ignores_everything_after_first_pipe(self):
        from custom_components.localthings.registry.by_type import _model_num_segments
        assert _model_num_segments('TP2X_RAC_20K|abc|REF_should_not_appear') == [
            'TP2X', 'RAC', '20K',
        ]

    def test_empty_for_none_or_empty_input(self):
        from custom_components.localthings.registry.by_type import _model_num_segments
        assert _model_num_segments('') == ['']
        assert _model_num_segments(None) == ['']


class TestConsumerModelKey:
    def test_finds_key_in_last_segment(self):
        from custom_components.localthings.registry.by_type import _consumer_model_key
        assert _consumer_model_key('DA_WM_TP1_21_COMMON_WW5000C') == 'washer'

    def test_finds_key_before_a_trailing_unrecognized_segment(self):
        """Issue #79: 'DVE50A8800_8600' pairs two model numbers -- the real
        consumer token is the second-to-last segment, not the last."""
        from custom_components.localthings.registry.by_type import _consumer_model_key
        assert _consumer_model_key(
            'DA_WM_TP1_21_COMMON_DVE50A8800_8600/DC92-02835A_0080') == 'dryer'

    def test_ignores_everything_after_first_slash(self):
        from custom_components.localthings.registry.by_type import _consumer_model_key
        assert _consumer_model_key('DA_WM_TP1_21_COMMON_WW5000C/DW9000_board') == 'washer'

    def test_none_when_no_segment_matches(self):
        from custom_components.localthings.registry.by_type import _consumer_model_key
        assert _consumer_model_key('ARTIK051_DONGLE_REF') is None


class TestForDeviceByModel:
    """Fallback device-type detection for hardware without oneUiVersion."""

    def test_washer_ww_prefix(self):
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'DA_WM_TP1_21_COMMON|20375141|20010002001811424AA30217008A0000',
            'DA_WM_TP1_21_COMMON_WW5000C/DC92-03495A_B048',
        )
        assert reg is not None
        assert reg.name == 'washer'

    def test_washer_wd_prefix(self):
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'DA_WM_TP1_21_COMMON|20375141|20010002001811424AA30217008A0000',
            'DA_WM_TP1_21_COMMON_WD7000B/DC92-03724A_004D',
        )
        assert reg is not None
        assert reg.name == 'washer'

    def test_dryer_not_misdetected_as_washer(self):
        """Dryer shares the DA_WM_ board prefix with washer -- must not
        be misrouted despite the shared prefix."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'DA_WM_TP2_20_COMMON_DV5000T', 'DA_WM_TP2_20_COMMON_DV5000T',
        )
        assert reg is not None
        assert reg.name == 'dryer'

    def test_dryer_dve50a8600_paired_model_numbers_in_description(self):
        """Issue #79: description pairs two model numbers
        ('..._DVE50A8800_8600/DC92-...'), so the 'DV' consumer token is one
        segment before the literal last segment ('8600', which has no
        recognizable prefix on its own). The old last-segment-only check
        fell through to 'unknown' here."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'DA_WM_TP1_21_COMMON|20286441|300000010015110002A3031700000000',
            'DA_WM_TP1_21_COMMON_DVE50A8800_8600/DC92-02835A_0080',
        )
        assert reg is not None
        assert reg.name == 'dryer'

    def test_dishwasher_not_misdetected_as_washer(self):
        """Dishwasher's modelNum contains the substring 'WW' -- must not
        be misrouted by a naive substring match."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'ADW-WW-RTL-24-AILITE|90000541|400002010019130059C1000500E10000',
            'ADW-WW-RTL-24-AILITE_DW9000F/DD91-00002A_0002',
        )
        assert reg is not None
        assert reg.name == 'dishwasher'

    def test_refrigerator_via_modelnum_ref_token(self):
        """Refrigerator's description has no consumer-model suffix; falls
        back to the '_REF_' token in modelNum."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'TP1X_REF_21K|00176141|00000850031813294103010041030000',
            'TP1X_REF_21K',
        )
        assert reg is not None
        assert reg.name == 'refrigerator'

    def test_refrigerator_rl_series_via_ref_token(self):
        """Issue #7: RL38C6B0CWW/EG (a bottom-freezer RL-series fridge, not
        the RF9000-style french-door this module was originally verified
        against) reports description/modelNum 'TP1X_REF_21K' -- same
        internal platform code as any other TP1X-based fridge, so the
        existing '_REF_' fallback already resolves it correctly."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'TP1X_REF_21K|00156941|00050126001611304100000031010000',
            'TP1X_REF_21K',
        )
        assert reg is not None
        assert reg.name == 'refrigerator'

    def test_refrigerator_dongle_ref_pipe_delimited_modelnum(self):
        """Issues #77/#83: the ARTIK051_DONGLE_REF family's modelNum is
        '<board>_DONGLE_REF|<rest>' -- REF is the last underscore segment
        before the pipe, with no trailing underscore, so the plain '_REF_'
        substring check used to miss it entirely and the device fell back
        to 'unknown' with only common capabilities."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'ARTIK051_DONGLE_REF|00127641|00080020001430300100000000000000',
            'ARTIK_REF_17K',
        )
        assert reg is not None
        assert reg.name == 'refrigerator'

    def test_airconditioner_via_prac_token(self):
        """Issue #17: a room AC (ARTIK051_PRAC_20K) reports no oneUiVersion and
        an unrecognized consumer token ('20K'); it falls back to the '_PRAC_'
        (Package Room Air Conditioner) token in modelNum."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'ARTIK051_PRAC_20K|10217841|60010532001411004200003000000000',
            'ARTIK051_PRAC_20K',
        )
        assert reg is not None
        assert reg.name == 'airconditioner'

    def test_dehumidifier_via_dhm_token(self):
        """Issue #88: a dehumidifier (AY18CG7500GED) shares the DA_AC_ board
        family with the room-AC models but reports no oneUiVersion and
        carries the '_DHM_' (DeHuMidifier) token instead of
        '_RAC_'/'_PRAC_'/'_WAC_'; falls back to the '_DHM_' token in
        modelNum."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'TP1X_DA_AC_DHM_01001_0000|10253841|77000000001700000A00000000000000',
            'TP1X_DA_AC_DHM_01001_0000',
        )
        assert reg is not None
        assert reg.name == 'dehumidifier'

    def test_water_purifier_via_waterpurifier_token(self):
        """Issue #90: a water purifier (TP2X_WATERPURIFIER_20K) reports no
        oneUiVersion and no consumer-prefix match; falls back to the
        'WATERPURIFIER' token shared by modelNum and description."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'TP2X_WATERPURIFIER_20K|00132341|900000000215130001060F0000020000',
            'TP2X_WATERPURIFIER_20K',
        )
        assert reg is not None
        assert reg.name == 'water_purifier'

    def test_airconditioner_via_wac_token(self):
        """Issue #87: a Bespoke Window AC (AW06C7155EWAZ) reports no
        oneUiVersion and a modelNum carrying the '_WAC_' (Window Air
        Conditioner) token instead of '_RAC_'/'_PRAC_'; falls back to the
        '_WAC_' token in modelNum."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'TP1X_DA_AC_WAC_01001_0000|40460041|50030018001611020A00000000000000',
            'AW06C7155EWAZ',
        )
        assert reg is not None
        assert reg.name == 'airconditioner'

    def test_wac_token_not_shadowed_by_wa_washer_prefix(self):
        """Regression: adding the 'WA' consumer-model prefix (issue #106)
        must not hijack devices whose description literally equals their
        modelNum (e.g. the Window AC family, issue #87) and so contains a
        'WAC' segment -- 'WAC'[:2] == 'WA' would otherwise match the washer
        prefix before the more specific '_WAC_' modelNum check ever runs."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'TP1X_DA_AC_WAC_01001_0000|40460041|50030018001611020A00000000000000',
            'TP1X_DA_AC_WAC_01001_0000',
        )
        assert reg is not None
        assert reg.name == 'airconditioner'

    def test_cooktop_via_legacy_model_description(self):
        """Older cooktops identify themselves as ARTIK051_GLOBAL_COOKTOP."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'ARTIK051_GB_CT_001|40424141|50000204001211000200000000000000',
            'ARTIK051_GLOBAL_COOKTOP',
        )
        assert reg is not None
        assert reg.name == 'gas_cooktop'

    def test_range_hood_via_ahd_model(self):
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'AHD-WW-TP1-22-COMMON|20136141|7800006B001713C44D00090001030000',
            'AHD-WW-TP1-22-COMMON',
        )
        assert reg is not None
        assert reg.name == 'range_hood'

    def test_washer_wf_prefix(self):
        """US front-load washers use the WF consumer-model prefix."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'DA_WM_TP1_21_COMMON|20313741|20010001001611244AA3021700000000',
            'DA_WM_TP1_21_COMMON_WF8900B/DC92-03129A_A0AE',
        )
        assert reg is not None
        assert reg.name == 'washer'

    def test_washer_wv_prefix(self):
        """FlexWash twin washers use the WV consumer-model prefix -- issue #19."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'DA_WM_A51_20_COMMON|20198042|20020001001111400203000000000000',
            'DA_WM_A51_20_COMMON_WV9600M/DC92-01980B_0014',
        )
        assert reg is not None
        assert reg.name == 'washer'

    def test_washer_wa_prefix(self):
        """Top-load washers (e.g. WA8000T) use the WA consumer-model prefix
        -- issue #106."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'DA_WM_TP2_20_COMMON|20233741|200000010014110002A3020200000000',
            'DA_WM_TP2_20_COMMON_WA8000T/DC92-02810A_0002',
        )
        assert reg is not None
        assert reg.name == 'washer'

    def test_oven_via_oven_token(self):
        """Issue #55: a wall oven (NV7000BS/ET5) reports no oneUiVersion and
        an unrecognized consumer token ('NV'); it falls back to the '-OVEN-'
        token in modelNum, mirroring the '-RANGE-' fallback for issue #44."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'TP1X_DA-KS-OVEN-0107X|40460041|50030018001611020A00000000000000',
            'NV7000BS/ET5',
        )
        assert reg is not None
        assert reg.name == 'oven'

    def test_induction_cooktop_via_hyphenated_cooktop_token(self):
        """Issue #86: a standalone induction cooktop (NV8500T-/KO4) reports
        no oneUiVersion and an unrecognized consumer token ('NV'); it falls
        back to the hyphenated '-COOKTOP-' token in modelNum -- distinct
        from the underscore-delimited '_COOKTOP'/'_GB_CT_' check, which is
        the unrelated older NA9300K gas-cooktop family."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model(
            'TP1X_DA-KS-COOKTOP-01011|40459741|50000203001711000A00000000000000',
            'NV8500T-/KO4',
        )
        assert reg is not None
        assert reg.name == 'induction_cooktop'

    def test_hyphenated_cooktop_token_not_confused_with_range(self):
        """'-COOKTOP-' and '-RANGE-' must route to distinct registries even
        though both are TP1X_DA-KS- board-family siblings."""
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model('TP1X_DA-KS-COOKTOP-01011', 'NV8500T-/KO4')
        assert reg is not None
        assert reg.name != 'range'

    def test_unknown_model_returns_none(self):
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model('SOME-UNKNOWN-BOARD', 'SOME-UNKNOWN-BOARD')
        assert reg is None

    def test_empty_inputs_return_none(self):
        from custom_components.localthings.registry.by_type import for_device_by_model
        assert for_device_by_model('', '') is None


class TestForDeviceByResources:
    def test_na9300k_without_one_ui_or_information_is_cooktop(self):
        from custom_components.localthings.registry.by_type import for_device_by_resources
        from tests.conftest import _load_device

        reg = for_device_by_resources(_load_device('cooktop'))

        assert reg is not None
        assert reg.name == 'gas_cooktop'

    def test_unrelated_mode_options_are_not_cooktop(self):
        from custom_components.localthings.registry.by_type import for_device_by_resources

        resources = {
            '/mode/vs/0': {
                'x.com.samsung.da.options': [
                    'DeviceType_SOME_OVEN',
                    'UpperLamp_Off',
                ],
            },
        }

        assert for_device_by_resources(resources) is None

    def test_hood_resource_signature(self):
        from custom_components.localthings.registry.by_type import for_device_by_resources
        resources = {
            '/hood/fanspeed/vs/0': {},
            '/hood/lamp/vs/0': {},
        }
        reg = for_device_by_resources(resources)
        assert reg is not None
        assert reg.name == 'range_hood'

    def test_ne63b8411ss_without_information_or_burner_status_is_range(self):
        """Issue #74: no oneUiVersion, no /information/vs/0 at all, and no
        /cooktop/status/vs/0 burner array -- only /cooktopmonitoring/vs/0.
        'Bake' in supportedModes plus that monitoring resource must still
        route this to the range registry, not plain oven or unknown."""
        from custom_components.localthings.registry.by_type import for_device_by_resources
        resources = {
            '/mode/vs/0': {
                'x.com.samsung.da.supportedModes': ['Bake', 'Broil', 'SelfClean'],
                'x.com.samsung.da.options': ['DeviceType_NE8411B-/AC0'],
            },
            '/oven/vs/0': {'x.com.samsung.da.state': 'Ready'},
            '/cooktopmonitoring/vs/0': {'x.com.samsung.da.cooktopRunningState': 'Ready'},
        }
        reg = for_device_by_resources(resources)
        assert reg is not None
        assert reg.name == 'range'

    def test_bake_without_cooktop_resource_is_plain_oven(self):
        from custom_components.localthings.registry.by_type import for_device_by_resources
        resources = {
            '/mode/vs/0': {
                'x.com.samsung.da.supportedModes': ['Bake', 'Broil'],
                'x.com.samsung.da.options': ['DeviceType_SOME_OVEN'],
            },
            '/oven/vs/0': {'x.com.samsung.da.state': 'Ready'},
        }
        reg = for_device_by_resources(resources)
        assert reg is not None
        assert reg.name == 'oven'

    def test_bake_without_oven_cavity_resource_is_not_matched(self):
        """'Bake' alone isn't enough -- the oven cavity resource must also
        be present, or this falls through to None like any other unknown
        shape."""
        from custom_components.localthings.registry.by_type import for_device_by_resources
        resources = {
            '/mode/vs/0': {
                'x.com.samsung.da.supportedModes': ['Bake', 'Broil'],
            },
        }
        assert for_device_by_resources(resources) is None
