from custom_components.localthings.registry.capabilities import common
from custom_components.localthings.registry.discovery import discover


def _reg():
    return {c.href: [c] for c in (
        common.KIDS_LOCK_GENERIC, common.KIDS_LOCK_VS_FALLBACK,
        common.REMOTE_CONTROL_GENERIC, common.REMOTE_CONTROL_VS_FALLBACK,
        common.POWER_GENERIC, common.POWER_VS_FALLBACK,
        common.ALARMS, common.ENERGY_METER, common.WATER_METER,
        common.WATER_FILTER,
    )}


def test_kids_lock_vs_value_fn():
    desc = common.KIDS_LOCK_VS_FALLBACK.entities[0]
    assert desc.value_fn('Lock') is True
    assert desc.value_fn('Ready') is False


def test_common_caps_discover_on_dishwasher(dishwasher_resources):
    bound = discover(dishwasher_resources, _reg())
    keys = {b.desc.key for b in bound}
    assert 'child_lock' in keys
    assert 'remote_control' in keys
    assert 'power_switch' in keys


# ---------------------------------------------------------------------------
# OCF-native / vendor '-vs' fallback pairs (power, kids-lock, remote control).
# ---------------------------------------------------------------------------


class TestPowerFallback:
    def test_generic_href_read_write(self):
        assert common.POWER_GENERIC.href == '/power/0'
        desc = common.POWER_GENERIC.entities[0]
        assert desc.value_fn(True) is True
        assert desc.value_fn(False) is False
        path, body = desc.write_fn('On', {})
        assert path == ['power', '0']
        assert body == {'value': True}
        assert desc.write_fn('Off', {})[1] == {'value': False}

    def test_vs_fallback_binds_only_when_generic_absent(self):
        assert common.POWER_VS_FALLBACK.href == '/power/vs/0'
        assert common.POWER_VS_FALLBACK.match_fn({}, {'/power/vs/0': {}}) is True
        assert common.POWER_VS_FALLBACK.match_fn(
            {}, {'/power/0': {}, '/power/vs/0': {}}) is False

    def test_vs_fallback_read_write(self):
        desc = common.POWER_VS_FALLBACK.entities[0]
        assert desc.value_fn('On') is True
        assert desc.value_fn('Off') is False
        path, body = desc.write_fn('On', {})
        assert path == ['power', 'vs', '0']
        assert body == {'x.com.samsung.da.power': 'On'}


class TestKidsLockFallback:
    def test_generic_read_write(self):
        assert common.KIDS_LOCK_GENERIC.href == '/kidslock/0'
        desc = common.KIDS_LOCK_GENERIC.entities[0]
        assert desc.value_fn(True) is True
        path, body = desc.write_fn('On', {})
        assert path == ['kidslock', '0']
        assert body == {'value': True}

    def test_vs_fallback_gated(self):
        assert common.KIDS_LOCK_VS_FALLBACK.match_fn({}, {'/kidslock/vs/0': {}}) is True
        assert common.KIDS_LOCK_VS_FALLBACK.match_fn(
            {}, {'/kidslock/0': {}, '/kidslock/vs/0': {}}) is False


class TestRemoteControlFallback:
    def test_generic_read(self):
        assert common.REMOTE_CONTROL_GENERIC.href == '/remotectrl/0'
        desc = common.REMOTE_CONTROL_GENERIC.entities[0]
        assert desc.value_fn(True) is True
        assert desc.value_fn(False) is False

    def test_vs_fallback_gated(self):
        assert common.REMOTE_CONTROL_VS_FALLBACK.match_fn({}, {'/remotectrl/vs/0': {}}) is True
        assert common.REMOTE_CONTROL_VS_FALLBACK.match_fn(
            {}, {'/remotectrl/0': {}, '/remotectrl/vs/0': {}}) is False

    def test_polled_warm_so_write_gating_stays_fresh(self):
        """coordinator.async_send_command blocks writes on this signal, so
        it can't sit in the default 'cold' tier (refreshed only once per
        30s summary poll) -- it needs the subscribe/subpoll cadence 'warm'
        and 'hot' hrefs get instead."""
        assert common.REMOTE_CONTROL_GENERIC.poll_tier == 'warm'
        assert common.REMOTE_CONTROL_VS_FALLBACK.poll_tier == 'warm'


