"""Capabilities specific to dishwasher appliances (DW9000F-class).

Resources verified against the live device dump at 10.0.0.129.

The /course/vs/0 cycle select and its options-array machinery are shared with
washer and dryer in laundry.py; only the dishwasher-specific options (storm
wash, auto release dry) are read locally here.
"""

from ..batch import is_stub_rep
from ..capability import Capability
from ..entities import ButtonDesc, SelectDesc, SensorDesc, SwitchDesc
from .common import diagnosis_status
from .laundry import (
    bool_option_switch,
    cycle_select,
    drum_clean_cycles_remaining,
)

# ---------------------------------------------------------------------------
# /dishwasher/vs/0 — cycle wash/dry settings
# ---------------------------------------------------------------------------


def _has(field):
    """Bound only where the board reports the field: the DW60BG750 (#538) has
    no heatedDry, and an absent field would otherwise show as a dead entity.
    A not-yet-fetched stub still binds, as elsewhere (issue #127)."""
    return lambda rep, resources: is_stub_rep(rep) or field in rep


def _setting_write(field):
    return lambda p, rep, href=None: (["dishwasher", "vs", "0"], {field: p})


DISHWASHER_SETTINGS = Capability(
    href="/dishwasher/vs/0",
    entities=(
        SwitchDesc(
            key="sanitize",
            field="x.com.samsung.da.sanitize",
            icon="mdi:bacteria",
            exists_fn=_has("x.com.samsung.da.sanitize"),
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["dishwasher", "vs", "0"],
                {"x.com.samsung.da.sanitize": "On" if p == "On" else "Off"},
            ),
        ),
        SwitchDesc(
            key="speed_booster",
            field="x.com.samsung.da.speedBooster",
            icon="mdi:fast-forward",
            exists_fn=_has("x.com.samsung.da.speedBooster"),
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["dishwasher", "vs", "0"],
                {"x.com.samsung.da.speedBooster": "On" if p == "On" else "Off"},
            ),
        ),
        SelectDesc(
            key="heated_dry",
            field="x.com.samsung.da.heatedDry",
            icon="mdi:heat-wave",
            options_field="x.com.samsung.da.supportedHeatedDry",
            exists_fn=_has("x.com.samsung.da.heatedDry"),
            write_fn=_setting_write("x.com.samsung.da.heatedDry"),
        ),
        # Which racks wash, as UPPER_LOWER: the DW60BG750 (#538) offers OFF_ON
        # and ON_ON. The reporter confirmed OFF_ON is the panel's lower-rack-only
        # zone wash.
        SelectDesc(
            key="wash_zone",
            field="x.com.samsung.da.selectedZone",
            icon="mdi:dishwasher",
            translation_key="wash_zone",
            options_field="x.com.samsung.da.supportedSelectedZone",
            exists_fn=_has("x.com.samsung.da.selectedZone"),
            write_fn=_setting_write("x.com.samsung.da.selectedZone"),
        ),
    ),
)

# /course/vs/0 -- cycle selection (shared laundry.cycle_select) plus the
# dishwasher-only StormWashZone / AutoDoorRelease toggles riding in the same
# options array (shared laundry.bool_option_switch). Course display names
# live in translations under entity.select.dishwasher_cycle.
#
# '83'/'86' were transposed in that catalog until issue #226: the original
# fixture's own live editCourseList puts them back to back, exactly the
# kind of adjacent pair a manual screenshot transcription slips on. The
# reporter's live confirmation (selecting 'Normal' ran the physical Express
# 60 program and vice versa) settled it: '86' is Express 60, '83' is Normal.
#
# Drum Clean+ maintenance tracking reuses washer.py/dryer.py's (issues #9,
# #258) DrumCleanProposal_/WashingTimes_/DrumCleanLog_ tokens riding on this
# same options[] array -- a live dump confirmed the dishwasher reports the
# identical trio (WashingTimes_18/DrumCleanProposal_20, plus a '|'-joined
# DrumCleanLog_ history matching the dryer's multi-entry shape), so
# laundry.drum_clean_cycles_remaining applies unchanged.
#
# laundry.drum_clean_last_cleaned (DrumCleanLog_'s own newest entry) is
# deliberately NOT wired up here (issue #398): a live dishwasher dump
# showed it moving every 30-90s on its own, including well after a cycle
# had already finished -- unlike the washer/dryer reports this reader was
# built from (issues #9, #258), it never settles on a value worth showing.

CYCLE_OPTIONS = Capability(
    href="/course/vs/0",
    entities=(
        cycle_select(translation_key="dishwasher_cycle", icon="mdi:dishwasher"),
        bool_option_switch("storm_wash", "mdi:weather-lightning-rainy", "StormWashZone"),
        bool_option_switch(
            "auto_release_dry", "mdi:door-open", "AutoDoorRelease", gate_on_presence=True
        ),
        SensorDesc(
            key="drum_clean_cycles_remaining",
            unit="cycles",
            icon="mdi:dishwasher-alert",
            state_class="measurement",
            exists_fn=lambda rep, resources: drum_clean_cycles_remaining(rep) is not None,
            rep_fn=drum_clean_cycles_remaining,
        ),
    ),
)

# ---------------------------------------------------------------------------
# Self-diagnostic trigger and last-operation-source sensor
# ---------------------------------------------------------------------------

DIAGNOSIS = Capability(
    href="/diagnosis/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="diagnosis_status",
            field="x.com.samsung.da.diagnosisStart",
            icon="mdi:stethoscope",
            entity_category="diagnostic",
            device_class="enum",
            options=("ready",),
            value_fn=diagnosis_status,
        ),
        ButtonDesc(
            key="diagnosis_start",
            field="",
            payload="Start",
            icon="mdi:play-circle-outline",
            entity_category="diagnostic",
            write_fn=lambda p, rep, href=None: (
                ["diagnosis", "vs", "0"],
                {"x.com.samsung.da.diagnosisStart": p},
            ),
        ),
    ),
)

OPERATION_ORIGIN = Capability(
    href="/operation/origin/vs/0",
    poll_tier="cold",
    entities=(
        SensorDesc(
            key="operation_origin", field="origin", icon="mdi:remote", entity_category="diagnostic"
        ),
    ),
)
