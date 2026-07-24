"""Capabilities for the Samsung ARTIK051_TVTL-class air purifier family
(model AX60R5080WD/SE, issue #56).

Power, kids-lock, remote-control, alarms, and the energy meter are the shared
common.py capabilities (this family exposes the standard /power/0+/power/vs/0
pair and /alarms/vs/0, /energy/consumption/vs/0). /diagnosis/vs/0 reuses
dishwasher.DIAGNOSIS -- identical field/write contract
(x.com.samsung.da.diagnosisStart, 'Ready' on both dumps).

/mode/vs/0's x.com.samsung.da.options array packs multiple independent
'<Prefix>_<value>' flags into one list -- the same packed-list/RMW contract
laundry.py's option_value/replace_in_options already model for
/course/vs/0's options[] (reused directly below, just against this family's
own href). Per issue #56's follow-up (five diagnostics dumps captured with
the physical unit set to Auto/Sleep/Low/Medium/High):
  Light_On / Light_Off  -- a plain on/off flag; MODE below models it as a
                            real switch, RMW-replacing just that one entry.
  Comode_Off            -- read 'Off' on *every* one of the five dumps,
                            including High/Low/Medium/Auto -- confirms this
                            is NOT the fan-speed selector (ruling out the
                            original guess); exposed read-only since its
                            actual purpose is still unconfirmed.
  OptionCode_60282       -- confirmed opaque/not user-facing in the
                            SmartThings app; not modeled (same treatment as
                            range_hood's OptionCode_* token on the same
                            href).
  Blooming_*             -- confirmed to have no corresponding SmartThings
                            app setting; dropped entirely rather than kept
                            as an unexplained diagnostic (it did track 1:1
                            with Sleep mode across the five dumps -- 0 in
                            Sleep, 6 otherwise -- so it's plausibly an
                            automatic side effect of sleep mode, e.g. a
                            display-dimming level, but that's still a guess).

/airflow/0 and /airflow/vs/0's `speed` still isn't modeled as a real
fan-speed control: across the same five dumps it read 0 for both Auto *and*
High, and 3 for Low/Medium *and* Sleep -- not a monotonic mapping to any
selectable level, and the dumps were all captured within about three minutes
of each other (only one poll cycle apart at this integration's 30s summary
interval), so the values may not have settled after each change before the
diagnostics snapshot was taken. Exposed read-only pending a confirmed,
stable capture -- see the issue #56 discussion for what's needed.
"""
from ..capability import Capability
from ..entities import BinarySensorDesc, SensorDesc, SwitchDesc
from .common import int_or_none, sensor_item_value
from .laundry import bool_option_exists, bool_option_value, option_value, replace_in_options

_AIR_QUALITY_SENSORS = (
    ('dust', 'Dust', 'mdi:blur', 'Dust'),
    ('fine_dust', 'Fine dust', 'mdi:blur', 'FineDust'),
    ('super_fine_dust', 'Super fine dust', 'mdi:blur', 'SuperFineDust'),
    ('odor', 'Odor', 'mdi:scent', 'Odor'),
    ('clean_level', 'Clean level', 'mdi:air-filter', 'CleanLevel'),
)

AIR_QUALITY = Capability(
    href='/sensors/vs/0',
    poll_tier='warm',
    entities=tuple(
        SensorDesc(key=key, field='x.com.samsung.da.items', name=name, icon=icon,
                   value_fn=lambda items, t=sensor_type: sensor_item_value(items, t))
        for key, name, icon, sensor_type in _AIR_QUALITY_SENSORS
    ),
)


def _consumable_state(items, name):
    """Read a `/consumable/vs/0`-style items[] entry -- {name, state} pairs,
    unlike AIR_QUALITY's {type, value} shape above."""
    for item in items or ():
        if isinstance(item, dict) and item.get('x.com.samsung.da.name') == name:
            return item.get('x.com.samsung.da.state')
    return None


# FilterProgress is a 0-100 percentage counting up as the filter wears --
# confirmed via issue #56: the SmartThings app shows "Filter needs changing"
# once this reaches 100, so 100 means fully used, not "brand new." Named
# after the raw field (matching the AC/range_hood filterUsage convention,
# which counts the same direction) rather than "filter life," which would
# imply the opposite direction.
FILTER = Capability(
    href='/consumable/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='filter_progress', field='x.com.samsung.da.items',
                   name='Filter progress', unit='%', state_class='measurement',
                   icon='mdi:air-filter', entity_category='diagnostic',
                   value_fn=lambda items: int_or_none(
                       _consumable_state(items, 'FilterProgress'))),
    ),
)

DEVICE_ACTIVE = Capability(
    href='/devicespecificinfo/vs/0',
    poll_tier='cold',
    entities=(
        BinarySensorDesc(key='device_active', field='x.com.samsung.da.deviceActive',
                          name='Device active', icon='mdi:check-network-outline',
                          entity_category='diagnostic',
                          value_fn=lambda v: bool(v)),
    ),
)

# OCF-native / vendor pair for fan speed+direction -- see module docstring for
# why these are read-only for now.
AIRFLOW_GENERIC = Capability(
    href='/airflow/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='fan_speed_level', field='speed',
                   name='Fan speed level', icon='mdi:fan',
                   state_class='measurement', entity_category='diagnostic'),
        SensorDesc(key='fan_direction', field='direction',
                   name='Fan direction', icon='mdi:rotate-3d-variant',
                   entity_category='diagnostic'),
    ),
)

AIRFLOW_VS_FALLBACK = Capability(
    href='/airflow/vs/0',
    match_fn=lambda rep, resources: '/airflow/0' not in resources,
    poll_tier='warm',
    entities=(
        SensorDesc(key='fan_speed_level', field='x.com.samsung.da.speedLevel',
                   name='Fan speed level', icon='mdi:fan',
                   state_class='measurement', entity_category='diagnostic',
                   value_fn=int_or_none),
        SensorDesc(key='fan_direction', field='x.com.samsung.da.direction',
                   name='Fan direction', icon='mdi:rotate-3d-variant',
                   entity_category='diagnostic'),
    ),
)


def _light_write(payload, rep, href=None):
    opts = list(rep.get('x.com.samsung.da.options') or [])
    return ['mode', 'vs', '0'], {
        'x.com.samsung.da.options': replace_in_options(opts, 'Light', payload),
    }


MODE = Capability(
    href='/mode/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='display_light', name='Display light', icon='mdi:led-on',
                   entity_category='config',
                   rep_fn=bool_option_value('Light'),
                   exists_fn=bool_option_exists('Light'),
                   write_fn=_light_write),
        # Read-only -- confirmed NOT the fan-speed selector (see module
        # docstring), actual purpose still unconfirmed.
        SensorDesc(key='operating_mode', name='Operating mode', icon='mdi:fan',
                   entity_category='diagnostic',
                   rep_fn=lambda rep: option_value(rep.get('x.com.samsung.da.options'), 'Comode'),
                   exists_fn=bool_option_exists('Comode')),
    ),
)

# /humidity/0 and /humidity/vs/0 are empty {} on both dumps this family has
# been verified against -- covered here (not globally, per ignored.py's
# module docstring) since those hrefs collide with fridge/AC schemas
# elsewhere. Same two hrefs and reasoning as airconditioner.py's _AC_IGNORED.
COVERAGE = [
    Capability(href='/humidity/0'),
    Capability(href='/humidity/vs/0'),
]
