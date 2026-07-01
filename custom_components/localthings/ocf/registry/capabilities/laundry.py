"""Capabilities specific to the laundry-family appliances (dishwasher, dryer).

Resources verified against the DW9000F-class dump at 10.0.0.129.
Note: door-LED keys use NO `x.com.samsung.da.` prefix — `setBrightness` and
`setNightLight` — preserved exactly as they appear in the OCF resource rep.
"""
from datetime import time as dt_time

from ..capability import Capability
from ..entities import NumberDesc, SelectDesc, SwitchDesc, TimeDesc

_LED_LEVELS = ('Low', 'High')
_SOUND_MODES = ('voice', 'tone', 'mute')


def _led_brightness_write(p, rep, href=None):
    if p not in _LED_LEVELS:
        return None
    return ['doorled', 'light', 'vs', '0'], {'setBrightness': p}


def _led_night_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    return ['doorled', 'light', 'vs', '0'], {'setNightLight': p}


def _parse_hm(v):
    if not v:
        return None
    try:
        h, m = v.split(':')
        return dt_time(int(h), int(m))
    except Exception:
        return None


def _sound_mode_write(p, rep, href=None):
    if p not in _SOUND_MODES:
        return None
    return ['settings', 'sound', 'mode', 'vs', '0'], {'mode': p}


DOOR_LED = Capability(
    href='/doorled/light/vs/0',
    entities=(
        SelectDesc(key='led_brightness', field='setBrightness',
                   name='Door LED brightness', icon='mdi:brightness-6',
                   entity_category='config',
                   options=_LED_LEVELS, write_fn=_led_brightness_write),
        SwitchDesc(key='led_night_light', field='setNightLight',
                   name='Door LED night light', icon='mdi:weather-night',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=_led_night_write),
        SelectDesc(key='led_night_brightness', field='setNightLightBrightness',
                   name='Door LED night brightness', icon='mdi:brightness-4',
                   entity_category='config',
                   options=_LED_LEVELS,
                   write_fn=lambda p, rep, href=None: (
                       ['doorled', 'light', 'vs', '0'],
                       {'setNightLightBrightness': p})),
        TimeDesc(key='led_night_start', field='setNightLightTimeStart',
                 name='Door LED night start', icon='mdi:clock-start',
                 entity_category='config',
                 value_fn=_parse_hm,
                 write_fn=lambda p, rep, href=None: (
                     ['doorled', 'light', 'vs', '0'],
                     {'setNightLightTimeStart': f'{p.hour:02d}:{p.minute:02d}'})),
        TimeDesc(key='led_night_end', field='setNightLightTimeEnd',
                 name='Door LED night end', icon='mdi:clock-end',
                 entity_category='config',
                 value_fn=_parse_hm,
                 write_fn=lambda p, rep, href=None: (
                     ['doorled', 'light', 'vs', '0'],
                     {'setNightLightTimeEnd': f'{p.hour:02d}:{p.minute:02d}'})),
    ),
)

SOUND_MODE = Capability(
    href='/settings/sound/mode/vs/0',
    entities=(
        SelectDesc(key='sound_mode', field='mode',
                   name='Sound mode', icon='mdi:volume-high',
                   entity_category='config',
                   options=_SOUND_MODES, write_fn=_sound_mode_write),
    ),
)

SOUND_VOLUME = Capability(
    href='/settings/sound/volume/vs/0',
    entities=(
        NumberDesc(key='sound_volume', field='level',
                   name='Sound volume', icon='mdi:volume-medium',
                   entity_category='config',
                   native_min=0, native_max=15, step=5,
                   value_fn=lambda v: int(v) if v is not None else None,
                   write_fn=lambda p, rep, href=None: (
                       ['settings', 'sound', 'volume', 'vs', '0'],
                       {'level': str(int(p))})),
    ),
)
