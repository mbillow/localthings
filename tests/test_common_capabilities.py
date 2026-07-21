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
