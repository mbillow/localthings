"""Per-device-type registries."""
import re
from typing import Optional, Sequence

from ._base import DeviceRegistry
from . import (
    air_dresser, air_monitor, air_purifier, airconditioner, cooktop,
    dehumidifier, dishwasher, dryer, ehs, induction_cooktop, microwave, oven,
    range as _range, range_hood, refrigerator, vacuum_station, washer,
    water_purifier,
)

__all__ = [
    'DeviceRegistry', 'resolve', 'for_device_by_oic_type', 'for_device_by_model',
    'for_device_by_resources', '_board_tokens',
]


# One entry per registry, no aliases: every key here is reachable from
# `_BOARD_TOKEN_TO_KEY`, `_CONSUMER_PREFIX_TO_KEY`, or `for_device_by_resources`.
_REGISTRY_BY_KEY: dict[str, DeviceRegistry] = {
    'air_dresser': air_dresser.REGISTRY,
    'air_monitor': air_monitor.REGISTRY,
    'air_purifier': air_purifier.REGISTRY,
    'airconditioner': airconditioner.REGISTRY,
    'cooktop': cooktop.REGISTRY,
    'dehumidifier': dehumidifier.REGISTRY,
    'dishwasher': dishwasher.REGISTRY,
    'dryer': dryer.REGISTRY,
    'ehs': ehs.REGISTRY,
    'induction_cooktop': induction_cooktop.REGISTRY,
    'microwave': microwave.REGISTRY,
    'oven': oven.REGISTRY,
    'range': _range.REGISTRY,
    'range_hood': range_hood.REGISTRY,
    'refrigerator': refrigerator.REGISTRY,
    'vacuum_station': vacuum_station.REGISTRY,
    'washer': washer.REGISTRY,
    'water_purifier': water_purifier.REGISTRY,
}


# Consumer-model prefix (first two letters of the '_'-delimited token in
# `description` right before any '/board-info' suffix) -> registry key.
# NOT derived from `modelNum` -- washer and dryer share the same 'DA_WM_'
# internal board-family prefix there, and dishwasher's modelNum contains
# the substring 'WW', so a modelNum-only rule misroutes both.
_CONSUMER_PREFIX_TO_KEY: dict[str, str] = {
    'WW': 'washer',
    'WD': 'washer',
    'WF': 'washer',
    'WV': 'washer',  # FlexWash twin units (e.g. WV55M9600AW) -- issue #19
    'WA': 'washer',  # Top-load washers (e.g. WA8000T) -- issue #106
    'DV': 'dryer',
    'DW': 'dishwasher',
}

