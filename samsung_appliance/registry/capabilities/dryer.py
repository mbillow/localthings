"""Capabilities specific to the dryer family (Samsung DV5000T-class).

Resources derived from the old dryer.py descriptor OBSERVE_PATHS and
flatten() implementation:
  /washer/vs/0              -> DRYER_SETTINGS (dryLevel, dryTime, dryerType, wrinklePrevent)
  /st/dryercourse/vs/0      -> DRYER_COURSE (dryer_mode SelectDesc)
  /wm/jobbeginingstatus/vs/0 -> JOB_BEGINNING_STATUS
  /diagnosis/vs/0           -> DRYER_DIAGNOSIS

Course table captured 2026-05-29 on a DA_WM_TP2_20_COMMON_DV5000T. Other
dryer models may use a different course table; options=() means HA renders
whatever the device reports, and the write_fn validates against this table.
"""
from ..capability import Capability
from ..entities import SelectDesc, SensorDesc, SwitchDesc

# Course table: hex codes -> human names (Table_03, DV5000T-class).
_COURSE_NAMES = {
    0x16: 'Cotton',
    0x18: 'Synthetics',
    0x19: 'Delicates',
    0x1A: 'Wool',
    0x1B: 'Bedding',
    0x1C: 'Shirts',
    0x1D: 'Towels',
    0x1E: 'Outdoor',
    0x1F: 'Mixed Load',
    0x20: 'Iron Dry',
    0x23: 'Quick Dry 35',
    0x24: 'Cool Air',
    0x25: 'Warm Air',
    0x27: 'Time Dry',
}
_COURSE_CODE_BY_NAME = {name: code for code, name in _COURSE_NAMES.items()}


def _wrinkle_write(p, rep):
    if p not in ('On', 'Off'):
        return None
    return ['washer', 'vs', '0'], {'x.com.samsung.da.wrinklePrevent': p}


def _course_write(p, rep):
    """Encode a human course name to the Samsung hex-encoded course string."""
    code = _COURSE_CODE_BY_NAME.get(p)
    if code is None:
        return None
    return ['st', 'dryercourse', 'vs', '0'], {
        'x.com.samsung.da.st.dryerMode': f'Course_{code:02X}',
    }


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

DRYER_SETTINGS = Capability(
    href='/washer/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='dry_level', field='x.com.samsung.da.dryLevel',
                   name='Dry level', icon='mdi:water-percent'),
        SensorDesc(key='dry_time', field='x.com.samsung.da.dryTime',
                   name='Dry time', icon='mdi:timer'),
        SensorDesc(key='dryer_type', field='x.com.samsung.da.dryerType',
                   name='Dryer type', icon='mdi:tumble-dryer'),
        SwitchDesc(key='wrinkle_prevent', field='x.com.samsung.da.wrinklePrevent',
                   name='Wrinkle prevent', icon='mdi:iron',
                   value_fn=lambda v: v == 'On',
                   write_fn=_wrinkle_write),
    ),
)

DRYER_COURSE = Capability(
    href='/st/dryercourse/vs/0',
    poll_tier='warm',
    entities=(
        SelectDesc(key='dryer_mode', field='x.com.samsung.da.st.dryerMode',
                   name='Dryer mode', icon='mdi:tumble-dryer',
                   options=(), write_fn=_course_write),
    ),
)

JOB_BEGINNING_STATUS = Capability(
    href='/wm/jobbeginingstatus/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='job_beginning_status',
                   field='x.com.samsung.da.jobBeginingStatus',
                   name='Job beginning status',
                   entity_category='diagnostic'),
    ),
)

DRYER_DIAGNOSIS = Capability(
    href='/diagnosis/vs/0',
    poll_tier='warm',
    entities=(
        SensorDesc(key='diagnosis', field='x.com.samsung.da.diagnosisStart',
                   name='Diagnosis', entity_category='diagnostic'),
    ),
)
