from samsung_appliance.registry.capabilities import common
from samsung_appliance.registry.discovery import discover


def _reg():
    return {c.href: c for c in (
        common.KIDS_LOCK, common.REMOTE_CONTROL, common.POWER,
        common.ALARMS, common.ENERGY_METER, common.WATER_METER,
        common.WATER_FILTER,
    )}


def test_kids_lock_binary_value_fn():
    desc = common.KIDS_LOCK.entities[0]
    assert desc.value_fn('Lock') is True
    assert desc.value_fn('Ready') is False


def test_energy_clamps_negative_power(fridge_resources):
    # instantaneousPower can read negative at idle; must clamp to 0.
    pw = next(e for e in common.ENERGY_METER.entities if e.key == 'power_watts')
    assert pw.value_fn(-500.0) == 0.0
    assert pw.value_fn(93.0) == 93.0


def test_common_caps_discover_on_dishwasher(dishwasher_resources):
    bound = discover(dishwasher_resources, _reg())
    keys = {b.desc.key for b in bound}
    assert 'child_lock' in keys
    assert 'remote_control' in keys
    assert 'power_watts' in keys