# Board-family token -> registry key, matched against whole tokens of
# `modelNum`/`description` (see `_board_tokens`).
#
# Tokenizing instead of substring-matching is what keeps this a table rather
# than a ladder of hand-written rules. Samsung spells the same board family
# with either delimiter -- 'TP1X_DA-AC-RAC-01001' and 'TP2X_RAC_20K' are the
# same RAC family -- so a substring rule has to be written once per spelling
# ('_RAC_' *and* '-RAC-'), and a token that lands at the end of the
# pipe-prefix with no trailing delimiter ('ARTIK051_DONGLE_REF', issues #77
# and #83) matches no '_TOKEN_' spelling at all. Whole-token matching sees
# every one of those as a single entry.
#
# Entries must name the *specific* device type, never the board family that
# contains it: 'DA-AC-' prefixes RAC/WAC/DHM/AIR alike, so a bare 'AC' entry
# would swallow the dehumidifier and the air purifier. Where two families
# genuinely share a resource surface they share a registry (all the
# air-conditioner spellings below), which is a statement about the hardware,
# not a shortcut.
_BOARD_TOKEN_TO_KEY: dict[str, str] = {
    'REF': 'refrigerator',
    # Air conditioners. Every one of these is a distinct board family with
    # the same resource surface: room (issues #37, #91), package, Korean
    # (#136), window (#87), 2-in-1 floor+wall (#150, #153), system/commercial
    # (#52), cassette (#191), and ARA-WW wall-mount (#115, #116, #117, #120).
    'RAC': 'airconditioner',
    'PRAC': 'airconditioner',
    'KRAC': 'airconditioner',
    'WAC': 'airconditioner',
    'FAC': 'airconditioner',
    'CAWW': 'airconditioner',
    'CAC': 'airconditioner',        # issue #191 -- TP1X_DA-AC-CAC-01001_0000
    'ARA': 'airconditioner',
    'DHM': 'dehumidifier',          # issue #88 -- target humidity, no climate
    'EHS': 'ehs',                   # Eco Heating System air-to-water heat pump --
                                     # zone1 space heating/cooling + dhw domestic
                                     # hot water, its own /mode/*/vs/0 and
                                     # /temperatures/*/vs/0 resource shapes
    'TVTL': 'air_purifier',         # issue #56 (ARTIK051)
    'VTWW': 'air_purifier',         # issue #151 (BESPOKE Cube Air)
    'AVT': 'air_purifier',          # issue #190 -- AVT-WW-TP1-23-AXX500, a
                                     # next-gen BESPOKE Cube Air board; same
                                     # lineage as VTWW above but the '-WW-'
                                     # delimiter now falls one letter to the
                                     # left ('A-VTWW-' -> 'AVT-WW-'), splitting
                                     # into a token the existing entry can't see
    'AIR': 'air_purifier',          # issue #130 (TP1X_DA-AC-AIR)
    'WATERPURIFIER': 'water_purifier',   # issue #90
    'ADW': 'dishwasher',
    'AHD': 'range_hood',
    'RANGE': 'range',               # issue #44 -- cooktop+oven combo
    'OVEN': 'oven',                 # issue #55 -- wall oven, no burners
    'MICROWAVE': 'microwave',       # issues #66, #121
    'COOKTOP': 'induction_cooktop',  # issue #86 -- standalone, no oven
    # Legacy ARTIK051 gas cooktops ('ARTIK051_GB_CT_001'), whose burner state
    # lives in /mode/vs/0's options array. Deliberately a bare two-letter
    # token, and so the loosest entry in this table -- it is only ever
    # reached by a device that matched nothing more specific, and its
    # `description` ('ARTIK051_GLOBAL_COOKTOP') would otherwise read as an
    # induction cooktop via the COOKTOP entry above. See `for_device_by_model`
    # for the field ordering that makes that resolve correctly.
    'CT': 'cooktop',
    'VSKR': 'vacuum_station',       # issue #131 -- stick-vacuum clean station
    'DF': 'air_dresser',            # issue #162
    'VSWW': 'vacuum_station',       # issue #219
    'ASM': 'air_monitor',           # issue #210 -- Air Monitor Plus
}

_TOKEN_SPLIT_RE = re.compile(r'[^A-Z0-9]+')


def _board_tokens(value: str, cut_at: str) -> list[str]:
    """Whole, upper-cased tokens of `value` up to the first `cut_at`.

    `cut_at` drops the trailing junk each field carries -- everything after
    modelNum's first '|' (a board revision and a capability bitmap, which can
    contain anything) and after description's first '/' (a '/DC92-...' board
    part number).
    """
    head = (value or '').split(cut_at, 1)[0].upper()
    return [t for t in _TOKEN_SPLIT_RE.split(head) if t]


def _board_family_key(value: str, cut_at: str) -> Optional[str]:
    """First `_BOARD_TOKEN_TO_KEY` hit among `value`'s tokens, or None.

    No known modelNum or description yields two *conflicting* board keys, so
    which token is found first doesn't matter within one field -- the table is
    a flat lookup, not a priority list. Adding an entry that could co-occur
    with another (a family token, or one short enough to collide by accident)
    would break that property; see this table's comment.

    One documented exception (issue #196): AILITE water-purifier boards
    spell their modelNum '...-REF-WATERPURIFIER-...', where 'REF' names the
    shared cooling-subsystem board, not the refrigerator device type --
    'WATERPURIFIER' is the actual, more specific type here. Rather than drop
    or rename either entry (both are correct on their own for the model
    strings that exist today), this one known co-occurrence resolves to
    'water_purifier'; TestBoardTokenAmbiguity's blanket check carries a
    matching carve-out for this exact pair.
    """
    tokens = _board_tokens(value, cut_at)
    if 'REF' in tokens and 'WATERPURIFIER' in tokens:
        return 'water_purifier'
    for token in tokens:
        key = _BOARD_TOKEN_TO_KEY.get(token)
        if key is not None:
            return key
    return None


