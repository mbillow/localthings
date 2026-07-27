"""Standalone induction-cooktop device registry (Samsung
TP1X_DA-KS-COOKTOP-class, issue #86) -- the cooktop half of the
range/oven board family (see capabilities/range.py), but with no oven
attached at all.

Reuses range.py's COOKTOP_STATUS/COOKTOP_SPEC/COOKTOP_SAFETY/PROBE_STATUS
wholesale (identical resource shapes to the range combo's cooktop half)
and cooktop.PAIRED_HOOD_STATUS for the Bluetooth-paired range hood some
units pair with. Distinct registry key from cooktop.REGISTRY ('cooktop')
-- that family is the unrelated NA9300K-class gas cooktop (burner state
embedded in /mode/vs/0's options array, a completely different OCF
surface that happens to share the English word "cooktop").
"""
from ..capabilities import common, cooktop as cooktop_caps, ignored
from ..capabilities import range as range_caps
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='induction_cooktop',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        *common.POWER,
        range_caps.COOKTOP_STATUS,
        range_caps.COOKTOP_SPEC,
        range_caps.COOKTOP_SAFETY,
        range_caps.PROBE_STATUS,
        cooktop_caps.PAIRED_HOOD_STATUS,
    ]),
)
