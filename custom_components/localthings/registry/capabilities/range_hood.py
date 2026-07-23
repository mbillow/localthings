"""Capabilities for Samsung AHD-WW-TP1-22 range hoods.

The verified device exposes its fan, two-level work lamp, washable-filter
status, and particulate sensors as distinct local OCF resources.  Fan power
and speed are combined into one HA fan entity by ``fan.py``; lamp power and
brightness remain separate controls because the device advertises them as two
independent fields.
"""

from datetime import datetime, timezone

from ..capability import Capability
from ..entities import (
    BinarySensorDesc,
    FanDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
)
from .common import int_or_none, sensor_item_value


def _timestamp(value):
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _active_alarm_codes(items):
    """Discard the hood firmware's retained/deleted ``ErrorCode_OFF`` row.

    Unlike ``common._alarm_codes``, the hood retains a deleted alarm row in
    its live representation, so this family-specific helper also checks state.
    """
    codes = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        if str(item.get('x.com.samsung.da.state', '')).lower() == 'deleted':
            continue
        code = item.get('x.com.samsung.da.code')
        if code and str(code).lower() != 'errorcode_off':
            codes.append(code)
    return ', '.join(codes) if codes else 'none'


HOOD_ALARMS = Capability(
    href='/alarms/vs/0',
    poll_tier='hot',
    entities=(
        SensorDesc(
            key='alarm_code',
            field='x.com.samsung.da.items',
            name='Alarm code',
            icon='mdi:alert',
            entity_category='diagnostic',
            value_fn=_active_alarm_codes,
        ),
    ),
)


def _hood_fan_write(payload, rep, href=None):
    kind, value, *args = payload
    if kind == 'power':
        power_href = args[0] if args else '/power/0'
        if power_href == '/power/0':
            return ['power', '0'], {'value': bool(value)}
        if power_href == '/power/vs/0':
            return ['power', 'vs', '0'], {
                'x.com.samsung.da.power': 'On' if value else 'Off',
            }
        return None
    if kind == 'speed':
        value = str(value)
        supported = [
            str(code)
            for code in rep.get('x.com.samsung.da.hood.supportedFanSpeed', ())
        ]
        if value not in supported:
            return None
        return ['hood', 'fanspeed', 'vs', '0'], {
            'x.com.samsung.da.hood.fanSpeed': value,
        }
    return None


HOOD_FAN = Capability(
    href='/hood/fanspeed/vs/0',
    poll_tier='hot',
    entities=(
        FanDesc(
            key='fan',
            field='x.com.samsung.da.hood.fanSpeed',
            name=None,
            write_fn=_hood_fan_write,
        ),
        BinarySensorDesc(
            key='automatic_operation',
            field='x.com.samsung.da.hood.autoOperation',
            name='Automatic operation',
            icon='mdi:fan-auto',
            entity_category='diagnostic',
            value_fn=lambda value: str(value).lower() == 'on',
        ),
    ),
)


def _lamp_level_write(value, rep, href=None):
    code = str(value)
    supported = [str(level) for level in rep.get('x.com.samsung.lamp.range', ())]
    if code not in supported:
        return None
    return ['hood', 'lamp', 'vs', '0'], {
        'x.com.samsung.lamp.current': code,
    }


HOOD_LAMP = Capability(
    href='/hood/lamp/vs/0',
    poll_tier='hot',
    entities=(
        SwitchDesc(
            key='lamp',
            field='x.com.samsung.lamp.power',
            name='Lamp',
            icon='mdi:range-hood',
            value_fn=lambda value: str(value).lower() == 'on',
            write_fn=lambda payload, rep, href=None: (
                ['hood', 'lamp', 'vs', '0'],
                {'x.com.samsung.lamp.power': 'On' if payload == 'On' else 'Off'},
            ),
        ),
        SelectDesc(
            key='lamp_brightness',
            field='x.com.samsung.lamp.current',
            name='Lamp brightness',
            icon='mdi:brightness-6',
            translation_key='range_hood_lamp_brightness',
            options_field='x.com.samsung.lamp.range',
            write_fn=_lamp_level_write,
        ),
    ),
)


