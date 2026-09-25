"""Cooktop device registry, for everything reporting `oic.d.cooktop`.

Named after the OCF device type rather than a fuel: gas and induction units
alike report it (issues #508, #314). Two board generations sit behind it,
with disjoint resource surfaces, so both capability sets live here and each
binds only on the board that serves its hrefs:

- TP2X and legacy ARTIK051 boards (NA9300K gas, NV9300K/NV8000T induction;
  PR #23) report burner state as strings in /mode/vs/0's options array.
  Read-only: no write contract has been verified for them.
- TP1X_DA-KS-COOKTOP boards (NV8500T, NV9000D; issue #86) serve the range
  combo's /cooktop/status/vs/0 burnerList surface, reused from range.py.

The one href both serve differently is /power/vs/0, split on which surface
the board has.
"""

import dataclasses

from ..capabilities import common, cooktop, ignored
from ..capabilities import range as range_caps
from ._base import DeviceRegistry, _build


def _burner_list_board(rep, resources):
    return "/cooktop/status/vs/0" in resources


_OPTIONS_POWER = dataclasses.replace(
    cooktop.COOKTOP_POWER,
    match_fn=lambda rep, resources: not _burner_list_board(rep, resources),
)
# POWER_VS_FALLBACK's own rule (the OCF-standard /power/0 wins) plus the
# surface split.
_BURNER_LIST_POWER = dataclasses.replace(
    common.POWER_VS_FALLBACK,
    match_fn=lambda rep, resources: (
        _burner_list_board(rep, resources) and "/power/0" not in resources
    ),
)


def _unique(caps):
    """Both surfaces pull in some of the same shared capabilities."""
    seen: set[int] = set()
    return [c for c in caps if not (id(c) in seen or seen.add(id(c)))]


REGISTRY = DeviceRegistry(
    name="cooktop",
    capabilities=_build(
        _unique(
            [
                *ignored.IGNORED,
                *common.UNIVERSAL,
                common.POWER_GENERIC,
                _OPTIONS_POWER,
                _BURNER_LIST_POWER,
                # /mode/vs/0 options-array boards.
                cooktop.COOKTOP_MODE,
                cooktop.COOKTOP_CONNECTED,
                # /cooktop/status/vs/0 burnerList boards.
                range_caps.COOKTOP_STATUS,
                range_caps.COOKTOP_SPEC,
                range_caps.COOKTOP_SAFETY,
                range_caps.PROBE_STATUS,
                cooktop.PAIRED_HOOD_STATUS,
                common.FIRMWARE_UPDATE,
                common.ALARMS,
                common.KIDS_LOCK_VS_FALLBACK,
                # registry.PROBE_HREFS is global, so every registry carries
                # these (issue #301).
                common.FILE_LIST,
                common.FILE_TRANSFER,
            ]
        )
    ),
)
