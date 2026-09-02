"""Per-device-type registries."""

import re
from collections.abc import Sequence

from . import (
    air_dresser,
    air_monitor,
    air_purifier,
    airconditioner,
    cooktop,
    dehumidifier,
    dishwasher,
    dryer,
    ehs,
    induction_cooktop,
    microwave,
    oven,
    range_hood,
    refrigerator,
    vacuum_station,
    washer,
    water_purifier,
)
from . import (
    range as _range,
)
from ._base import DeviceRegistry

__all__ = [
    "DeviceRegistry",
    "_board_tokens",
    "for_device_by_model",
    "for_device_by_oic_type",
    "for_device_by_resources",
    "resolve",
]


# One entry per registry, no aliases: every key here is reachable from
# `_BOARD_TOKEN_TO_KEY`, `_CONSUMER_PREFIX_TO_KEY`, or `for_device_by_resources`.
_REGISTRY_BY_KEY: dict[str, DeviceRegistry] = {
    "air_dresser": air_dresser.REGISTRY,
    "air_monitor": air_monitor.REGISTRY,
    "air_purifier": air_purifier.REGISTRY,
    "airconditioner": airconditioner.REGISTRY,
    "cooktop": cooktop.REGISTRY,
    "dehumidifier": dehumidifier.REGISTRY,
    "dishwasher": dishwasher.REGISTRY,
    "dryer": dryer.REGISTRY,
    "ehs": ehs.REGISTRY,
    "induction_cooktop": induction_cooktop.REGISTRY,
    "microwave": microwave.REGISTRY,
    "oven": oven.REGISTRY,
    "range": _range.REGISTRY,
    "range_hood": range_hood.REGISTRY,
    "refrigerator": refrigerator.REGISTRY,
    "vacuum_station": vacuum_station.REGISTRY,
    "washer": washer.REGISTRY,
    "water_purifier": water_purifier.REGISTRY,
}


# Consumer-model prefix (first two letters of the '_'-delimited token in
# `description` right before any '/board-info' suffix) -> registry key.
# NOT derived from `modelNum`: washer and dryer share the same 'DA_WM_'
# board-family prefix there, and dishwasher's modelNum contains the
# substring 'WW', so a modelNum-only rule misroutes both.
_CONSUMER_PREFIX_TO_KEY: dict[str, str] = {
    "WW": "washer",
    "WD": "washer",
    "WF": "washer",
    "WV": "washer",  # FlexWash twin units (e.g. WV55M9600AW) -- issue #19
    "WA": "washer",  # Top-load washers (e.g. WA8000T) -- issue #106
    "DV": "dryer",
    "DW": "dishwasher",
}

# Board-family token -> registry key, matched against whole tokens of
# `modelNum`/`description` (see `_board_tokens`).
#
# Tokenizing instead of substring-matching keeps this a table rather than a
# ladder of hand-written rules: Samsung spells the same board family with
# either delimiter ('TP1X_DA-AC-RAC-01001' vs 'TP2X_RAC_20K', both RAC), so
# a substring rule would need writing once per spelling, and a token with
# no trailing delimiter ('ARTIK051_DONGLE_REF') would match neither.
#
# Entries must name the specific device type, never the board family that
# contains it: 'DA-AC-' prefixes RAC/WAC/DHM/AIR alike, so a bare 'AC' entry
# would swallow the dehumidifier and the air purifier. Where two families
# genuinely share a resource surface they share a registry (the
# air-conditioner spellings below), which is a statement about the
# hardware, not a shortcut.
_BOARD_TOKEN_TO_KEY: dict[str, str] = {
    "REF": "refrigerator",
    # Air conditioners: distinct board families sharing one resource
    # surface -- room, package, Korean (#136), window (#87), 2-in-1
    # floor+wall (#150/#153), system/commercial (#52), cassette (#191), and
    # ARA-WW wall-mount (#115-120).
    "RAC": "airconditioner",
    "PRAC": "airconditioner",
    "KRAC": "airconditioner",
    "WAC": "airconditioner",
    "FAC": "airconditioner",
    "CAWW": "airconditioner",
    "CAC": "airconditioner",  # issue #191
    "ARA": "airconditioner",
    "DHM": "dehumidifier",  # issue #88 -- target humidity, no climate
    "EHS": "ehs",  # heat pump: zone1 heating/cooling + domestic hot water
    "TVTL": "air_purifier",  # issue #56 (ARTIK051)
    "VTWW": "air_purifier",  # issue #151 (BESPOKE Cube Air)
    # issue #190: same lineage as VTWW, but the '-WW-' delimiter falls one
    # letter left ('A-VTWW-' -> 'AVT-WW-'), splitting into a different token.
    "AVT": "air_purifier",
    "AIR": "air_purifier",  # issue #130 (TP1X_DA-AC-AIR)
    "WATERPURIFIER": "water_purifier",  # issue #90
    "ADW": "dishwasher",
    "AHD": "range_hood",
    "RANGE": "range",  # issue #44 -- cooktop+oven combo
    "OVEN": "oven",  # issue #55 -- wall oven, no burners
    "MICROWAVE": "microwave",  # issues #66, #121
    "COOKTOP": "induction_cooktop",  # issue #86 -- standalone, no oven
    # Legacy ARTIK051 gas cooktops ('ARTIK051_GB_CT_001'): burner state
    # lives in /mode/vs/0's options array. Deliberately the loosest entry
    # here -- reached only when nothing more specific matched, since its
    # description ('ARTIK051_GLOBAL_COOKTOP') would otherwise read as an
    # induction cooktop via COOKTOP above (see for_device_by_model's field
    # ordering).
    "CT": "cooktop",
    "VSKR": "vacuum_station",  # issue #131 -- stick-vacuum clean station
    "DF": "air_dresser",  # issue #162
    "VSWW": "vacuum_station",  # issue #219
    "ASM": "air_monitor",  # issue #210 -- Air Monitor Plus
}

