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
    flags into one list (same shape as fridge.FLEX_ZONE's `modes` field, but
    keyed `options` here and, unlike FLEX_ZONE, with no `supportedOptions`
    list to check membership against). Of the tokens seen:
      Light_On / Light_Off       -- read as a plain on/off flag; MODE_LIGHT
                                     below models it as a real switch, RMW-
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
from .common import sensor_item_value


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


AIR_QUALITY = Capability(
    href='/sensors/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='dust', field='x.com.samsung.da.items',
                   name='Dust', icon='mdi:blur',
                   value_fn=lambda items: sensor_item_value(items, 'Dust')),
        SensorDesc(key='fine_dust', field='x.com.samsung.da.items',
                   name='Fine dust', icon='mdi:blur',
                   value_fn=lambda items: sensor_item_value(items, 'FineDust')),
        SensorDesc(key='super_fine_dust', field='x.com.samsung.da.items',
                   name='Super fine dust', icon='mdi:blur',
                   value_fn=lambda items: sensor_item_value(items, 'SuperFineDust')),
        SensorDesc(key='odor', field='x.com.samsung.da.items',
                   name='Odor', icon='mdi:scent',
                   value_fn=lambda items: sensor_item_value(items, 'Odor')),
        SensorDesc(key='clean_level', field='x.com.samsung.da.items',
                   name='Clean level', icon='mdi:air-filter',
                   value_fn=lambda items: sensor_item_value(items, 'CleanLevel')),
    ),
)

# x.com.samsung.da.items here is a single-entry {name, state} pair rather than
# the {type, value} shape AIR_QUALITY reads above -- a different schema on the
# same 'items' field name. FilterProgress is a raw 0-100 percentage in both
# dumps (100 and 62); which end of that scale means "replace me" isn't
# confirmed from the dump alone, so the entity is named after the raw field
# rather than asserting a direction (see issue #56 follow-up questions).
FILTER = Capability(
    href='/consumable/vs/0',
    poll_tier='cold',
    entities=(
        SensorDesc(key='filter_progress', field='x.com.samsung.da.items',
                   name='Filter progress', unit='%', state_class='measurement',
                   icon='mdi:air-filter', entity_category='diagnostic',
                   value_fn=lambda items: _int_or_none(next(
                       (i.get('x.com.samsung.da.state') for i in (items or ())
                        if isinstance(i, dict)
                        and i.get('x.com.samsung.da.name') == 'FilterProgress'),
                       None))),
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
                   value_fn=_int_or_none),
        SensorDesc(key='fan_direction', field='x.com.samsung.da.direction',
                   name='Fan direction', icon='mdi:rotate-3d-variant',
                   entity_category='diagnostic'),
    ),
)


def _mode_options(rep):
    opts = rep.get('x.com.samsung.da.options')
    return list(opts) if isinstance(opts, (list, tuple)) else []


def _mode_token(rep, prefix):
    """Value after `prefix` from the first matching entry in the packed
    options list, or None if no entry carries that prefix."""
    for opt in _mode_options(rep):
        if isinstance(opt, str) and opt.startswith(prefix):
            return opt[len(prefix):]
    return None


def _mode_has_prefix(prefix):
    return lambda rep, resources: _mode_token(rep, prefix) is not None


def _light_write(payload, rep, href=None):
    new_token = f"Light_{'On' if payload == 'On' else 'Off'}"
    opts = [o for o in _mode_options(rep)
            if not (isinstance(o, str) and o.startswith('Light_'))]
    opts.append(new_token)
    return ['mode', 'vs', '0'], {'x.com.samsung.da.options': opts}


MODE = Capability(
    href='/mode/vs/0',
    poll_tier='warm',
    entities=(
        SwitchDesc(key='display_light', name='Display light', icon='mdi:led-on',
                   entity_category='config',
                   rep_fn=lambda rep: _mode_token(rep, 'Light_') == 'On',
                   exists_fn=_mode_has_prefix('Light_'),
                   write_fn=_light_write),
        # Read-only pending issue #56 follow-up -- see module docstring.
        SensorDesc(key='operating_mode', name='Operating mode', icon='mdi:fan',
                   entity_category='diagnostic',
                   rep_fn=lambda rep: _mode_token(rep, 'Comode_'),
                   exists_fn=_mode_has_prefix('Comode_')),
        SensorDesc(key='blooming_level', name='Blooming level', icon='mdi:flower',
                   entity_category='diagnostic',
                   rep_fn=lambda rep: _mode_token(rep, 'Blooming_'),
                   exists_fn=_mode_has_prefix('Blooming_')),
    ),
)