# ---------------------------------------------------------------------------
# Energy meter. instantaneousPower clamps negatives to 0, but the constant
# '-500' sentinel (a dead field on DA_WM_ laundry + dishwasher dumps, issue #6)
# gates power_watts out entirely so it doesn't read as a real idle "0 W".
# ---------------------------------------------------------------------------


class TestEnergyMeter:
    def test_href(self):
        assert common.ENERGY_METER.href == '/energy/consumption/vs/0'

    def test_power_clamps_negative(self):
        pw = next(e for e in common.ENERGY_METER.entities if e.key == 'power_watts')
        assert pw.value_fn(-500.0) == 0.0
        assert pw.value_fn(93.0) == 93.0

    def test_power_watts_hidden_for_dead_sentinel(self):
        pw = next(e for e in common.ENERGY_METER.entities if e.key == 'power_watts')
        assert pw.exists_fn({'x.com.samsung.da.instantaneousPower': '-500'}, {}) is False

    def test_power_watts_shown_for_real_value(self):
        pw = next(e for e in common.ENERGY_METER.entities if e.key == 'power_watts')
        assert pw.exists_fn({'x.com.samsung.da.instantaneousPower': '150'}, {}) is True

    def test_energy_kwh_hidden_when_cumulative_power_absent(self):
        kwh = next(e for e in common.ENERGY_METER.entities if e.key == 'energy_kwh')
        assert kwh.exists_fn({'x.com.samsung.da.instantaneousPower': '-500'}, {}) is False

    def test_energy_kwh_shown_when_present(self):
        kwh = next(e for e in common.ENERGY_METER.entities if e.key == 'energy_kwh')
        assert kwh.exists_fn({'x.com.samsung.da.cumulativePower': '58900'}, {}) is True

    def test_both_entities_included_on_empty_stub(self):
        """An empty {} rep means the resource exists but data isn't fetched yet
        (see entity._is_included) -- include both so sub-polls populate them."""
        pw = next(e for e in common.ENERGY_METER.entities if e.key == 'power_watts')
        kwh = next(e for e in common.ENERGY_METER.entities if e.key == 'energy_kwh')
        assert pw.exists_fn({}, {}) is True
        assert kwh.exists_fn({}, {}) is True

    def test_power_watts_hidden_when_field_absent_in_populated_rep(self):
        """A populated rep that lacks instantaneousPower must not spawn a
        phantom power sensor (the exists_fn replaces the field-presence gate)."""
        pw = next(e for e in common.ENERGY_METER.entities if e.key == 'power_watts')
        assert pw.exists_fn({'x.com.samsung.da.cumulativePower': '5'}, {}) is False


# ---------------------------------------------------------------------------
# AI energy-saving level. '0' is off; supportedAiLevel lists the additional
# level(s) on offer. A single-entry list (issue #21 fridge, issue #40 washer)
# is really a binary toggle, so it's exposed as a switch instead of a
# one-choice select; multiple entries get a select with '0' synthesized back
# in as the explicit off option (supportedAiLevel never lists '0' itself, but
# it's a real, observed value of aiLevel).
# ---------------------------------------------------------------------------


def _ai_energy_level_desc(cls_name):
    return next(e for e in common.AI_ENERGY_LEVEL.entities
                if e.__class__.__name__ == cls_name)


