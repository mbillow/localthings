"""Tests for the AVT-WW-TP1-class air-purifier profile (model
AVT-WW-TP1-23-AXX500). Shares the air_purifier registry with the ARTIK051_TVTL
family but is detected by the 'AVT-' modelNum prefix and exposes fan speed on
/wind/strength/vs/0 and the filter on /filter/hepafilter/vs/0. The fan, pollution
light, sensing interval, sensing-mode select and do-not-disturb window write
contracts were all confirmed against a real unit."""
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities import air_purifier
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device


def _purifier():
    resources = _load_device('air_purifier_avt')
    info = resources['/information/vs/0']
    reg = for_device_by_model(
        info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'],
    )
    return reg, resources


def _state():
    reg, resources = _purifier()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_model_resolves_to_air_purifier_registry():
    """The 'AVT-' modelNum prefix routes to the air_purifier registry -- the
    device previously came back 'unknown' and fell through to the global
    CAPABILITIES fallback, raising the incomplete-coverage repair."""
    reg, _ = _purifier()
    assert reg is not None
    assert reg.name == 'air_purifier'


def test_no_unbound_hrefs():
    """Every resource in the AVT-WW-TP1-23-AXX500 dump binds or is ignored."""
    reg, resources = _purifier()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_entities_present():
    state = _state()
    for key in (
        # shared with the TVTL family / common
        'alarm_code', 'energy_kwh', 'firmware_update',
        'dust', 'fine_dust', 'super_fine_dust', 'odor', 'clean_level',
        'device_active', 'display_light',
        # AVT-board-specific
        'fan', 'mute_once', 'pollution_light',
        # filter: percent + status + raw-hour readings
        'hepa_filter_usage', 'hepa_filter_status', 'hepa_filter_usage_hours',
        'hepa_filter_life_remaining', 'hepa_filter_capacity',
        # /airlevelcheck air-quality sensing
        'air_sensing_state', 'last_air_sensing_time', 'last_air_sensing_level',
        'periodic_air_sensing', 'periodic_sensing_skip_status',
        'sensing_interval', 'sensing_mode',
        # do-not-disturb (skip-sensing window)
        'periodic_sensing_skip_status', 'sensing_skip_start', 'sensing_skip_end',
    ):
        assert key in state, key


def test_sensing_mode_select_writes_both_fields():
    """Picking a sensing mode sets the toggle + auto-action in one PUT, so
    'Only sensing' arms sense-with-no-action directly."""
    from custom_components.localthings.registry.entities import SelectDesc
    sm = next(e for e in air_purifier.AIR_LEVEL_CHECK.entities if e.key == 'sensing_mode')
    assert isinstance(sm, SelectDesc)
    assert sm.write_fn('sensing_only', {}) == (['airlevelcheck', 'vs', '0'], {
        'x.com.samsung.da.periodicSensingActivationState': 'On',
        'x.com.samsung.da.autoExeState': 'Off'})
    assert sm.write_fn('off', {}) == (['airlevelcheck', 'vs', '0'], {
        'x.com.samsung.da.periodicSensingActivationState': 'Off'})
    assert sm.write_fn('bogus', {}) is None


def test_dnd_skip_window_round_trips():
    """The skip window is one HHMMHHMM field split into two time entities; each
    write preserves the other half."""
    import datetime
    from custom_components.localthings.registry.entities import SwitchDesc, TimeDesc
    ents = {e.key: e for e in air_purifier.AIR_LEVEL_CHECK.entities}
    assert isinstance(ents['periodic_sensing_skip_status'], SwitchDesc)
    start, end = ents['sensing_skip_start'], ents['sensing_skip_end']
    assert isinstance(start, TimeDesc)
    # read: "14002100" -> 14:00 / 21:00
    assert start.value_fn('14002100') == datetime.time(14, 0)
    assert end.value_fn('14002100') == datetime.time(21, 0)
    # write start=09:30 keeps the 21:00 end half.
    rep = {'x.com.samsung.da.periodicSensingSkipTime': '14002100'}
    assert start.write_fn(datetime.time(9, 30), rep) == (
        ['airlevelcheck', 'vs', '0'], {'x.com.samsung.da.periodicSensingSkipTime': '09302100'})
    assert end.write_fn(datetime.time(23, 0), rep) == (
        ['airlevelcheck', 'vs', '0'], {'x.com.samsung.da.periodicSensingSkipTime': '14002300'})


def test_tvtl_only_entities_absent():
    """The TVTL family's /airflow + /consumable + /diagnosis entities must not
    appear -- this board exposes none of those hrefs."""
    state = _state()
    for key in ('fan_speed_level', 'fan_direction', 'filter_progress',
                'operating_mode', 'blooming_level', 'diagnosis_status'):
        assert key not in state, key


def test_no_power_switch_on_fan_boards():
    """AVT boards expose a fan that owns on/off, so the standalone power switch
    is suppressed (the TVTL family, which has no fan, keeps it)."""
    assert 'power_switch' not in _state()


def test_wind_strength_is_a_fan_entity():
    """The wind-strength control is a real HA fan (FanDesc), not a select --
    all modes (Auto/Low/Medium/High/Sleep) surface as a flat set of fan
    preset_modes (see fan.py's LocalThingsAirPurifierFan)."""
    from custom_components.localthings.registry.entities import FanDesc
    desc = air_purifier.WIND_STRENGTH.entities[0]
    assert isinstance(desc, FanDesc)
    assert desc.key == 'fan'
    assert air_purifier.WIND_STRENGTH.href == '/wind/strength/vs/0'