_TOKEN_SPLIT_RE = re.compile(r"[^A-Z0-9]+")


def _board_tokens(value: str, cut_at: str) -> list[str]:
    """Whole, upper-cased tokens of `value` up to the first `cut_at`.

    `cut_at` drops the trailing junk each field carries -- everything after
    modelNum's first '|' (a board revision and a capability bitmap, which can
    contain anything) and after description's first '/' (a '/DC92-...' board
    part number).
    """
    head = (value or "").split(cut_at, 1)[0].upper()
    return [t for t in _TOKEN_SPLIT_RE.split(head) if t]


def _board_family_key(value: str, cut_at: str) -> str | None:
    """First `_BOARD_TOKEN_TO_KEY` hit among `value`'s tokens, or None.

    No known modelNum or description yields two conflicting board keys, so
    which token is found first doesn't matter within one field -- the
    table is a flat lookup, not a priority list.

    One documented exception (issue #196): AILITE water-purifier boards
    spell their modelNum '...-REF-WATERPURIFIER-...', where 'REF' names
    the shared cooling-subsystem board, not the refrigerator type --
    'WATERPURIFIER' is the actual, more specific type. This one known
    co-occurrence resolves to 'water_purifier'; TestBoardTokenAmbiguity
    carries a matching carve-out for this exact pair.
    """
    tokens = _board_tokens(value, cut_at)
    if "REF" in tokens and "WATERPURIFIER" in tokens:
        return "water_purifier"
    for token in tokens:
        key = _BOARD_TOKEN_TO_KEY.get(token)
        if key is not None:
            return key
    return None


def _consumer_model_key(description: str) -> str | None:
    """Registry key from the consumer-model token in `description`, or None.

    Usually that token is the last '_'-delimited segment before any
    '/board-info' suffix (e.g. '..._WW90DG6U25LEU4' -> 'WW90DG6U25LEU4').
    But issue #79's dryer pairs two model numbers in one description, so
    the true consumer token sits one segment before the actual last
    segment -- scan from the end and take the first segment that resolves.

    Splits on '_' only, unlike `_board_tokens` above: widening the split to
    '-' would start reading board-family segments as consumer models (the
    dishwasher's 'ADW-WW-RTL-24-AILITE' would offer up a bare 'WW' and
    route to washer).

    Only a 2-letter prefix match, so e.g. 'WAC' (Window AC, issue #87) also
    matches 'WA' (top-load washer, issue #106) at this granularity --
    for_device_by_model() consults the board-family table first and this
    only as a fallback, so that ambiguity resolves correctly.
    """
    segments = (description or "").split("/", 1)[0].split("_")
    for segment in reversed(segments):
        key = _CONSUMER_PREFIX_TO_KEY.get(segment[:2].upper())
        if key is not None:
            return key
    return None


# /oic/d's `rt` (OCF's own device-type declaration, see registry/identity.py)
# -> registry key. The device naming its own type, no board-part guessing --
# consulted before modelNum/description.
#
# Every value must already be a key in `_REGISTRY_BY_KEY` (checked by
# `test_every_oic_type_resolves_to_a_real_registry`) -- this deliberately
# stops short of the full OCF/SmartThings vocabulary, since most of it (lights,
# locks, cameras, TVs, ...) has no registry here to point at, and
# 'oic.d.robotcleaner' names an actual robot vacuum, a different product from
# the clean/auto-empty *station* `vacuum_station` covers.
#
# `x.com.st.d.*` entries are SmartThings' own vendor extension to the OCF
# device-type vocabulary, for categories with no `oic.d.*` equivalent.
#
# `oic.d.cooktop` is deliberately absent: a TP1X_DA-KS-COOKTOP induction
# reports it, but `cooktop` and `induction_cooktop` are unrelated registries
# sharing the English word (see by_type/cooktop.py's docstring) -- the OCF
# type doesn't distinguish them, and as the primary signal it would override
# a correct `COOKTOP`/`CT` board token. No unambiguous key to point at, so no
# row.
_OIC_TYPE_TO_KEY: dict[str, str] = {
    "oic.d.airconditioner": "airconditioner",
    "oic.d.airpurifier": "air_purifier",
    "oic.d.dishwasher": "dishwasher",
    "oic.d.dryer": "dryer",
    "oic.d.microwave": "microwave",  # issue #433
    "oic.d.oven": "oven",
    "oic.d.range": "range",  # issue #324 -- oven+cooktop combo, no /information/vs/0
    "oic.d.refrigerator": "refrigerator",
    "oic.d.krefrigerator": "refrigerator",  # issue #328 -- kimchi refrigerator
    "oic.d.washer": "washer",
    "x.com.st.d.airqualitysensor": "air_monitor",
    "x.com.st.d.dehumidifier": "dehumidifier",
    "x.com.st.d.hood": "range_hood",  # AHD-WW-TP1-22-COMMON
    "x.com.st.d.stickcleaner": "vacuum_station",
    "x.com.st.d.steamcloset": "air_dresser",
    "x.com.st.d.winecellar": "refrigerator",  # issue #328 -- same TP1X_REF_21K board
}


