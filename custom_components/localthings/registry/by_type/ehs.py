"""EHS (Eco Heating System) air-to-water heat pump device registry
(Samsung TP1X_DA_AC_EHS-class).

Shares the DA_AC_ board prefix with the room-AC family in
airconditioner.py, but its /mode/*/vs/0 and /temperatures/*/vs/0 resources
are its own shape (two independent loops: zone1 space heating/cooling and
dhw domestic hot water), not airconditioner.py's HREF_MODE/HREF_TEMP* OCF
pattern -- so nothing from that module is reused here except MUTE_ONCE,
whose /option/muteonce/vs/0 field shape (`muteonce`) is identical on this
family's dump.
"""
from ..capabilities import airconditioner, common, ehs, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name='ehs',
    capabilities=_build([
        *ignored.IGNORED,
        *common.UNIVERSAL,
        airconditioner.MUTE_ONCE,
        ehs.ZONE_POWER,
        ehs.ZONE_MODE,
        ehs.ZONE_TEMPERATURE,
        ehs.DHW_POWER,
        ehs.DHW_MODE,
        ehs.DHW_TEMPERATURE,
        ehs.AWAY_MODE,
        *ehs.COVERAGE,
    ]),
)
