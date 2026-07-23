"""Tests for the ARTIK051_TVTL_18K air-purifier profile (issue #56)."""
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities import air_purifier
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device


def _purifier():
    resources = _load_device('air_purifier')
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
    reg, _ = _purifier()
    assert reg is not None
    assert reg.name == 'air_purifier'


def test_no_unbound_hrefs():
    """Every resource in the issue #56 dump binds or is ignored -- clears the
    coverage-gap repair."""
    reg, resources = _purifier()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_entities_present():
    state = _state()
    for key in (
        'power_switch', 'alarm_code', 'dust', 'fine_dust', 'super_fine_dust',
        'odor', 'clean_level', 'filter_life', 'device_active',
        'diagnosis_status', 'fan_speed_level', 'fan_direction',
        'display_light', 'operating_mode',
    ):
        assert key in state, key


def test_air_quality_sensor_values():
    """Dust/FineDust/SuperFineDust/Odor/CleanLevel read index 0 of each
    items[] entry's value list (the raw measurement, per common.sensor_item_value)."""
    state = _state()
    assert state['dust'] == 11
    assert state['fine_dust'] == 9
    assert state['super_fine_dust'] == 5
    assert state['odor'] == 0
    assert state['clean_level'] == 0


def test_filter_life_reads_named_consumable_item():
    """FilterProgress is confirmed (issue #56) to count down as the filter
    wears -- 100 means fresh, not "100% used"."""
    assert _state()['filter_life'] == 100


def test_diagnosis_reuses_dishwasher_capability():
    """/diagnosis/vs/0 has the identical field/write contract as
    dishwasher.DIAGNOSIS, so the by_type registry reuses it directly."""
    from custom_components.localthings.registry.capabilities import dishwasher
    reg, _ = _purifier()
    assert dishwasher.DIAGNOSIS in reg.capabilities['/diagnosis/vs/0']


def test_light_switch_write_contract():
    """The display-light switch RMW-replaces only the 'Light_*' entry in the
    packed /mode/vs/0 options list (via laundry.replace_in_options), leaving
    the other flags and the list order untouched."""
    desc = next(e for e in air_purifier.MODE.entities if e.key == 'display_light')
    rep = {'x.com.samsung.da.options': [
        'Comode_Off', 'Light_On', 'OptionCode_60282',
    ]}
    assert desc.rep_fn(rep) is True
    assert desc.write_fn('Off', rep) == (
        ['mode', 'vs', '0'],
        {'x.com.samsung.da.options': [
            'Comode_Off', 'Light_Off', 'OptionCode_60282',
        ]},
    )


def test_operating_mode_is_a_read_only_diagnostic():
    """Comode_* surfaces as a raw diagnostic sensor rather than a select/
    control -- issue #56's five running-state dumps confirmed it reads 'Off'
    regardless of the device's actual fan setting, ruling out the original
    guess that it was the fan-speed selector; its real purpose is still
    unconfirmed (see the air_purifier.py module docstring)."""
    operating_mode = next(e for e in air_purifier.MODE.entities if e.key == 'operating_mode')
    rep = {'x.com.samsung.da.options': ['Comode_Off']}
    assert operating_mode.rep_fn(rep) == 'Off'
    assert not hasattr(operating_mode, 'write_fn')


def test_blooming_not_modeled():
    """Confirmed (issue #56) to have no corresponding SmartThings app
    setting -- dropped entirely rather than kept as an unexplained
    diagnostic."""
    assert not any(e.key == 'blooming_level' for e in air_purifier.MODE.entities)


def test_airflow_vs_fallback_only_binds_without_generic():
    """/airflow/vs/0 is a match_fn fallback -- it must not bind when the
    OCF-standard /airflow/0 is also present (both are on every dump seen)."""
    assert air_purifier.AIRFLOW_VS_FALLBACK.match_fn(
        {}, {'/airflow/0': {'speed': 0, 'direction': 'Off'}},
    ) is False
    assert air_purifier.AIRFLOW_VS_FALLBACK.match_fn({}, {}) is True