def _consumer_model_key(description: str) -> Optional[str]:
    """Registry key from the consumer-model token in `description`, or None.

    Usually that token is the last '_'-delimited segment before any
    '/board-info' suffix (e.g. '..._WW90DG6U25LEU4' -> 'WW90DG6U25LEU4').
    But issue #79's dryer pairs two model numbers in one description --
    '..._DVE50A8800_8600/DC92-...' -- so the true consumer token
    ('DVE50A8800') sits one segment *before* the actual last segment
    ('8600', a bare second model number with no recognizable prefix). Scan
    segments from the end and take the first one that resolves, rather
    than assuming the last segment is always it.

    Splits on '_' only, unlike `_board_tokens` above: these are two-letter
    prefixes matched against the *start* of a segment, so widening the split
    to '-' as well would start reading board-family segments as consumer
    models -- the dishwasher's 'ADW-WW-RTL-24-AILITE' would offer up a bare
    'WW' segment and route to washer.

    Only a 2-letter *prefix* match -- e.g. 'WAC' (the Window Air Conditioner
    board-family token, issue #87) also starts with 'WA' (the top-load-washer
    prefix, issue #106) at this granularity. for_device_by_model() consults
    the board-family table first and this function only as a fallback, so
    that ambiguity resolves correctly without this function needing to know
    about unrelated device families.
    """
    segments = (description or '').split('/', 1)[0].split('_')
    for segment in reversed(segments):
        key = _CONSUMER_PREFIX_TO_KEY.get(segment[:2].upper())
        if key is not None:
            return key
    return None


# /oic/d's `rt` (OCF's own device-type declaration, see registry/identity.py)
# -> registry key. This is the device naming its own type -- no board-part
# guessing involved -- so it's consulted before modelNum/description at all.
#
# Every value must already be a key in `_REGISTRY_BY_KEY` (checked by
# `test_every_oic_type_resolves_to_a_real_registry`). That's why this list
# stops well short of the full OCF/SmartThings device-type vocabulary: a
# compiled list of `x.com.st.d.*` types will include plenty of device
# categories (lights, switches, sensors, locks, cameras, TVs, generic energy
# meters, ...) no Samsung DA appliance dump could ever report and this
# integration has no registry for -- and 'oic.d.robotcleaner' names an
# actual robot vacuum, a different product from the clean/auto-empty
# *station* `vacuum_station` covers (see that registry's own module
# docstring); mapping it there would misroute a genuine robot-vacuum dump
# into a registry with no vacuum-body capabilities at all. Add a row only
# once there's a real registry key on the right-hand side to point at.
#
# `x.com.st.d.*` entries are SmartThings' own vendor extension to the OCF
# device-type vocabulary (used for categories with no `oic.d.*` equivalent),
# same prefix convention as the `x.com.samsung.da.*` resource fields
# elsewhere in this codebase.
#
# `oic.d.cooktop` is deliberately absent, and is the one measured type left out.
# A TP1X_DA-KS-COOKTOP induction reports it, but `cooktop` and
# `induction_cooktop` are two unrelated registries that happen to share the
# English word (see by_type/cooktop.py's docstring: the NA9300K gas family keeps
# burner state in /mode/vs/0's options array, a completely different OCF
# surface). The OCF type does not distinguish them, so mapping it to either key
# would silently misroute the other -- and as the *primary* signal it would
# override a `COOKTOP`/`CT` board token that had it right. Same reasoning as
# `oic.d.robotcleaner` above: no unambiguous key to point at, so no row.
_OIC_TYPE_TO_KEY: dict[str, str] = {
    'oic.d.airconditioner': 'airconditioner',
    'oic.d.airpurifier': 'air_purifier',
    'oic.d.dishwasher': 'dishwasher',
    'oic.d.dryer': 'dryer',
    'oic.d.oven': 'oven',
    'oic.d.refrigerator': 'refrigerator',
    'oic.d.washer': 'washer',
    'x.com.st.d.airqualitysensor': 'air_monitor',
    'x.com.st.d.hood': 'range_hood',              # AHD-WW-TP1-22-COMMON
    'x.com.st.d.stickcleaner': 'vacuum_station',
    'x.com.st.d.steamcloset': 'air_dresser',
}


def for_device_by_oic_type(device_types: Sequence[str]) -> Optional[DeviceRegistry]:
    """Device-type detection from /oic/d's `rt` -- OCF's own device-type
    declaration.

    The primary path when a dump carries it: the device names its own type,
    so there's nothing to infer from board part numbers. Most hardware still
    doesn't populate `/oic/d` usefully -- see `resolve()`'s docstring -- so
    this only ever helps a minority of dumps, and `for_device_by_model`/
    `for_device_by_resources` remain load-bearing for everything else.
    """
    for device_type in device_types:
        key = _OIC_TYPE_TO_KEY.get(device_type)
        if key is not None:
            return _REGISTRY_BY_KEY[key]
    return None


