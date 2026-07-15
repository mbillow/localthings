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

    def test_unknown_model_returns_none(self):
        from custom_components.localthings.registry.by_type import for_device_by_model
        reg = for_device_by_model('SOME-UNKNOWN-BOARD', 'SOME-UNKNOWN-BOARD')
        assert reg is None

    def test_empty_inputs_return_none(self):
        from custom_components.localthings.registry.by_type import for_device_by_model
        assert for_device_by_model('', '') is None
