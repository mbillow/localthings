"""Tests for fridge-specific capabilities."""
from custom_components.localthings.registry.capabilities import fridge


class TestTempCurrentGeneric:
    """Issue #7: unit must be read live from the device, not assumed to be
    Fahrenheit -- a TP1X_REF_21K fridge (RL38C6B0CWW/EG) reports the same
    resource in Celsius."""

    def test_unit_reads_celsius(self):
        desc = fridge.TEMP_CURRENT_GENERIC.entities[0]
        assert desc.unit_fn({'temperature': 3.0, 'units': 'C'}) == '°C'

    def test_unit_reads_fahrenheit(self):
        desc = fridge.TEMP_CURRENT_GENERIC.entities[0]
        assert desc.unit_fn({'temperature': 5.0, 'units': 'F'}) == '°F'

    def test_unit_defaults_to_fahrenheit_when_missing(self):
        desc = fridge.TEMP_CURRENT_GENERIC.entities[0]
        assert desc.unit_fn({'temperature': 5.0}) == '°F'


class TestTempSetpointGeneric:
    def test_unit_reads_celsius(self):
        desc = fridge.TEMP_SETPOINT_GENERIC.entities[0]
        assert desc.unit_fn({'temperature': -19.0, 'units': 'C'}) == '°C'


class TestTemperaturesFallback:
    """Aggregate /temperatures/vs/0 -- per-item unit from
    x.com.samsung.da.unit ('Celsius'/'Fahrenheit')."""

    def test_freezer_unit_celsius(self):
        desc = next(e for e in fridge.TEMPERATURES_FALLBACK.entities
                    if e.key == 'freezer_temperature')
        rep = {'x.com.samsung.da.items': [
            {'x.com.samsung.da.description': 'Freezer', 'x.com.samsung.da.current': '-19',
             'x.com.samsung.da.unit': 'Celsius'},
            {'x.com.samsung.da.description': 'Fridge', 'x.com.samsung.da.current': '3',
             'x.com.samsung.da.unit': 'Celsius'},
        ]}
        assert desc.unit_fn(rep) == '°C'
        assert desc.value_fn(rep['x.com.samsung.da.items']) == -19

    def test_fridge_unit_fahrenheit(self):
        desc = next(e for e in fridge.TEMPERATURES_FALLBACK.entities
                    if e.key == 'fridge_temperature')
        rep = {'x.com.samsung.da.items': [
            {'x.com.samsung.da.description': 'Fridge', 'x.com.samsung.da.current': '37',
             'x.com.samsung.da.unit': 'Fahrenheit'},
        ]}
        assert desc.unit_fn(rep) == '°F'

    def test_unit_defaults_to_fahrenheit_when_item_missing(self):
        desc = next(e for e in fridge.TEMPERATURES_FALLBACK.entities
                    if e.key == 'freezer_temperature')
        assert desc.unit_fn({'x.com.samsung.da.items': []}) == '°F'


class TestDefrostBlockStatus:
    """DEFROST_BLOCK_ON means the defrost cycle is actively running, not
    that defrost is being withheld -- confirmed against live dumps showing
    it ON while defrost_delay is off."""

    def test_key_and_name_reflect_active_defrosting(self):
        desc = fridge.DEFROST_BLOCK_STATUS.entities[0]
        assert desc.key == 'defrost_active'

    def test_value_fn(self):
        desc = fridge.DEFROST_BLOCK_STATUS.entities[0]
        assert desc.value_fn(['DEFROST_BLOCK_ON']) is True
        assert desc.value_fn(['DEFROST_BLOCK_OFF']) is False


class TestRefrigerationFallback:
    """/refrigeration/0 duplicates two of REFRIGERATION's (/refrigeration/vs/0)
    fields under different names (rapidFreeze/rapidCool vs
    rapidFreezing/rapidFridge) but also carries one genuinely new field
    (defrost) that has no vs-href equivalent."""

    def test_href(self):
        assert fridge.REFRIGERATION_FALLBACK.href == '/refrigeration/0'

    def test_defrost_active_is_fallback_of_defrost_block_status(self):
        # Duplicates DEFROST_BLOCK_STATUS's defrost_active
        # (/defrost/block/vs/0) -- only a true fallback when that richer
        # href is absent (issue #7's device: /refrigeration/0 only).
        desc = next(e for e in fridge.REFRIGERATION_FALLBACK.entities
                    if e.key == 'defrost_active')
        assert desc.value_fn(True) is True
        assert desc.value_fn(False) is False
        assert desc.exists_fn({}, {'/refrigeration/0': {}}) is True
        assert desc.exists_fn(
            {}, {'/refrigeration/0': {}, '/defrost/block/vs/0': {}}) is False

    def test_rapid_switches_hidden_when_vs_href_present(self):
        for key in ('rapid_fridge', 'rapid_freezing'):
            desc = next(e for e in fridge.REFRIGERATION_FALLBACK.entities if e.key == key)
            assert desc.exists_fn({}, {'/refrigeration/vs/0': {}, '/refrigeration/0': {}}) is False

    def test_rapid_switches_shown_when_vs_href_absent(self):
        for key in ('rapid_fridge', 'rapid_freezing'):
            desc = next(e for e in fridge.REFRIGERATION_FALLBACK.entities if e.key == key)
            assert desc.exists_fn({}, {'/refrigeration/0': {}}) is True


