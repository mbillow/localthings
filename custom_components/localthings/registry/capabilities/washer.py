"""Capabilities specific to washer appliances (Samsung DA_WM_TP1-class
front-load washers).

Resources verified against two live WW90DG6U25LEU4 dumps (Table_02 course
family). Washers never report `oneUiVersion` -- see
`registry/by_type/__init__.py`'s `for_device_by_model()` for the fallback
detection this device type requires.
"""
from ..capability import Capability
from ..entities import BinarySensorDesc, SelectDesc, SensorDesc, SwitchDesc

# ---------------------------------------------------------------------------
# /washer/vs/0 -- wash temperature, spin speed, rinse cycle count
#
# Despite the shared href, this is unrelated to dryer.DRYER_SETTINGS (also
# bound to '/washer/vs/0') -- an artifact of Samsung reusing the same OCF
# path for different device families. Only one of the two ever binds for a
# given device, since dryer and washer are separate by_type registries.
# ---------------------------------------------------------------------------

WASHER_SETTINGS = Capability(
    href='/washer/vs/0',
    entities=(
        SelectDesc(key='wash_temperature', field='x.com.samsung.da.waterTemperature',
                   name='Wash temperature', icon='mdi:thermometer-water',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedWaterTemperature',
                   write_fn=lambda p, rep, href=None: (
                       ['washer', 'vs', '0'], {'x.com.samsung.da.waterTemperature': p})),
        SelectDesc(key='spin_speed', field='x.com.samsung.da.spinLevel',
                   name='Spin speed', icon='mdi:sync',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedSpinLevel',
                   write_fn=lambda p, rep, href=None: (
                       ['washer', 'vs', '0'], {'x.com.samsung.da.spinLevel': p})),
        SelectDesc(key='rinse_cycles', field='x.com.samsung.da.rinseCycles',
                   name='Rinse cycles', icon='mdi:water-sync',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedRinseCycles',
                   write_fn=lambda p, rep, href=None: (
                       ['washer', 'vs', '0'], {'x.com.samsung.da.rinseCycles': p})),
    ),
)

# ---------------------------------------------------------------------------
# /course/vs/0 -- selected course, read-only.
#
# Unlike dishwasher.CYCLE_OPTIONS, we have no verified Course_XX -> name
# table for washer hardware (only two codes observed: 1C, 65) and no
# supported-course list field to validate a write against. Exposing this
# as a plain sensor of the raw hex code is honest about what we actually
# know; add write support once a real course-name table exists.
# ---------------------------------------------------------------------------

def _option_value(options, prefix):
    for o in (options or []):
        if isinstance(o, str) and o.startswith(prefix + '_'):
            return o.split('_', 1)[1]
    return None


WASHER_COURSE = Capability(
    href='/course/vs/0',
    entities=(
        SensorDesc(key='cycle', name='Cycle', icon='mdi:washing-machine',
                   rep_fn=lambda rep: _option_value(
                       rep.get('x.com.samsung.da.options'), 'Course')),
    ),
)

# ---------------------------------------------------------------------------
# /buzzersound/vs/0 -- buzzer volume and (on some units) a separate finish
# chime. Fields have no 'x.com.samsung.da.' prefix in this resource, unlike
# most other washer hrefs.
# ---------------------------------------------------------------------------

BUZZER_SOUND = Capability(
    href='/buzzersound/vs/0',
    entities=(
        SelectDesc(key='buzzer_sound', field='setBuzzerSound',
                   name='Buzzer sound', icon='mdi:volume-high',
                   entity_category='config',
                   options_field='supportedBuzzerSound',
                   write_fn=lambda p, rep, href=None: (
                       ['buzzersound', 'vs', '0'], {'setBuzzerSound': p})),
        SelectDesc(key='finish_sound', field='setFinishSound',
                   name='Finish sound', icon='mdi:bell-ring',
                   entity_category='config',
                   exists_fn=lambda rep: 'supportedFinishSound' in rep,
                   options_field='supportedFinishSound',
                   write_fn=lambda p, rep, href=None: (
                       ['buzzersound', 'vs', '0'], {'setFinishSound': p})),
    ),
)

