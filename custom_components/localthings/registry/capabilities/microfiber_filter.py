"""Capabilities for Samsung's microfiber filter (AMF board; issues #475, #626).

A lint filter that sits on a washer's drain. It shares the washer's
/operational/state/vs/0, but its progress vocabulary is the filter's own
(Filtering, Bypassing, Sensing, ...) and it has no cycle to delay, start
or time, so only the machine state carries over.
"""

from dataclasses import replace

from ..capability import Capability
from ..entities import SensorDesc
from .operational import OPERATIONAL_STATE

_SUPPORTED_PROGRESS = "x.com.samsung.da.supportedProgress"


def _activity_options(resources):
    rep = resources.get("/operational/state/vs/0") or {}
    return [str(v).lower() for v in rep.get(_SUPPORTED_PROGRESS) or ()]


FILTER_OPERATIONAL_STATE = replace(
    OPERATIONAL_STATE,
    entities=(
        next(e for e in OPERATIONAL_STATE.entities if e.key == "machine_state"),
        SensorDesc(
            key="filter_activity",
            field="x.com.samsung.da.progress",
            icon="mdi:filter-outline",
            device_class="enum",
            options=_activity_options,
            value_fn=lambda v: None if v is None else str(v).lower(),
        ),
    ),
)

# Washer plumbing the filter reports without a washer behind it: an empty
# /washer/vs/0, a course resource listing only the app's home-care wizard,
# the laundry job status, and kids-lock resources it leaves empty.
COVERAGE = [
    Capability(href=h)
    for h in (
        "/washer/vs/0",
        "/course/vs/0",
        "/wm/jobbeginingstatus/vs/0",
        "/kidslock/0",
        "/kidslock/vs/0",
    )
]
