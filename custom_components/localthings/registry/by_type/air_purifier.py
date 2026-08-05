"""Air-purifier device registry.

Spans three board generations sharing this one registry (see
capabilities/air_purifier.py's module docstring for the per-href
match_fn discriminators that keep them from colliding):

- ARTIK051_TVTL-class (issue #56). Resolved via the 'TVTL' modelNum board
  token (see by_type/__init__.py).
- TP1X_DA-AC-AIR-class (issue #130). Resolved via the 'AIR' board token.
  Adds real fan-mode control plus
  display/HEPA-filter/pet-filter/sound resources the older family never
  reported; reuses airconditioner.DISPLAY_LIGHT and airconditioner.MUTE_ONCE
  for /light/vs/0 and /option/muteonce/vs/0, which are identical shapes on
  the shared DA-AC- board family.
- A-VTWW-TP2-21-COMMON-class (issue #151). Resolved via the 'VTWW' board
  token, added for it. Its fan
  is WIND_STRENGTH_FAN on /wind/strength/vs/0 rather than FAN on
  /mode/vs/0 -- see that capability's comment.
- AVT-WW-TP1-23-class (issue #190). A next-gen board in the same VTWW
  lineage, resolved via its own 'AVT' board token since the '-WW-' delimiter
  falls one letter to the left of 'VTWW's whole-token spelling. Same
  resource surface as A-VTWW-TP2-21-COMMON above; no new capabilities
  needed.

AIR_LEVEL_CHECK ("AI Purify" -- the periodic air-quality sensing engine on
/airlevelcheck/vs/0) is shared by the last three of those: their dumps all
carry the resource with the same field names, and only the TVTL family has no
such href. It was covered as opaque plumbing until two AVT-WW-TP1 dumps
(issues #84 and #190) showed it drives a real user-facing feature.

Reuses dishwasher.DIAGNOSIS for /diagnosis/vs/0 (identical field/write
contract).
"""

from ..capabilities import air_purifier, airconditioner, common, dishwasher, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="air_purifier",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *common.UNIVERSAL,
            *common.POWER,
            dishwasher.DIAGNOSIS,
            air_purifier.AIR_QUALITY,
            air_purifier.AIR_LEVEL_CHECK,
            air_purifier.FILTER,
            air_purifier.DEVICE_ACTIVE,
            air_purifier.AIRFLOW_GENERIC,
            air_purifier.AIRFLOW_VS_FALLBACK,
            air_purifier.MODE,
            air_purifier.FAN,
            air_purifier.WIND_STRENGTH_FAN,
            air_purifier.DISPLAY,
            air_purifier.HEPA_FILTER,
            air_purifier.PANEL_STATUS,
            air_purifier.PET_FILTER_ACTIVATION,
            air_purifier.SOUND_MODE,
            air_purifier.SOUND_OUTPUT,
            air_purifier.SOUND_VOLUME,
            airconditioner.DISPLAY_LIGHT,
            airconditioner.MUTE_ONCE,
            *air_purifier.COVERAGE,
        ]
    ),
)