class TestFlexZone:
    """Issue #32 (surfaced by #26/#27's TP2X/Bespoke dumps): the flex-zone
    entry's prefix varies by fridge family (CV_TTYPE_RF9000A_ on RF9000-class,
    CV_FDR_ on TP1X/Bespoke-class). The old hardcoded-prefix match left
    CV_FDR_-family fridges with an always-unknown select, and a write would
    have appended a duplicate flag instead of replacing the existing one."""

    def test_reads_rf9000_prefix(self):
        rep = {
            'x.com.samsung.da.modes': ['CV_TTYPE_RF9000A_BEVERAGE', 'WATERFILTER_DISABLE'],
            'x.com.samsung.da.supportedOptions': [
                'CV_TTYPE_RF9000A_FREEZE', 'CV_TTYPE_RF9000A_BEVERAGE'],
        }
        assert fridge._flex_zone_current(rep) == 'CV_TTYPE_RF9000A_BEVERAGE'

    def test_reads_cv_fdr_prefix(self):
        rep = {
            'x.com.samsung.da.modes': ['CVN_CONVERTIBLE_ZONE', 'CV_FDR_MEAT', 'WATERFILTER_ENABLE'],
            'x.com.samsung.da.supportedOptions': [
                'CV_FDR_WINE', 'CV_FDR_DELI', 'CV_FDR_BEVERAGE', 'CV_FDR_MEAT'],
        }
        assert fridge._flex_zone_current(rep) == 'CV_FDR_MEAT'

    def test_write_replaces_rather_than_duplicates(self):
        rep = {
            'x.com.samsung.da.modes': ['CVN_CONVERTIBLE_ZONE', 'CV_FDR_MEAT', 'WATERFILTER_ENABLE'],
            'x.com.samsung.da.supportedOptions': [
                'CV_FDR_WINE', 'CV_FDR_DELI', 'CV_FDR_BEVERAGE', 'CV_FDR_MEAT'],
        }
        path, payload = fridge._flex_zone_write('CV_FDR_WINE', rep)
        assert path == ['mode', 'vs', '0']
        modes = payload['x.com.samsung.da.modes']
        assert modes.count('CV_FDR_WINE') == 1
        assert 'CV_FDR_MEAT' not in modes
        assert 'CVN_CONVERTIBLE_ZONE' in modes and 'WATERFILTER_ENABLE' in modes


class TestTp1xNativeDuplicateResources:
    """The US TP1X_REF_21K publishes two native mirrors in addition to the
    richer vendor resources.  They must count as covered without producing
    duplicate entities or guessing unverified write contracts."""

    def test_duplicate_capabilities_have_no_entities(self):
        assert fridge.DEFROST_DELAY_NATIVE_DUPLICATE.entities == ()
        assert fridge.ICEMAKER_STATUS_NATIVE_DUPLICATE.entities == ()

    def test_us_fixture_has_complete_coverage(self):
        from custom_components.localthings.registry.adapter import flatten
        from custom_components.localthings.registry.by_type import refrigerator
        from custom_components.localthings.registry.discovery import discover
        from tests.conftest import _load_device

        resources = _load_device('refrigerator_tp1x_ref_21k_us')
        unbound = []
        bound = discover(
            resources,
            refrigerator.REGISTRY.capabilities,
            refrigerator.REGISTRY.pattern_capabilities,
            log=unbound.append,
        )

        assert unbound == []
        state = flatten(bound, resources)
        assert state['defrost_delay'] is False
        assert 'ice_maker_enabled' not in state
        assert state['icemaker_one_enabled'] is True
        assert state['icemaker_two_enabled'] is True
        assert state['selfcheck_error'] == 'DA_ERROR_NONE'


class TestPantryZone:
    """Cool Select Zone pantry compartment on ARTIK051_REF_17K -- issue #20."""

    def test_href(self):
        assert fridge.PANTRY_ZONE.href == '/status/pantry/one/vs/0'

    def test_write(self):
        desc = fridge.PANTRY_ZONE.entities[0]
        path, body = desc.write_fn('FDR_WINE', {})
        assert path == ['status', 'pantry', 'one', 'vs', '0']
        assert body == {'x.com.samsung.da.mode': 'FDR_WINE'}