def for_device_by_model(model_num: str, description: str) -> Optional[DeviceRegistry]:
    """Device-type detection from /information/vs/0's model strings.

    The primary path: the board named in `modelNum` determines the resource
    surface, which is what a registry describes.

    Three passes, narrowest evidence first:

    1. Board-family tokens in `modelNum`. The most reliable signal -- it names
       the board, which determines the resource surface.
    2. The same tokens in `description`. Some units carry the board token only
       there (a scrubbed or placeholder modelNum, e.g. description
       'TP1X_REF_21K'). This runs second so that a device whose two fields
       disagree is typed by its modelNum: the legacy gas cooktop reports
       'ARTIK051_GB_CT_001' (CT -> gas cooktop) alongside
       'ARTIK051_GLOBAL_COOKTOP' (COOKTOP -> induction cooktop), and the
       board is right.
    3. The consumer-model prefix in `description` (washer/dryer/dishwasher).
       Last, because a bare two-letter prefix is the fuzziest evidence here
       and would otherwise shadow the specific board tokens above.

    Args:
        model_num: x.com.samsung.da.modelNum from /information/vs/0.
        description: x.com.samsung.da.description from /information/vs/0.

    Returns:
        DeviceRegistry if the modelNum or consumer-model code resolves to a
        known type, None otherwise.
    """
    key = (
        _board_family_key(model_num, '|')
        or _board_family_key(description, '/')
        or _consumer_model_key(description)
    )
    return _REGISTRY_BY_KEY.get(key) if key else None


def for_device_by_resources(resources: dict[str, dict]) -> Optional[DeviceRegistry]:
    """Detect a device family from a distinctive local-resource signature.

    For boards that ship no ``/information/vs/0`` at all, leaving
    `for_device_by_model` nothing to read. Some newer cooktops are the
    original case: their mode resource still identifies them, carrying a
    DeviceType option and multiple per-burner OperationState options.

    Require two independent shapes for every signature here, never one, so
    an unrelated family's ``/mode/vs/0`` isn't misclassified.
    """
    mode = resources.get('/mode/vs/0', {})
    options = mode.get('x.com.samsung.da.options') or ()
    has_device_type = any(
        isinstance(option, str) and option.startswith('DeviceType_')
        for option in options
    )
    operation_states = sum(
        1 for option in options
        if isinstance(option, str) and option.startswith('OperationState')
    )
    if has_device_type and operation_states >= 2:
        return _REGISTRY_BY_KEY['cooktop']
    if (
        '/hood/fanspeed/vs/0' in resources
        and '/hood/lamp/vs/0' in resources
    ):
        return _REGISTRY_BY_KEY['range_hood']
    # Oven/range/microwave boards that report no /information/vs/0 at all
    # (issue #74's NE63B8411SS, issue #172's ME8000T -- the resource is simply
    # absent from the dump, not just empty) can't be matched via
    # for_device_by_model's modelNum tokens either. Mode vocabulary alongside
    # the oven cavity resource (/oven/vs/0) is a safe signature.
    supported_modes = mode.get('x.com.samsung.da.supportedModes') or ()
    if '/oven/vs/0' in resources:
        if any(
            m in supported_modes
            for m in ('MicroWave', 'MicroWaveGrill', 'MicroWaveConvection')
        ):
            return _REGISTRY_BY_KEY['microwave']
        if 'Bake' in supported_modes:
            if '/cooktopmonitoring/vs/0' in resources or '/cooktop/status/vs/0' in resources:
                return _REGISTRY_BY_KEY['range']
            return _REGISTRY_BY_KEY['oven']
    return None


def resolve(
    resources: dict[str, dict], device_types: Sequence[str] = (),
) -> Optional[DeviceRegistry]:
    """Device type for a parsed /device/0 dump, or None if unrecognized.

    The single entry point for detection -- the coordinator, the config
    flow's probe and the golden-regression harness all call this, so the
    order can't drift between what ships and what the tests assert.

    `device_types` (/oic/d's `rt`, read separately from the /device/0 dump --
    see registry/identity.py) is the primary signal when present: the device
    naming its own type beats parsing board part numbers. Falls back to model
    strings (`for_device_by_model`), then a distinctive resource signature
    (`for_device_by_resources`) for boards that report no /information/vs/0
    at all -- both unchanged from before /oic/d was ever consulted, since most
    hardware still doesn't populate it usefully.

    `/otninformation/vs/0`'s oneUiVersion is deliberately not consulted. It
    reads like the obvious signal -- the device naming its own type, e.g.
    '7.0 Dishwasher' -- but only a minority of hardware populates it, every
    device that does is already typed by its modelNum board token, and no
    device-support issue has ever been fixed by adding a mapping for it. It
    is still reported in diagnostics as a firmware-generation marker.
    """
    info = resources.get('/information/vs/0', {})
    return (
        for_device_by_oic_type(device_types)
        or for_device_by_model(
            info.get('x.com.samsung.da.modelNum', ''),
            info.get('x.com.samsung.da.description', ''),
        )
        or for_device_by_resources(resources)
    )