HOOD_FILTER = Capability(
    href='/filter/hoodfilter/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(
            key='hood_filter_usage',
            field='x.com.samsung.da.filterUsage',
            name='Filter usage',
            unit='%',
            state_class='measurement',
            icon='mdi:air-filter',
            entity_category='diagnostic',
            value_fn=int_or_none,
        ),
        SensorDesc(
            key='hood_filter_status',
            field='x.com.samsung.da.filterStatus',
            name='Filter status',
            icon='mdi:air-filter',
            entity_category='diagnostic',
        ),
        SensorDesc(
            key='hood_filter_capacity',
            field='x.com.samsung.da.filterCapacity',
            name='Filter capacity',
            unit='h',
            icon='mdi:timer-outline',
            entity_category='diagnostic',
            enabled_default=False,
            value_fn=int_or_none,
        ),
    ),
)


AIR_QUALITY = Capability(
    href='/sensors/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(
            key='clean_level',
            field='x.com.samsung.da.items',
            name='Clean level',
            icon='mdi:air-filter',
            value_fn=lambda items: sensor_item_value(items, 'CleanLevel'),
        ),
        SensorDesc(
            key='dust',
            field='x.com.samsung.da.items',
            name='Dust',
            value_fn=lambda items: sensor_item_value(items, 'Dust'),
        ),
        SensorDesc(
            key='fine_dust',
            field='x.com.samsung.da.items',
            name='Fine dust',
            value_fn=lambda items: sensor_item_value(items, 'FineDust'),
        ),
        SensorDesc(
            key='super_fine_dust',
            field='x.com.samsung.da.items',
            name='Super fine dust',
            value_fn=lambda items: sensor_item_value(items, 'SuperFineDust'),
        ),
    ),
)


AIR_LEVEL_CHECK = Capability(
    href='/airlevelcheck/vs/0',
    poll_tier='warm',
    entities=(
        BinarySensorDesc(
            key='periodic_air_sensing',
            field='x.com.samsung.da.periodicSensingActivationState',
            name='Periodic air sensing',
            icon='mdi:radar',
            entity_category='diagnostic',
            value_fn=lambda value: str(value).lower() == 'on',
        ),
        SensorDesc(
            key='air_sensing_state',
            field='x.com.samsung.da.sensingState',
            name='Air sensing state',
            icon='mdi:radar',
            entity_category='diagnostic',
        ),
        SensorDesc(
            key='last_air_sensing_time',
            field='x.com.samsung.da.lastSensingTime',
            name='Last air sensing time',
            device_class='timestamp',
            entity_category='diagnostic',
            value_fn=_timestamp,
        ),
        SensorDesc(
            key='last_air_sensing_level',
            field='x.com.samsung.da.lastSensingLevel',
            name='Last air sensing level',
            icon='mdi:air-filter',
            entity_category='diagnostic',
        ),
        SensorDesc(
            key='automatic_ventilation_state',
            field='x.com.samsung.da.autoExeState',
            name='Automatic ventilation state',
            icon='mdi:fan-auto',
            entity_category='diagnostic',
        ),
    ),
)


AUTO_VENTILATION = Capability(
    href='/autoventilation/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(
            key='auto_ventilation_action',
            field='action',
            name='Auto ventilation action',
            icon='mdi:fan-auto',
        ),
    ),
)


# Resource plumbing and opaque feature-negotiation fields that are specific to
# this family.  Bare capabilities mark them covered without creating entities.
COVERAGE = [
    Capability(href=href)
    for href in (
        '/power/0',
        '/power/vs/0',
        '/mode/vs/0',
        '/personality/presence/vs/0',
        '/availablecontrolsets/vs/0',
        '/da/softreset/vs/0',
    )
]
