"""Capabilities specific to the dryer family (Samsung DA_WM_TP1/TP2-class).

Dryer-specific controls only. The shared laundry surface -- power/kids-lock/
remote-control fallback pairs, buzzer, energy meter, job-beginning-status, and
the /course/vs/0 cycle select -- lives in laundry.py.

  /washer/vs/0        -> DRYER_SETTINGS (dryLevel, dryTime, dryerType, wrinklePrevent)
  /course/vs/0        -> DRYER_COURSE (shared cycle select, plus the AI-pattern
                          toggle read off the same options[] array; see below)
  /diagnosis/vs/0     -> DRYER_DIAGNOSIS
  /cycleinterface/vs/0 -> AUTO_CYCLE_LINK
"""
from ..capability import Capability
from ..entities import SelectDesc, SensorDesc, SwitchDesc
from .laundry import bool_option_switch, cycle_select


def _wrinkle_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    return ['washer', 'vs', '0'], {'x.com.samsung.da.wrinklePrevent': p}


DRYER_SETTINGS = Capability(
    href='/washer/vs/0',
    poll_tier='warm',
    entities=(
        # supportedDryLevel values (e.g. 'None'/'Less'/'Normal'/'More') are
        # already human-readable on this device, unlike the combo-unit
        # numeric-minute codes washer.py's washer_dry_level select needs
        # translations for -- no translation_key needed here.
        SelectDesc(key='dry_level', field='x.com.samsung.da.dryLevel',
                   name='Dry level', icon='mdi:water-percent',
                   options_field='x.com.samsung.da.supportedDryLevel',
                   write_fn=lambda p, rep, href=None: (
                       ['washer', 'vs', '0'], {'x.com.samsung.da.dryLevel': p})),
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
# 2026-05-29; codes 0x21/0x4C named from the issue #14 DV90BB5245AES1
# SmartThings course-list screenshots on 2026-07-24). The /st/dryercourse/vs/0
# resource re-encodes the same selected course and is ignored (ignored.py) --
# the mirror of how /st/washercourse/vs/0 is ignored for washers.
#
# The same options[] array also carries AiOption_On/Off -- the app's "AI
# pattern" toggle ("Get cycle recommendations based on your usage patterns"),
# confirmed against the issue #14 screenshots. Same On/Off token shape as
# washer.py's bubble-soak/pre-wash/intensive toggles; not previously bound.
DRYER_COURSE = Capability(
    href='/course/vs/0',
    entities=(
        cycle_select(translation_key='dryer_cycle', icon='mdi:tumble-dryer'),
        bool_option_switch('ai_pattern', 'AI pattern', 'mdi:creation', 'AiOption',
                            entity_category='config', gate_on_presence=True),
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

# /cycleinterface/vs/0 -- the app's "Auto cycle link" toggle ("automatically
# set to the best drying cycle by communicating with the washer cycle"; both
# devices must stay connected). Empty ({}) on every washer dump seen so far,
# which is why it used to sit in the global ignored.IGNORED list -- the
# issue #14 dryer dump is the first to populate it. washer.py scopes its own
# ignore locally now (washer.CYCLE_INTERFACE_IGNORED) to avoid a collision.
def _auto_cycle_link_write(p, rep, href=None):
    if p not in ('On', 'Off'):
        return None
    return ['cycleinterface', 'vs', '0'], {'x.com.samsung.da.cycleInterfaceEnabled': p}


AUTO_CYCLE_LINK = Capability(
    href='/cycleinterface/vs/0',
    entities=(
        SwitchDesc(key='auto_cycle_link', field='x.com.samsung.da.cycleInterfaceEnabled',
                   name='Auto cycle link', icon='mdi:link-variant',
                   entity_category='config',
                   value_fn=lambda v: v == 'On',
                   write_fn=_auto_cycle_link_write),
    ),
)
