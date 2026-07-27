"""Gas cooktop device registry (NA9300K-class, PR #23).

Named 'gas_cooktop' (not 'cooktop') so its diagnostics/device-info label
doesn't collide with the unrelated induction_cooktop family (issue #86,
by_type/induction_cooktop.py) -- two different OCF surfaces that happen to
share the English word "cooktop". The `_REGISTRY_BY_KEY['cooktop']` lookup
key is unchanged: it's relied on by real devices reporting
oneUiVersion "Cooktop" (for_device), the legacy ARTIK051 modelNum rule
(for_device_by_model), and the resource-signature fallback
(for_device_by_resources) alike.
"""

from ..capabilities import common, cooktop, ignored
from ._base import DeviceRegistry, _build


REGISTRY = DeviceRegistry(
    name='gas_cooktop',
    capabilities=_build([
        *ignored.IGNORED,
        cooktop.COOKTOP_POWER,
        cooktop.COOKTOP_MODE,
        cooktop.COOKTOP_CONNECTED,
        cooktop.PAIRED_HOOD_STATUS,
        common.FIRMWARE_UPDATE,
    ]),
)
