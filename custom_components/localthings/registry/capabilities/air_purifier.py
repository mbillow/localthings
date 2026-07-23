"""Capabilities for the Samsung ARTIK051_TVTL-class air purifier family
(model AX60R5080WD/SE, issue #56 -- verified against two independent
diagnostics dumps of the same internal model).

Power, kids-lock, remote-control, alarms, and the energy meter are the shared
common.py capabilities (this family exposes the standard /power/0+/power/vs/0
pair and /alarms/vs/0, /energy/consumption/vs/0). /diagnosis/vs/0 reuses
dishwasher.DIAGNOSIS -- identical field/write contract
(x.com.samsung.da.diagnosisStart, 'Ready' on both dumps).

Two things are deliberately left as raw, unwritable diagnostic sensors rather
than modeled as real controls, per the "don't guess" rule:

  /airflow/0, /airflow/vs/0 -- OCF-standard + vendor pair for fan speed/
    direction, both zeroed/'Off' on every dump seen (device was off in both).
    No supportedSpeed/supportedModes list is present anywhere in either dump
    to confirm the valid range, so a write-capable fan/select isn't safe to
    build yet -- see the issue #56 request for a running-state dump.

  /mode/vs/0's x.com.samsung.da.options array packs multiple independent
    '<Prefix>_<value>' flags into one list -- the same packed-list/RMW
    contract laundry.py's option_value/replace_in_options already model for
    /course/vs/0's options[] (reused directly below, just against this
    family's own href). Of the tokens seen:
      Light_On / Light_Off       -- read as a plain on/off flag; MODE below
                                     models it as a real switch, RMW-
                                     replacing just that one list entry.
      Comode_Off                 -- never seen non-'Off' on these dumps;
                                     likely the fan operating mode the issue
                                     describes (Auto/Sleep/1/2/3), but
                                     unconfirmed -- exposed read-only.
      Blooming_0 / Blooming_6     -- meaning unconfirmed; exposed read-only.
      OptionCode_60282            -- opaque, unchanged across both dumps;
                                     not modeled (same treatment as
                                     range_hood's OptionCode_* token on the
                                     same href).
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


# FilterProgress is a raw 0-100 percentage in both dumps (100 and 62); which
# end of that scale means "replace me" isn't confirmed from the dump alone,
# so the entity is named after the raw field rather than asserting a
# direction (see issue #56 follow-up questions).
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
        # Read-only pending issue #56 follow-up -- see module docstring.
        SensorDesc(key='operating_mode', name='Operating mode', icon='mdi:fan',
                   entity_category='diagnostic',
                   rep_fn=lambda rep: option_value(rep.get('x.com.samsung.da.options'), 'Comode'),
                   exists_fn=bool_option_exists('Comode')),
        SensorDesc(key='blooming_level', name='Blooming level', icon='mdi:flower',
                   entity_category='diagnostic',
                   rep_fn=lambda rep: option_value(rep.get('x.com.samsung.da.options'), 'Blooming'),
                   exists_fn=bool_option_exists('Blooming')),
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
