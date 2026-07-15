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
