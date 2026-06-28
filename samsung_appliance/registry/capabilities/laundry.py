"""Capabilities specific to the laundry-family appliances (dishwasher, dryer).

Resources verified against the DW9000F-class dump at 10.0.0.129.
Note: door-LED keys use NO `x.com.samsung.da.` prefix — `setBrightness` and
`setNightLight` — preserved exactly as they appear in the OCF resource rep.
"""
from ..capability import Capability
from ..entities import SelectDesc, SwitchDesc

_LED_LEVELS = ('Low', 'High')
_SOUND_MODES = ('voice', 'tone', 'mute')


def _led_brightness_write(p, rep):
    if p not in _LED_LEVELS:
        return None
    return ['doorled', 'light', 'vs', '0'], {'setBrightness': p}


def _led_night_write(p, rep):
    if p not in ('On', 'Off'):
        return None
    return ['doorled', 'light', 'vs', '0'], {'setNightLight': p}


def _sound_mode_write(p, rep):
    if p not in _SOUND_MODES:
        return None
    return ['settings', 'sound', 'mode', 'vs', '0'], {'mode': p}


DOOR_LED = Capability(
    href='/doorled/light/vs/0',
    entities=(
        SelectDesc(key='led_brightness', field='setBrightness',
                   name='Door LED brightness', icon='mdi:brightness-6',
                   options=_LED_LEVELS, write_fn=_led_brightness_write),
        SwitchDesc(key='led_night_light', field='setNightLight',
                   name='Door LED night light', icon='mdi:weather-night',
                   value_fn=lambda v: v == 'On',
                   write_fn=_led_night_write),
    ),
)

SOUND_MODE = Capability(
    href='/settings/sound/mode/vs/0',
    entities=(
        SelectDesc(key='sound_mode', field='mode',
                   name='Sound mode', icon='mdi:volume-high',
                   options=_SOUND_MODES, write_fn=_sound_mode_write),
    ),
)
