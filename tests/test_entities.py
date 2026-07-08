from custom_components.localthings.registry.entities import (
    SensorDesc, BinarySensorDesc, SelectDesc, SwitchDesc, ButtonDesc,
    NumberDesc, PLATFORM_OF,
)


def test_value_fn_defaults_to_identity():
    d = SensorDesc(key='power', field='x.com.samsung.da.instantaneousPower')
    assert d.value_fn(42) == 42


def test_descriptions_are_frozen():
    d = SensorDesc(key='power', field='f')
    try:
        d.key = 'other'
    except Exception as e:
        assert 'frozen' in str(type(e)).lower() or 'cannot' in str(e).lower()
    else:
        raise AssertionError("expected frozen dataclass")


def test_platform_mapping_covers_all_subclasses():
    assert PLATFORM_OF[SensorDesc] == 'sensor'
    assert PLATFORM_OF[BinarySensorDesc] == 'binary_sensor'
    assert PLATFORM_OF[SelectDesc] == 'select'
    assert PLATFORM_OF[SwitchDesc] == 'switch'
    assert PLATFORM_OF[ButtonDesc] == 'button'
    assert PLATFORM_OF[NumberDesc] == 'number'


def test_select_carries_options_and_write_fn():
    d = SelectDesc(key='sound', field='mode', options=('voice', 'tone', 'mute'),
                   write_fn=lambda p, rep: (['settings', 'sound', 'mode', 'vs', '0'], {'mode': p}))
    assert d.write_fn('tone', {}) == (['settings', 'sound', 'mode', 'vs', '0'], {'mode': 'tone'})
