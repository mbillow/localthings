"""Capabilities specific to the dryer family (Samsung DA_WM_TP1/TP2-class).

Dryer-specific controls only. The shared laundry surface -- power/kids-lock/
remote-control fallback pairs, buzzer, energy meter, job-beginning-status, and
the /course/vs/0 cycle select -- lives in laundry.py.

  /washer/vs/0   -> DRYER_SETTINGS (dryLevel, dryTime, dryerType, wrinklePrevent)
  /course/vs/0   -> DRYER_COURSE (shared cycle select; see below)
  /diagnosis/vs/0 -> DRYER_DIAGNOSIS
"""
from ..capability import Capability
from ..entities import SensorDesc, SwitchDesc
from .laundry import cycle_select


def _wrinkle_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    return ['washer', 'vs', '0'], {'x.com.samsung.da.wrinklePrevent': p}


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

# /course/vs/0 -- cycle selection, shared with washer/dishwasher via
# laundry.cycle_select (options read live from /wm/editcourse/vs/0, written as
# an RMW on the options array). Course display names live in translations
# under entity.select.dryer_cycle (Table_03, DV5000-class, captured
# 2026-05-29). Codes 0x21 and 0x4C appear in the issue #14 DV90BB5245AES1
# editCourseList but aren't identified yet -- they render as the raw code
# until named. The /st/dryercourse/vs/0 resource re-encodes the same selected
# course and is ignored (ignored.py) -- the mirror of how /st/washercourse/vs/0
# is ignored for washers.
DRYER_COURSE = Capability(
    href='/course/vs/0',
    entities=(
        cycle_select(translation_key='dryer_cycle_table_03', icon='mdi:tumble-dryer',
                     table_href='/st/dryercourse/vs/0', validated_table='Table_03'),
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
