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