def for_device_by_oic_type(device_types: Sequence[str]) -> DeviceRegistry | None:
    """Device-type detection from /oic/d's `rt` -- OCF's own device-type
    declaration. The primary path when a dump carries it, since the device
    names its own type. Most hardware still doesn't populate `/oic/d`
    usefully, so `for_device_by_model`/`for_device_by_resources` remain
    load-bearing for everything else.
    """
    for device_type in device_types:
        key = _OIC_TYPE_TO_KEY.get(device_type)
        if key is not None:
            return _REGISTRY_BY_KEY[key]
    return None


def for_device_by_model(model_num: str, description: str) -> DeviceRegistry | None:
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
        _board_family_key(model_num, "|")
        or _board_family_key(description, "/")
        or _consumer_model_key(description)
    )
    return _REGISTRY_BY_KEY.get(key) if key else None


def for_device_by_resources(resources: dict[str, dict]) -> DeviceRegistry | None:
    """Detect a device family from a distinctive local-resource signature.

    Runs first as an override path for non-standard devices -- not because
    resource signatures are more trustworthy than OIC/model metadata, but
    because it also types boards with no ``/information/vs/0`` at all.
    Some newer cooktops were the original case: their mode resource still
    identifies them via a DeviceType option and multiple per-burner
    OperationState options.

    Every signature here requires two independent shapes, never one, so
    running this ahead of OIC/model metadata can't let a common resource
    misclassify an unrelated family.
    """
    mode = resources.get("/mode/vs/0", {})
    options = mode.get("x.com.samsung.da.options") or ()
    has_device_type = any(
        isinstance(option, str) and option.startswith("DeviceType_") for option in options
    )
    operation_states = sum(
        1 for option in options if isinstance(option, str) and option.startswith("OperationState")
    )
    if has_device_type and operation_states >= 2:
        return _REGISTRY_BY_KEY["cooktop"]
    if "/hood/fanspeed/vs/0" in resources and "/hood/lamp/vs/0" in resources:
        return _REGISTRY_BY_KEY["range_hood"]
    # Oven/range/microwave boards that report no /information/vs/0 at all
    # (issues #74, #172) can't be matched via modelNum tokens either. Mode
    # vocabulary alongside the oven cavity resource (/oven/vs/0) is a safe
    # two-resource signature; it also corrects Qooker's generic oic.d.oven
    # metadata (PR #225) since resource detection runs before it.
    supported_modes = mode.get("x.com.samsung.da.supportedModes") or ()
    if not isinstance(supported_modes, (list, tuple)):
        supported_modes = ()
    cavity = resources.get("/oven/vs/0")
    if isinstance(cavity, dict):
        if any(
            m in supported_modes for m in ("MicroWave", "MicroWaveGrill", "MicroWaveConvection")
        ):
            return _REGISTRY_BY_KEY["microwave"]
        if "Bake" in supported_modes:
            if "/cooktopmonitoring/vs/0" in resources or "/cooktop/status/vs/0" in resources:
                return _REGISTRY_BY_KEY["range"]
            return _REGISTRY_BY_KEY["oven"]
    return None


def resolve(
    resources: dict[str, dict],
    device_types: Sequence[str] = (),
) -> DeviceRegistry | None:
    """Device type for a parsed /device/0 dump, or None if unrecognized.

    The single entry point for detection -- the coordinator, the config
    flow's probe and the golden-regression harness all call this, so the
    order can't drift between what ships and what the tests assert.

    Distinctive resource signatures run first, since they describe the
    live capability surface a registry must bind; `for_device_by_resources`
    is deliberately strict (multiple independent details required) so this
    can correct misleading metadata without a common href overriding an
    unrelated family. When no signature matches, `/oic/d`'s `rt` wins over
    model-string parsing.

    `/otninformation/vs/0`'s oneUiVersion is deliberately not consulted:
    only a minority of hardware populates it, every device that does is
    already typed by its modelNum board token, and no device-support issue
    has ever needed it. Still reported in diagnostics as a firmware
    marker.
    """
    info = resources.get("/information/vs/0", {})
    return (
        for_device_by_resources(resources)
        or for_device_by_oic_type(device_types)
        or for_device_by_model(
            info.get("x.com.samsung.da.modelNum", ""),
            info.get("x.com.samsung.da.description", ""),
        )
    )
