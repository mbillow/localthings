"""Capabilities specific to dishwasher appliances (DW9000F-class).

Resources verified against the live device dump at 10.0.0.129.

The /course/vs/0 cycle select and its options-array machinery are shared with
washer and dryer in laundry.py; only the dishwasher-specific options (storm
wash, auto release dry) are read locally here.
"""

from ..capability import Capability
from ..entities import ButtonDesc, SelectDesc, SensorDesc, SwitchDesc
from .laundry import bool_option_switch, cycle_select

# ---------------------------------------------------------------------------
# /dishwasher/vs/0 — cycle wash/dry settings
# ---------------------------------------------------------------------------

DISHWASHER_SETTINGS = Capability(
    href="/dishwasher/vs/0",
    entities=(
        SwitchDesc(
            key="sanitize",
            field="x.com.samsung.da.sanitize",
            icon="mdi:bacteria",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["dishwasher", "vs", "0"],
                {"x.com.samsung.da.sanitize": "On" if p == "On" else "Off"},
            ),
        ),
        SelectDesc(
            key="heated_dry",
            field="x.com.samsung.da.heatedDry",
            icon="mdi:heat-wave",
            options_field="x.com.samsung.da.supportedHeatedDry",
            write_fn=lambda p, rep, href=None: (
                ["dishwasher", "vs", "0"],
                {"x.com.samsung.da.heatedDry": p},
            ),
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

CYCLE_OPTIONS = Capability(
    href="/course/vs/0",
    entities=(
        cycle_select(translation_key="dishwasher_cycle", icon="mdi:dishwasher"),
        bool_option_switch("storm_wash", "mdi:weather-lightning-rainy", "StormWashZone"),
        bool_option_switch(
            "auto_release_dry", "mdi:door-open", "AutoDoorRelease", gate_on_presence=True
        ),
    ),
)

# ---------------------------------------------------------------------------
# Self-diagnostic trigger and last-operation-source sensor
# ---------------------------------------------------------------------------


def _diagnosis_status(value):
    """Normalize the confirmed idle diagnosis state for HA translation."""
    return "ready" if value == "Ready" else value


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
            value_fn=_diagnosis_status,
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