class TestArtik051AndTp2xFixturesHaveCompleteCoverage:
    """issue #20 (ARTIK051_REF_17K) and #26 (TP2X_REF_20K) both triggered
    the incomplete-capability-coverage repair; both must resolve to zero
    unbound hrefs now that /diagnosis/vs/0 (dishwasher.DIAGNOSIS, reused for
    fridges), /status/pantry/one/vs/0 (PANTRY_ZONE), and the OCF-native
    defrost/icemaker mirrors are covered."""

    def test_artik051_ref_17k(self):
        from custom_components.localthings.registry.adapter import flatten
        from custom_components.localthings.registry.by_type import refrigerator
        from custom_components.localthings.registry.discovery import discover
        from tests.conftest import _load_device

        resources = _load_device('refrigerator_artik051_ref_17k')
        unbound = []
        bound = discover(
            resources,
            refrigerator.REGISTRY.capabilities,
            refrigerator.REGISTRY.pattern_capabilities,
            log=unbound.append,
        )
        assert unbound == []
        state = flatten(bound, resources)
        assert state['pantry_zone_mode'] == 'FDR_DRINKS'

    def test_tp2x_ref_20k(self):
        from custom_components.localthings.registry.adapter import flatten
        from custom_components.localthings.registry.by_type import refrigerator
        from custom_components.localthings.registry.discovery import discover
        from tests.conftest import _load_device

        resources = _load_device('refrigerator_tp2x_ref_20k')
        unbound = []
        bound = discover(
            resources,
            refrigerator.REGISTRY.capabilities,
            refrigerator.REGISTRY.pattern_capabilities,
            log=unbound.append,
        )
        assert unbound == []
        state = flatten(bound, resources)
        assert state['flex_zone_mode'] == 'CV_FDR_BEVERAGE'


class TestSelfCheckError:
    """Self-check diagnostic error list -- surfaced on hardware that reports
    x.com.samsung.da.error, joined into a single display string."""

    def _desc(self):
        return next(e for e in fridge.SELF_CHECK.entities if e.key == 'selfcheck_error')

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


class TestAiEnergyLevel:
    """AI energy-saving level select on /energy/ailevel/vs/0 -- only
    exposed when the device actually offers more than one level; hardware
    that only ever reports a single supported level keeps this ignored
    (see capabilities/ignored.py)."""

    def test_href(self):
        assert fridge.AI_ENERGY_LEVEL.href == '/energy/ailevel/vs/0'

    def test_hidden_with_single_supported_level(self):
        desc = fridge.AI_ENERGY_LEVEL.entities[0]
        assert desc.exists_fn({'aiLevel': '1', 'supportedAiLevel': ['1']}, {}) is False

    def test_shown_with_multiple_supported_levels(self):
        desc = fridge.AI_ENERGY_LEVEL.entities[0]
        assert desc.exists_fn(
            {'aiLevel': '1', 'supportedAiLevel': ['1', '2']}, {}) is True

    def test_exists_for_empty_stub_rep(self):
        """An empty {} rep is /device/0's not-yet-fetched-stub carve-out --
        must be included-for-now, same as ENERGY_METER's fields."""
        desc = fridge.AI_ENERGY_LEVEL.entities[0]
        assert desc.exists_fn({}, {}) is True

    def test_hidden_when_supported_level_is_non_list_scalar(self):
        """A stray scalar (e.g. a string) must not be len()-checked as if it
        were a list -- a 5-char string would otherwise wrongly pass `> 1`."""
        desc = fridge.AI_ENERGY_LEVEL.entities[0]
        assert desc.exists_fn(
            {'aiLevel': '1', 'supportedAiLevel': '12'}, {}) is False

    def test_write(self):
        desc = fridge.AI_ENERGY_LEVEL.entities[0]
        path, body = desc.write_fn('2', {})
        assert path == ['energy', 'ailevel', 'vs', '0']
        assert body == {'aiLevel': '2'}

    def test_synthetic_fixture_has_complete_coverage(self):
        """refrigerator_device.json's supportedAiLevel was extended to two
        entries (['1', '2']) specifically to exercise this select -- the
        real captured TP1X_REF_21K_US dump only ever reports one entry, so
        this capability is synthetic-fixture-only for now."""
        from custom_components.localthings.registry.adapter import flatten
        from custom_components.localthings.registry.by_type import refrigerator
        from custom_components.localthings.registry.discovery import discover
        from tests.conftest import _load_device

        resources = _load_device('refrigerator')
        unbound = []
        bound = discover(
            resources,
            refrigerator.REGISTRY.capabilities,
            refrigerator.REGISTRY.pattern_capabilities,
            log=unbound.append,
        )
        assert unbound == []
        state = flatten(bound, resources)
        assert state['ai_energy_level'] == '1'
