"""Per-device-type registries."""
import re
from typing import Optional

from ._base import DeviceRegistry
from . import (
    air_dresser, air_purifier, airconditioner, cooktop, dehumidifier,
    dishwasher, dryer, induction_cooktop, microwave, oven, range as _range,
    range_hood, refrigerator, vacuum_station, washer, water_purifier,
)

__all__ = [
    'DeviceRegistry', 'resolve', 'for_device_by_model',
    'for_device_by_resources', '_board_tokens',
]


# One entry per registry, no aliases: every key here is reachable from
# `_BOARD_TOKEN_TO_KEY`, `_CONSUMER_PREFIX_TO_KEY`, or `for_device_by_resources`.
_REGISTRY_BY_KEY: dict[str, DeviceRegistry] = {
    'air_dresser': air_dresser.REGISTRY,
    'air_purifier': air_purifier.REGISTRY,
    'airconditioner': airconditioner.REGISTRY,
    'cooktop': cooktop.REGISTRY,
    'dehumidifier': dehumidifier.REGISTRY,
    'dishwasher': dishwasher.REGISTRY,
    'dryer': dryer.REGISTRY,
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
    # (#52), ARA-WW wall-mount (#115, #116, #117, #120), and CAC
    # (TP1X_DA-AC-CAC-01001, a Korean-market unit).
    'RAC': 'airconditioner',
    'PRAC': 'airconditioner',
    'KRAC': 'airconditioner',
    'CAC': 'airconditioner',
    'WAC': 'airconditioner',
    'FAC': 'airconditioner',
    'CAWW': 'airconditioner',
    'ARA': 'airconditioner',
    'DHM': 'dehumidifier',          # issue #88 -- target humidity, no climate
    'TVTL': 'air_purifier',         # issue #56 (ARTIK051)
    'VTWW': 'air_purifier',         # issue #151 (BESPOKE Cube Air)
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
    """
    for token in _board_tokens(value, cut_at):
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


def resolve(resources: dict[str, dict]) -> Optional[DeviceRegistry]:
    """Device type for a parsed /device/0 dump, or None if unrecognized.

    The single entry point for detection -- the coordinator, the config
    flow's probe and the golden-regression harness all call this, so the
    order can't drift between what ships and what the tests assert.

    Model strings first (`for_device_by_model`), then a distinctive resource
    signature (`for_device_by_resources`) for boards that report no
    /information/vs/0 at all.

    `/otninformation/vs/0`'s oneUiVersion is deliberately not consulted. It
    reads like the obvious signal -- the device naming its own type, e.g.
    '7.0 Dishwasher' -- but only a minority of hardware populates it, every
    device that does is already typed by its modelNum board token, and no
    device-support issue has ever been fixed by adding a mapping for it. It
    is still reported in diagnostics as a firmware-generation marker.
    """
    info = resources.get('/information/vs/0', {})
    return for_device_by_model(
        info.get('x.com.samsung.da.modelNum', ''),
        info.get('x.com.samsung.da.description', ''),
    ) or for_device_by_resources(resources)