# ---------------------------------------------------------------------------
# /wm/jobbeginingstatus/vs/0 -- same href as dryer.JOB_BEGINNING_STATUS, but
# a different field name (currentStatus, not jobBeginingStatus).
# ---------------------------------------------------------------------------

WASHER_JOB_BEGINNING_STATUS = Capability(
    href='/wm/jobbeginingstatus/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='job_beginning_status',
                   field='x.com.samsung.da.currentStatus',
                   name='Job beginning status',
                   entity_category='diagnostic'),
    ),
)

# ---------------------------------------------------------------------------
# OCF-native / '-vs' fallback pairs for power, kids-lock, remote control.
#
# Same shape as fridge.py's "Aggregate-resource fallbacks": the generic OCF
# href (/power/0, plain boolean 'value') is preferred when present; the
# vendor '-vs' href (richer historically, but for these three controls just
# a string-encoded duplicate) only binds when the generic href is absent
# from this device's resource set, via match_fn. Scoped to washer.py, not
# common.py -- no other device type has been confirmed to expose the
# generic hrefs, so this must not change behavior for dishwasher/dryer/
# oven/refrigerator.
# ---------------------------------------------------------------------------

POWER_GENERIC = Capability(
    href='/power/0',
    entities=(
        SwitchDesc(key='power_switch', field='value',
                   name='Power',
                   value_fn=lambda v: bool(v),
                   write_fn=lambda p, rep, href=None: (
                       ['power', '0'], {'value': p == 'On'})),
    ),
)

POWER_VS_FALLBACK = Capability(
    href='/power/vs/0',
    match_fn=lambda rep, resources: '/power/0' not in resources,
    entities=(
        SwitchDesc(key='power_switch', field='x.com.samsung.da.power',
                   name='Power',
                   value_fn=lambda v: v == 'On',
                   write_fn=lambda p, rep, href=None: (
                       ['power', 'vs', '0'],
                       {'x.com.samsung.da.power': 'On' if p == 'On' else 'Off'})),
    ),
)

KIDS_LOCK_GENERIC = Capability(
    href='/kidslock/0',
    entities=(
        SwitchDesc(key='child_lock', field='value',
                   name='Child lock', device_class='lock',
                   value_fn=lambda v: bool(v),
                   write_fn=lambda p, rep, href=None: (
                       ['kidslock', '0'], {'value': p == 'On'})),
    ),
)

KIDS_LOCK_VS_FALLBACK = Capability(
    href='/kidslock/vs/0',
    match_fn=lambda rep, resources: '/kidslock/0' not in resources,
    entities=(
        SwitchDesc(key='child_lock', field='x.com.samsung.da.kidsLock',
                   name='Child lock', device_class='lock',
                   value_fn=lambda v: v != 'Ready',
                   write_fn=lambda p, rep, href=None: (
                       ['kidslock', 'vs', '0'],
                       {'x.com.samsung.da.kidsLock': 'Enable' if p == 'On' else 'Ready'})),
    ),
)

REMOTE_CONTROL_GENERIC = Capability(
    href='/remotectrl/0',
    entities=(
        BinarySensorDesc(key='remote_control', field='value',
                         name='Smart Control', device_class='connectivity',
                         value_fn=lambda v: bool(v)),
    ),
)

REMOTE_CONTROL_VS_FALLBACK = Capability(
    href='/remotectrl/vs/0',
    match_fn=lambda rep, resources: '/remotectrl/0' not in resources,
    entities=(
        BinarySensorDesc(key='remote_control',
                         field='x.com.samsung.da.remoteControlEnabled',
                         name='Smart Control', device_class='connectivity',
                         value_fn=lambda v: str(v).lower() == 'true'),
    ),
)
