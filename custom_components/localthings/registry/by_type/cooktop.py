"""Cooktop device registry for the /mode/vs/0 burner-options surface
(NA9300K-class, PR #23).

Fuel-neutral by name: the gas NA9300K and the NV9300K/NV8000T induction
cooktops (issues #508, #314) share this surface, and the name becomes the
default device name. Not to be confused with the induction_cooktop registry
(issue #86, by_type/induction_cooktop.py), a different OCF surface that
happens to share the English word "cooktop". The
`_REGISTRY_BY_KEY['cooktop']` lookup key is relied on by the legacy ARTIK051
'CT' modelNum token (for_device_by_model) and the resource-signature
fallback (for_device_by_resources) alike.
"""

from ..capabilities import common, cooktop, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="cooktop",
    capabilities=_build(
        [
            *ignored.IGNORED,
            cooktop.COOKTOP_POWER,
            cooktop.COOKTOP_MODE,
            cooktop.COOKTOP_CONNECTED,
            cooktop.PAIRED_HOOD_STATUS,
            common.FIRMWARE_UPDATE,
            # issue #314: /alarms/vs/0 and /kidslock/vs/0 are the same
            # generic shapes common.UNIVERSAL already models elsewhere --
            # picked individually rather than pulling in all of UNIVERSAL,
            # matching this registry's existing hand-picked-common style.
            common.ALARMS,
            common.KIDS_LOCK_VS_FALLBACK,
            # Not optional the way the two above are: registry.PROBE_HREFS is
            # global, so a board that answers the probe surfaces these as a
            # coverage gap unless every registry carries them (issue #301).
            common.FILE_LIST,
            common.FILE_TRANSFER,
        ]
    ),
)