class TestAiEnergyLevelSwitch:
    def _desc(self):
        return _ai_energy_level_desc('SwitchDesc')

    def test_href(self):
        assert common.AI_ENERGY_LEVEL.href == '/energy/ailevel/vs/0'

    def test_shown_only_with_single_supported_level(self):
        desc = self._desc()
        assert desc.exists_fn({'aiLevel': '1', 'supportedAiLevel': ['1']}, {}) is True
        assert desc.exists_fn({'aiLevel': '1', 'supportedAiLevel': ['1', '2']}, {}) is False

    def test_hidden_when_supported_level_is_non_list_scalar(self):
        """A stray scalar (e.g. a string) must not be len()-checked as if it
        were a list -- a 2-char string would otherwise wrongly pass `== 1`
        style checks."""
        desc = self._desc()
        assert desc.exists_fn({'aiLevel': '1', 'supportedAiLevel': '1'}, {}) is False

    def test_hidden_when_supported_level_missing(self):
        desc = self._desc()
        assert desc.exists_fn({'aiLevel': '1'}, {}) is False

    def test_hidden_when_supported_level_empty_list(self):
        desc = self._desc()
        assert desc.exists_fn({'aiLevel': '0', 'supportedAiLevel': []}, {}) is False

    def test_hidden_on_empty_stub_rep(self):
        desc = self._desc()
        assert desc.exists_fn({}, {}) is False

    def test_value_fn(self):
        desc = self._desc()
        assert desc.value_fn('0') is False
        assert desc.value_fn('1') is True

    def test_write_on_uses_the_single_supported_level(self):
        """The on-value is whatever the device calls its one level, not a
        hardcoded '1'."""
        desc = self._desc()
        path, body = desc.write_fn('On', {'supportedAiLevel': ['2']})
        assert path == ['energy', 'ailevel', 'vs', '0']
        assert body == {'aiLevel': '2'}

    def test_write_off(self):
        desc = self._desc()
        path, body = desc.write_fn('Off', {'supportedAiLevel': ['1']})
        assert body == {'aiLevel': '0'}


class TestAiEnergyLevelSelect:
    def _desc(self):
        return _ai_energy_level_desc('SelectDesc')

    def test_shown_only_with_multiple_supported_levels(self):
        desc = self._desc()
        assert desc.exists_fn({'aiLevel': '1', 'supportedAiLevel': ['1', '2']}, {}) is True
        assert desc.exists_fn({'aiLevel': '1', 'supportedAiLevel': ['1']}, {}) is False

    def test_hidden_when_supported_level_is_non_list_scalar(self):
        desc = self._desc()
        assert desc.exists_fn({'aiLevel': '1', 'supportedAiLevel': '12'}, {}) is False

    def test_hidden_when_supported_level_missing(self):
        desc = self._desc()
        assert desc.exists_fn({'aiLevel': '1'}, {}) is False

    def test_hidden_when_supported_level_empty_list(self):
        desc = self._desc()
        assert desc.exists_fn({'aiLevel': '0', 'supportedAiLevel': []}, {}) is False

    def test_no_translation_key(self):
        """aiLevel's values are plain digits that render fine untranslated
        (select.py's _display()) -- no strings.json entry to maintain
        against an unknown number of future levels."""
        desc = self._desc()
        assert desc.translation_key is None

    def test_options_synthesize_off(self):
        """'0' is never in supportedAiLevel but is a real, observed aiLevel
        value -- synthesized back in as the explicit off option."""
        desc = self._desc()
        resources = {'/energy/ailevel/vs/0': {'supportedAiLevel': ['1', '2']}}
        assert desc.options(resources) == ['0', '1', '2']

    def test_options_empty_when_resource_missing(self):
        desc = self._desc()
        assert desc.options({}) == ['0']

    def test_write(self):
        desc = self._desc()
        path, body = desc.write_fn('2', {})
        assert path == ['energy', 'ailevel', 'vs', '0']
        assert body == {'aiLevel': '2'}