def test_fan_golden_state_is_current_code():
    assert _state()['fan'] == '0'


def test_fan_write_sets_scalar_mode_code():
    """A speed/preset selection writes the raw code as a SCALAR string, only for
    advertised codes (confirmed on hardware, incl. Sleep 91)."""
    _, resources = _purifier()
    rep = resources['/wind/strength/vs/0']
    write = air_purifier.WIND_STRENGTH.entities[0].write_fn
    assert write(('mode', '3'), rep) == (
        ['wind', 'strength', 'vs', '0'], {'x.com.samsung.da.modes': '3'})
    assert write(('mode', '91'), rep) == (
        ['wind', 'strength', 'vs', '0'], {'x.com.samsung.da.modes': '91'})
    assert write(('mode', '7'), rep) is None  # not advertised
    assert write(('power', True, '/power/0'), {}) == (['power', '0'], {'value': True})
    assert write(('power', False, '/power/vs/0'), {}) == (
        ['power', 'vs', '0'], {'x.com.samsung.da.power': 'Off'})


def test_filter_hour_readings():
    """Raw-hour filter readings: usage 8h, capacity 8762h, remaining 8754h."""
    state = _state()
    assert state['hepa_filter_usage_hours'] == 8
    assert state['hepa_filter_capacity'] == 8762
    assert state['hepa_filter_life_remaining'] == 8754


def test_confirmed_writable_contracts():
    """Pollution + sensing-interval write contracts verified live on hardware."""
    mode = air_purifier.MODE
    alc = air_purifier.AIR_LEVEL_CHECK

    # Pollution: one-token option_write; OptionCode_27514 stays put (confirmed).
    pol = next(e for e in mode.entities if e.key == 'pollution_light')
    assert pol.write_fn('On', {}) == (
        ['mode', 'vs', '0'], {'x.com.samsung.da.options': ['Pollution_On']})
    assert pol.write_fn('Off', {}) == (
        ['mode', 'vs', '0'], {'x.com.samsung.da.options': ['Pollution_Off']})

    # Sensing interval: entity is in minutes, device stores seconds.
    si = next(e for e in alc.entities if e.key == 'sensing_interval')
    assert si.value_fn('600') == 10  # 600 s -> 10 min
    assert si.write_fn('1', {}) == (
        ['airlevelcheck', 'vs', '0'], {'x.com.samsung.da.periodicSensingInterval': '60'})
    assert si.write_fn(10, {}) == (
        ['airlevelcheck', 'vs', '0'], {'x.com.samsung.da.periodicSensingInterval': '600'})


def test_periodic_sensing_is_writable_switch():
    """periodicSensingActivationState is a writable switch (confirmed on
    hardware) -- the master on/off for the auto air-quality response."""
    from custom_components.localthings.registry.entities import SwitchDesc
    ps = next(e for e in air_purifier.AIR_LEVEL_CHECK.entities if e.key == 'periodic_air_sensing')
    assert isinstance(ps, SwitchDesc)
    assert ps.write_fn('On', {}) == (
        ['airlevelcheck', 'vs', '0'],
        {'x.com.samsung.da.periodicSensingActivationState': 'On'})
    assert ps.write_fn('Off', {}) == (
        ['airlevelcheck', 'vs', '0'],
        {'x.com.samsung.da.periodicSensingActivationState': 'Off'})


def test_sensing_mode_folds_toggle_and_action():
    """'Sensing mode' combines the periodic-sensing toggle and the auto-action:
    on + Off = 'sensing_only' (the state the user asked to surface)."""
    sm = next(e for e in air_purifier.AIR_LEVEL_CHECK.entities if e.key == 'sensing_mode')
    on_off = {'x.com.samsung.da.periodicSensingActivationState': 'On',
              'x.com.samsung.da.autoExeState': 'Off'}
    assert sm.rep_fn(on_off) == 'sensing_only'
    assert sm.rep_fn({**on_off, 'x.com.samsung.da.autoExeState': 'Airpurify'}) == 'auto_purify'
    assert sm.rep_fn({'x.com.samsung.da.periodicSensingActivationState': 'Off'}) == 'off'


def test_no_filter_reset_button():
    """No reset control on /filter/hepafilter/vs/0 -- contract unknown.

    Five candidate writes were tried on real AVT-WW-TP1-23-AXX500 hardware and
    every one returned 2.04 Changed while leaving filterUsage alone; the module
    comment lists them. That does not prove no local reset exists, only that it
    hasn't been found. This test guards against a button reappearing on a hunch
    -- restore it when someone has a contract confirmed on hardware, not before.
    """
    from custom_components.localthings.registry.entities import ButtonDesc
    assert not [e for e in air_purifier.HEPA_FILTER.entities
                if isinstance(e, ButtonDesc)]



def test_mute_once_reuses_airconditioner_capability():
    from custom_components.localthings.registry.capabilities import airconditioner
    reg, _ = _purifier()
    assert airconditioner.MUTE_ONCE in reg.capabilities['/option/muteonce/vs/0']