class TestAiEnergyLevelStubDoesNotDecideThePlatform:
    """Issue found in review: entity *creation* runs once, against whatever
    snapshot is current when platforms are set up (see
    __init__.py's async_config_entry_first_refresh-before-forward-entry-setups
    ordering), while flatten() re-evaluates exists_fn every poll against live
    data. Both descriptors share key='ai_energy_level' (see adapter._key), so
    if a stub carve-out let one of them win at setup time while the other
    wins once real data lands, flatten() would feed the already-instantiated
    entity a value shaped for the other platform (e.g. a bool into a Select
    expecting a string option). Neither side gets a `not rep` carve-out, so
    an unfetched stub can never win entity creation for either platform --
    the entity simply doesn't appear until a reload happens with real data,
    same as any other exists_fn-gated entity in this codebase that's unlucky
    on first-poll timing, instead of appearing as the wrong widget type."""

    def test_neither_widget_exists_on_empty_stub_rep(self):
        switch = _ai_energy_level_desc('SwitchDesc')
        select = _ai_energy_level_desc('SelectDesc')
        assert switch.exists_fn({}, {}) is False
        assert select.exists_fn({}, {}) is False


class TestSelfCheckError:
    """Self-check diagnostic error list -- surfaced on hardware that reports
    x.com.samsung.da.error, joined into a single display string."""

    def _desc(self):
        return next(e for e in common.SELF_CHECK.entities if e.key == 'selfcheck_error')

    def test_exists_when_field_present(self):
        desc = self._desc()
        assert desc.exists_fn({'x.com.samsung.da.error': ['DA_ERROR_NONE']}, {}) is True

    def test_does_not_exist_when_field_absent(self):
        desc = self._desc()
        assert desc.exists_fn({'x.com.samsung.da.status': 'Ready'}, {}) is False

    def test_exists_for_empty_stub_rep(self):
        """An empty {} rep is /device/0's not-yet-fetched-stub carve-out --
        must be included-for-now, same as ENERGY_METER's fields."""
        desc = self._desc()
        assert desc.exists_fn({}, {}) is True

    def test_value_joins_list(self):
        desc = self._desc()
        assert desc.value_fn(['E1', 'E2']) == 'E1, E2'

    def test_value_passes_through_scalar(self):
        desc = self._desc()
        assert desc.value_fn('DA_ERROR_NONE') == 'DA_ERROR_NONE'

    def test_value_none_for_empty_list(self):
        """An empty error list means no value to show -- None (unknown),
        not an empty string."""
        desc = self._desc()
        assert desc.value_fn([]) is None


# ---------------------------------------------------------------------------
# Cross-family bundles (UNIVERSAL / POWER) -- unpacked into every by_type
# registry's _build([...]) call in place of the hand-duplicated capability
# lists that used to live there.
# ---------------------------------------------------------------------------


class TestUniversalAndPowerBundles:
    def test_universal_contains_the_no_conflict_capabilities(self):
        assert set(common.UNIVERSAL) == {
            common.ALARMS, common.ENERGY_METER, common.FIRMWARE_UPDATE,
            common.SELF_CHECK, common.AI_ENERGY_LEVEL,
            common.KIDS_LOCK_GENERIC, common.KIDS_LOCK_VS_FALLBACK,
            common.REMOTE_CONTROL_GENERIC, common.REMOTE_CONTROL_VS_FALLBACK,
        }

    def test_power_kept_separate_for_airconditioners_sake(self):
        """See common.POWER's own comment for why airconditioner opts out."""
        assert set(common.POWER) == {common.POWER_GENERIC, common.POWER_VS_FALLBACK}

    def test_no_overlap_between_the_two_bundles(self):
        assert not (set(common.UNIVERSAL) & set(common.POWER))

    def test_airconditioner_registry_does_not_include_power(self):
        from custom_components.localthings.registry.by_type import airconditioner
        bound_caps = {c for caps in airconditioner.REGISTRY.capabilities.values() for c in caps}
        assert common.POWER_GENERIC not in bound_caps
        assert common.POWER_VS_FALLBACK not in bound_caps
