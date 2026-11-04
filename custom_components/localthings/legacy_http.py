"""Envelope translation for the legacy 8888/HTTPS appliance family (issue #168).

Some 2018-2022 Samsung appliances serve no OCF at all -- only a small REST
bridge on TCP 8888. It speaks the same vocabulary (the same `options` arrays
and `Course_XX` tokens) with bare field names and `/devices/0/<resource>`
paths, so this module translates it to the `{href: rep}` shape
`parse_device0_batch` produces and `registry/` works on it unchanged.

Pure: no I/O, no Home Assistant. Two mechanical rules carry the translation
-- a field takes the `x.com.samsung.da.` prefix, a resource maps onto the
canonical href its fields belong in -- and everything else is a table row.
The table is shared by every appliance; a device type that keeps a resource
elsewhere costs an override row rather than a table of its own.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from .registry.capabilities.oven import is_lcd_ov_wall_16k

# The prefix every OCF-side field carries and no 8888-side field does.
PREFIX = "x.com.samsung.da."


@dataclass(frozen=True)
class Resource:
    """One 8888 resource and where its fields belong on the OCF side.

    ``endpoint`` is the path segment under ``/devices/0/``; ``wrapper`` is
    the PascalCase key the same resource takes inside the aggregate body
    (both directions -- the appliance wraps what it reports, and expects
    the same wrapping on a write to ``/devices/0``).

    ``fan_out`` and ``rename`` are the exception tables: a field named in
    ``fan_out`` lands on that canonical href instead of ``href``, and a
    field named in ``rename`` lands under that canonical name instead of
    its own. Anything not named in either is mechanical.
    """

    endpoint: str
    wrapper: str
    href: str
    fan_out: Mapping[str, str] = field(default_factory=dict)
    rename: Mapping[str, str] = field(default_factory=dict)
    # True where the appliance serves a bare JSON list and the OCF side
    # carries it as an ``items`` array of Property maps (see /alarms/vs/0
    # on any of this repository's fixtures).
    as_items: bool = False
    # Fields this firmware accepts only in the same body as a start and
    # answers 204 to, then discards, on their own; the transport holds them
    # for the next start (see split_start_only).
    start_only: frozenset[str] = frozenset()
    # Fields the appliance reports as JSON numbers, which the OCF side
    # writes as strings; to_write sends them back as numbers.
    numeric: frozenset[str] = frozenset()
    # List fields the appliance packs into one comma-joined string, which
    # the OCF side carries one value per entry.
    joined: frozenset[str] = frozenset()


INFORMATION = Resource(
    endpoint="information",
    wrapper="Information",
    href="/information/vs/0",
    rename={"modelID": "modelNum", "serialNumber": "serialNum"},
)

# Where each resource lands on the OCF side. The wrapper names are one
# vocabulary on every 8888 appliance on record, and each field goes where the
# CoAP boards keep it: power and the child lock on their own hrefs, the rest
# of Operation on the operational state. A field this table doesn't name
# translates mechanically, so a device type nobody has mapped still reads
# whole rather than as identity alone.
RESOURCES: tuple[Resource, ...] = (
    Resource(
        endpoint="operation",
        wrapper="Operation",
        href="/operational/state/vs/0",
        fan_out={"power": "/power/vs/0", "kidsLock": "/kidslock/vs/0"},
        numeric=frozenset({"progressPercentage"}),
    ),
    Resource(
        endpoint="mode",
        wrapper="Mode",
        href="/mode/vs/0",
        joined=frozenset({"supportedModes"}),
    ),
    Resource(
        endpoint="washer",
        wrapper="Washer",
        href="/washer/vs/0",
        start_only=frozenset({"waterTemperature", "rinseCycles", "spinLevel"}),
    ),
    Resource(endpoint="oven", wrapper="Oven", href="/oven/vs/0"),
    # The DONGLE_0281 fridge's rapid-cool flag (#698), where the CoAP fridges
    # keep it. The endpoint follows the other wrappers' lowercase spelling;
    # neither it nor a write is confirmed on hardware yet.
    Resource(endpoint="fridge", wrapper="Fridge", href="/refrigeration/vs/0"),
    Resource(
        endpoint="temperatures",
        wrapper="Temperatures",
        href="/temperatures/vs/0",
        as_items=True,
        numeric=frozenset({"desired"}),
    ),
    Resource(endpoint="doors", wrapper="Doors", href="/doors/vs/0", as_items=True),
    # The ARTIK051 ACs' href: Wind is /airflow/vs/0, not /wind/strength/vs/0.
    Resource(
        endpoint="wind",
        wrapper="Wind",
        href="/airflow/vs/0",
        numeric=frozenset({"speedLevel"}),
    ),
    Resource(endpoint="configuration", wrapper="Configuration", href="/remotectrl/vs/0"),
    INFORMATION,
    Resource(endpoint="diagnosis", wrapper="Diagnosis", href="/diagnosis/vs/0"),
    Resource(endpoint="alarms", wrapper="Alarms", href="/alarms/vs/0", as_items=True),
)

# A washer's or dryer's Mode is its cycle (`Course_XX`), which CoAP laundry
# serves as /course/vs/0 and the TP6X_WW6500 takes only with a start. The
# DV80M8214AW's start behavior is unmeasured (#646), so it is held the same way.
_LAUNDRY = (
    Resource(
        endpoint="mode",
        wrapper="Mode",
        href="/course/vs/0",
        start_only=frozenset({"Course"}),
    ),
)

# A Configuration that holds only the region, as on the ARTIK051 ACs'
# /configuration/vs/0 (#563). On /remotectrl/vs/0 it would read as Remote
# Control off and block every write.
_REGION_CONFIGURATION = (
    Resource(endpoint="configuration", wrapper="Configuration", href="/configuration/vs/0"),
)

# Resources a device type keeps somewhere else, keyed by the `type` its own
# record reports.
_BY_TYPE: dict[str, tuple[Resource, ...]] = {
    "Washer": _LAUNDRY,
    "Dryer": _LAUNDRY,
    "Air_Conditioner": _REGION_CONFIGURATION,
    "Refrigerator": _REGION_CONFIGURATION,  # DONGLE_0281 (#698)
}


@cache
def table_for(device_type: str | None) -> tuple[Resource, ...]:
    """The resource table for an appliance reporting `device_type`."""
    overrides = {resource.wrapper: resource for resource in _BY_TYPE.get(device_type or "", ())}
    return tuple(overrides.get(resource.wrapper, resource) for resource in RESOURCES)


_CAPACITY = re.compile(r"_\d+K$")


def _family_key(family: str | None) -> str:
    return _CAPACITY.sub("", family or "")


# A divided oven's cavities, told apart by what each slot reports for itself:
# the lower one's record extends the board's description with `_DIV`
# (`LCD_OV_WALL_16K_DIV`, #572), and the upper one's modes name `Upper` ones.
# A `_DIV` slot reporting no modes is not a cavity; the NW9300MD has one
# beside its microwave (#681).
_LOWER_SUFFIX = "_DIV"
_UPPER_PREFIX = "Upper"
_LOWER_PREFIX = "Lower"
CAVITY_UPPER = "upper"
CAVITY_LOWER = "lower"

# The NV51K7770S (#572) repeats its upper list: LowerConvectionBake was
# captured, while Bake and Convection Roast are the reporter's reading
# of the panel. Healthycook1-4 are inferred from its UpperHealthycook1-4
# and the NW9300MD's own lower list (#681).
_ASSUMED_LOWER_MODES = (
    "LowerBake",
    "LowerConvectionBake",
    "LowerConvectionRoast",
    "LowerHealthycook1",
    "LowerHealthycook2",
    "LowerHealthycook3",
    "LowerHealthycook4",
)

_IDLE_MODE = "NoOperation"


def _supported_modes(bodies: Mapping[str, Any]) -> list[str]:
    mode = bodies.get("Mode")
    supported = _split_joined(mode.get("supportedModes")) if isinstance(mode, Mapping) else None
    return [m for m in supported if isinstance(m, str)] if isinstance(supported, list) else []


def cavity_role(record: Mapping[str, Any]) -> str | None:
    """CAVITY_UPPER or CAVITY_LOWER for a slot of a divided oven, from its
    own record, or None for anything else."""
    supported = _supported_modes(record)
    if not supported:
        return None
    description = record.get("description")
    if isinstance(description, str) and description.endswith(_LOWER_SUFFIX):
        return CAVITY_LOWER
    if any(m.startswith(_UPPER_PREFIX) for m in supported):
        return CAVITY_UPPER
    return None


def is_disconnected(record: Mapping[str, Any]) -> bool:
    """Whether a listed record says it is not connected. The NW9300MD's
    `/devices` lists its lower cavity with the divider out too, and only that
    record's own `connected` turns false (#681)."""
    connected = record.get("connected")
    if isinstance(connected, str):
        return connected.strip().lower() in ("false", "off")
    return connected is False


def divider_in(devices: list) -> bool:
    """Whether `/devices` lists a connected lower cavity: the NV51K777OS
    lists one only while the divider is in (#572), the NW9300MD lists it
    disconnected otherwise (#681)."""
    return any(
        isinstance(d, Mapping) and cavity_role(d) == CAVITY_LOWER and not is_disconnected(d)
        for d in devices
    )


def with_cavity_modes(bodies: dict[str, Any], lower: bool, divided: bool | None) -> dict[str, Any]:
    """`bodies` with `Mode.supportedModes` narrowed to what this cavity can
    run: Upper modes on the upper cavity with the divider in, unprefixed ones
    with it out, Lower modes on the lower cavity and none with the divider
    out. The upper cavity's is left as reported while the divider state is
    unknown. The mode the cavity reports is always kept, so a running cook
    still reads as one of its options."""
    mode = bodies.get("Mode")
    if not isinstance(mode, Mapping) or not isinstance(mode.get("supportedModes"), list):
        return bodies
    supported = _supported_modes(bodies)
    if lower:
        own = [m for m in supported if m.startswith(_LOWER_PREFIX)]
        modes = [_IDLE_MODE] if divided is False else [_IDLE_MODE, *(own or _ASSUMED_LOWER_MODES)]
    elif divided is None:
        return bodies
    elif divided:
        modes = [m for m in supported if m == _IDLE_MODE or m.startswith(_UPPER_PREFIX)]
    else:
        modes = [m for m in supported if not m.startswith((_UPPER_PREFIX, _LOWER_PREFIX))]
    current = mode.get("modes")
    for name in current if isinstance(current, list) else ():
        if isinstance(name, str) and name not in modes:
            modes.append(name)
    return {**bodies, "Mode": {**mode, "supportedModes": [",".join(modes)]}}


# Record types that stand in for a missing `deviceSubType`. The DONGLE_0281
# fridge bridge reports only `type: Refrigerator` (#698). Other types stay
# out: a hood slot's record says `Cooktop` (#676), and as a subtype that
# would outrank the board token that types it.
_RECORD_TYPE_AS_SUBTYPE = frozenset({"Refrigerator"})


def with_record_type(bodies: Mapping[str, Any], device_type: str | None) -> dict[str, Any]:
    """`bodies` with the record's own `type` as its Information's
    `deviceSubType`, which registry routing reads, when it names none."""
    information = bodies.get("Information")
    if (
        device_type not in _RECORD_TYPE_AS_SUBTYPE
        or not isinstance(information, Mapping)
        or information.get("deviceSubType")
    ):
        return dict(bodies)
    return {**bodies, "Information": {**information, "deviceSubType": device_type}}


def unmapped_wrappers(bodies: Mapping[str, Any], table: tuple[Resource, ...]) -> list[str]:
    """The resources the appliance reported that `table` has no row for, and
    so are not read: what a new row would have to cover. Links and the
    aggregate's own scalars are not resources."""
    named = {resource.wrapper for resource in table}
    return sorted(
        key
        for key, body in bodies.items()
        if key[:1].isupper()
        and not key.endswith("Link")
        and isinstance(body, (Mapping, list))
        and key not in named
    )


def _canonical_name(name: str, rename: Mapping[str, str]) -> str:
    return PREFIX + rename.get(name, name)


def _canonical_value(value: Any, rename: Mapping[str, str]) -> Any:
    """Prefix nested Property maps too -- an alarms entry is itself a map of
    bare field names on the wire and of prefixed ones on the OCF side."""
    if isinstance(value, Mapping):
        return {_canonical_name(k, rename): _canonical_value(v, rename) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonical_value(v, rename) for v in value]
    return value


def _split_joined(value: Any) -> Any:
    """`["a,b,a"]` -> `["a", "b"]`: split, trimmed, first occurrence kept."""
    if not isinstance(value, list):
        return value
    out: list[Any] = []
    for entry in value:
        parts = entry.split(",") if isinstance(entry, str) else [entry]
        for part in parts:
            part = part.strip() if isinstance(part, str) else part
            if part != "" and part not in out:
                out.append(part)
    return out


def unwrap(*responses: Mapping[str, Any]) -> dict[str, Any]:
    """The appliance's responses -> the wrapper-keyed mapping to_resources takes.

    Handles the aggregate `{"Device": {...}}`, its plural `{"Devices":
    [{...}]}`, and a single resource's own `{"Configuration": {...}}`. The
    aggregate's non-resource keys (`connected`, `description`, links, ...) are
    kept; to_resources drops them since no table row names them.
    """
    out: dict[str, Any] = {}
    for response in responses:
        if not isinstance(response, Mapping):
            continue
        device = response.get("Device")
        if device is None:
            devices = response.get("Devices")
            if isinstance(devices, list) and devices:
                device = devices[0]
        if isinstance(device, Mapping):
            out.update(device)
        else:
            out.update(response)
    return out


def to_resources(bodies: Mapping[str, Any], table: tuple[Resource, ...]) -> dict[str, dict]:
    """Wrapper-keyed 8888 bodies (see unwrap) -> `{canonical href: rep}`.

    A wrapper the appliance didn't report is absent from the result, the same
    as an href a `/device/0` batch didn't carry.
    """
    out: dict[str, dict] = {}
    for resource in table:
        body = bodies.get(resource.wrapper)
        if body is None:
            continue
        if resource.as_items:
            if not isinstance(body, list):
                continue
            out[resource.href] = {
                PREFIX + "items": [_canonical_value(item, resource.rename) for item in body]
            }
            continue
        if not isinstance(body, Mapping):
            continue
        for name, value in body.items():
            if name in resource.joined:
                value = _split_joined(value)
            href = resource.fan_out.get(name, resource.href)
            out.setdefault(href, {})[_canonical_name(name, resource.rename)] = _canonical_value(
                value, resource.rename
            )
    return out


# Where cycle_select reads a washer's course-table id to pick its labels.
COURSE_TABLE_HREF = "/st/washercourse/vs/0"
_COURSE_TABLE_FIELD = PREFIX + "st.courseTable"

# This family serves no /st/washercourse/vs/0, so its table is declared per
# family. TP6X_WASHER is Table_00: a dial walk through all 14 panel positions
# on a WW6500 matched the existing washer_cycle_table_00 catalog on 13 codes,
# and the fourteenth (0x6C) is Denim by elimination.
FAMILY_COURSE_TABLES: dict[str, str] = {"TP6X_WASHER": "Table_00"}


# Where model_allows_power_on_off reads whether remote power on/off works.
SETINFO_HREF = "/wm/setinfo/vs/0"
_POWER_ON_OFF_FIELD = PREFIX + "isModelSettingPowerOnOff"


# Families whose modelID feature string is the washer plugin's. The
# TP6X_RAC's carries one too, but the byte means nothing known on an AC
# (#524's units all read 00), and /wm/setinfo/vs/0 is a laundry resource.
_SETINFO_FAMILIES = frozenset({"TP6X_WASHER"})


def model_settings(family: str | None, bodies: Mapping[str, Any]) -> dict[str, dict]:
    """A `/wm/setinfo/vs/0` rep carrying the power on/off flag this family
    states in Information.modelID, or a confirmed model-specific override.
    CoAP boards serve this flag as a resource of its own.

    The third `|` field of modelID is a hex feature string. Samsung's own
    washer plugin reads remote power control from bit 0 of the byte at
    offset 26 (`isPowerControlSupportedModel`). Measured on a TP6X_WW6500,
    whose byte is 00: `Operation.power = Off` answers
    `400 Control fail, <Operation.power=Off>` and the washer stays on.
    """
    info = bodies.get("Information")
    if not isinstance(info, Mapping):
        return {}
    model_id = info.get("modelID")
    # Power writes are accepted and do nothing on this board: on both of the
    # NV51K7770S's cavities (#664, #572) and on the NW9300MD combo's oven
    # (#681). Every NW9300MD slot reports CombiOven; its microwave's power
    # is untested and keeps its switch.
    subtype = info.get("deviceSubType")
    combo_oven = subtype == "CombiOven" and not any(
        m.startswith("MicroWave") for m in _supported_modes(bodies)
    )
    if is_lcd_ov_wall_16k(model_id) and (subtype == "WallOven" or combo_oven):
        return {SETINFO_HREF: {_POWER_ON_OFF_FIELD: "false"}}
    if _family_key(family) not in _SETINFO_FAMILIES:
        return {}
    if not isinstance(model_id, str):
        return {}
    parts = model_id.split("|")
    if len(parts) < 3 or len(parts[2]) < 28:
        return {}
    try:
        flags = int(parts[2][26:28], 16)
    except ValueError:
        return {}
    return {SETINFO_HREF: {_POWER_ON_OFF_FIELD: "true" if flags & 1 else "false"}}


def course_table(family: str) -> dict[str, dict]:
    """A `/st/washercourse/vs/0` rep for `family`; empty (raw course codes)
    for a family nobody has walked the dial on."""
    table = FAMILY_COURSE_TABLES.get(family)
    if not table:
        return {}
    return {COURSE_TABLE_HREF: {_COURSE_TABLE_FIELD: table}}


# Every slot on a bridge shares one panel, so one unit. The TP6X_OV combo's
# oven labels its Temperatures "Celsius" whatever the panel shows: preheating
# to 350 F it read 350 "Celsius" (#674). Device 0 labels its own right.
def with_panel_unit(bodies: dict[str, Any], main_bodies: Mapping[str, Any]) -> dict[str, Any]:
    """A sibling's `bodies` with its Temperatures in the main unit's unit;
    unchanged until the main unit has been read."""
    unit = _first_unit(main_bodies)
    temperatures = bodies.get("Temperatures")
    if unit is None or not isinstance(temperatures, list):
        return bodies
    relabeled = [{**t, "unit": unit} if isinstance(t, dict) else t for t in temperatures]
    return {**bodies, "Temperatures": relabeled}


def _first_unit(bodies: Mapping[str, Any]) -> str | None:
    temperatures = bodies.get("Temperatures")
    first = temperatures[0] if isinstance(temperatures, list) and temperatures else None
    unit = first.get("unit") if isinstance(first, dict) else None
    return unit if isinstance(unit, str) else None


# Where the CoAP boards take a clock sync (range.RANGE_CLOCK_SYNC). Some 8888
# bridges serve it unlisted at /devices/0/configuration/time, which answers a
# GET with `{}` and a 404 where it is not served; an NQ70M6650DS's panel took
# the time from it with Smart Control off (#674), though the clock kept
# flashing until a key was pressed.
CLOCK_HREF = "/configuration/vs/0"
CLOCK_ENDPOINT = "configuration/time"
_CLOCK_FIELD = PREFIX + "currentTime"


def clock_write(href: str, body: Mapping[str, Any]) -> dict[str, Any] | None:
    """The CLOCK_ENDPOINT body for a clock sync, or None for any other write."""
    if href != CLOCK_HREF or set(body) != {_CLOCK_FIELD}:
        return None
    return {"Time": {"currentTime": body[_CLOCK_FIELD]}}


def _bare(name: str) -> str:
    return name[len(PREFIX) :] if name.startswith(PREFIX) else name


def _wire_value(value: Any, numeric: frozenset[str], name: str = "") -> Any:
    """One field's value as the appliance spells it: bare names inside any
    nested map, and numbers where it reports numbers."""
    if isinstance(value, Mapping):
        return {_bare(k): _wire_value(v, numeric, _bare(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_wire_value(v, numeric) for v in value]
    if name in numeric and isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return value
        return int(number) if number.is_integer() else number
    return value


def _wire_field(href: str, name: str, table: tuple[Resource, ...]) -> tuple[str, str] | None:
    """``(wrapper, wire field name)`` for one canonical field, or None when
    this table has nowhere to put it."""
    bare = _bare(name)
    for resource in table:
        for wire_name, fan_href in resource.fan_out.items():
            if fan_href == href and wire_name == bare:
                return resource.wrapper, wire_name
        if resource.href != href:
            continue
        for wire_name, canonical in resource.rename.items():
            if canonical == bare:
                return resource.wrapper, wire_name
        return resource.wrapper, bare
    return None


def to_write(
    steps: list[tuple[str, Mapping[str, Any]]], table: tuple[Resource, ...]
) -> dict[str, Any]:
    """`[(canonical href, canonical patch), ...]` -> one `PUT /devices/0` body.

    Always the aggregate endpoint: a bare body on a resource endpoint
    (`PUT /devices/0/mode`) has never been tested on this hardware.
    """
    device: dict[str, Any] = {}
    items = {resource.href: resource for resource in table if resource.as_items}
    for href, patch in steps:
        listed = items.get(href)
        for name, value in patch.items():
            if listed is not None:
                # The appliance's list is the wrapper's whole body, so the
                # items go there as they are and nothing else on the href does.
                if name == PREFIX + "items" and isinstance(value, list):
                    device[listed.wrapper] = _wire_value(value, listed.numeric)
                continue
            target = _wire_field(href, name, table)
            if target is None:
                continue
            wrapper, wire_name = target
            resource = next(r for r in table if r.wrapper == wrapper)
            device.setdefault(wrapper, {})[wire_name] = _wire_value(
                value, resource.numeric, wire_name
            )
    return {"Device": device}


def batch_steps(
    batch: list, table: tuple[Resource, ...]
) -> list[tuple[str, Mapping[str, Any]]] | None:
    """A Collection batch's elements as to_write steps, or None when any one
    can't be translated whole: a cook start that lost its mode would still
    carry its `Run`. The bare `/devices/0` marker carries no rep and is
    skipped."""
    steps: list[tuple[str, Mapping[str, Any]]] = []
    for element in batch:
        if not isinstance(element, Mapping) or not isinstance(element.get("href"), str):
            return None
        href, rep = element["href"], element.get("rep")
        if rep is None:
            continue
        if not isinstance(rep, Mapping) or not to_write([(href, rep)], table)["Device"]:
            return None
        steps.append((href, rep))
    return steps


# One value held for a start: (wrapper, wire field, option-token prefix). The
# prefix is set for a token inside an `options` array (the cycle's
# `Course_XX`) and None for a plain field (the washer's temperature).
StagedKey = tuple[str, str, str | None]


def split_start_only(
    aggregate: Mapping[str, Any], table: tuple[Resource, ...]
) -> tuple[dict[str, Any], dict[StagedKey, Any]]:
    """Split a wire body into what can be sent now and what only a start
    carries.

    This family takes a cycle and the washer's settings only in the same
    body as `Operation.state = Run`; sent alone they are answered `204` and
    discarded (measured on a TP6X_WW6500, including in a body whose other
    token did apply). Returns `(sendable body, {key: value})`.
    """
    by_wrapper = {resource.wrapper: resource for resource in table}
    device: dict[str, Any] = {}
    staged: dict[StagedKey, Any] = {}
    for wrapper, fields in (aggregate.get("Device") or {}).items():
        if not isinstance(fields, Mapping):
            device[wrapper] = fields
            continue
        resource = by_wrapper.get(wrapper)
        start_only = resource.start_only if resource is not None else frozenset()
        for name, value in fields.items():
            if name in start_only:
                staged[(wrapper, name, None)] = value
                continue
            if isinstance(value, list):
                kept = []
                for token in value:
                    prefix = token.split("_", 1)[0] if isinstance(token, str) else None
                    if prefix in start_only:
                        staged[(wrapper, name, prefix)] = token
                    else:
                        kept.append(token)
                if not kept:
                    continue
                value = kept
            device.setdefault(wrapper, {})[name] = value
    return {"Device": device}, staged


def staged_current(bodies: Mapping[str, Any], key: StagedKey) -> Any:
    """What the appliance itself reports for a staged key, or None."""
    wrapper, name, prefix = key
    fields = bodies.get(wrapper)
    if not isinstance(fields, Mapping):
        return None
    value = fields.get(name)
    if prefix is None:
        return value
    if isinstance(value, list):
        for token in value:
            if isinstance(token, str) and token.split("_", 1)[0] == prefix:
                return token
    return None


def with_staged(bodies: Mapping[str, Any], staged: Mapping[StagedKey, Any]) -> dict[str, Any]:
    """`bodies` (wrapper-keyed, the appliance's own spelling) with every
    staged value in place of the reported one."""
    out = {k: (dict(v) if isinstance(v, Mapping) else v) for k, v in bodies.items()}
    for (wrapper, name, prefix), value in staged.items():
        fields = out.get(wrapper)
        if not isinstance(fields, dict):
            continue
        if prefix is None:
            fields[name] = value
            continue
        tokens = fields.get(name)
        tokens = list(tokens) if isinstance(tokens, list) else []
        rest = [t for t in tokens if not (isinstance(t, str) and t.split("_", 1)[0] == prefix)]
        fields[name] = [*rest, value]
    return out


def add_staged(aggregate: Mapping[str, Any], staged: Mapping[StagedKey, Any]) -> dict[str, Any]:
    """A start body carrying every staged value alongside what it already
    had. Tokens go in as single-token writes, the merge this firmware
    applies to an `options` array."""
    device = {
        k: dict(v) if isinstance(v, Mapping) else v
        for k, v in (aggregate.get("Device") or {}).items()
    }
    for (wrapper, name, prefix), value in staged.items():
        fields = device.setdefault(wrapper, {})
        if prefix is None:
            fields[name] = value
        else:
            fields[name] = [*fields.get(name, []), value]
    return {"Device": device}


def is_start(aggregate: Mapping[str, Any]) -> bool:
    operation = (aggregate.get("Device") or {}).get("Operation") or {}
    return operation.get("state") == "Run"


# HTTP status -> CoAP response code, so everything above the transport keeps
# one vocabulary. 204 -> 2.04 is exact in the unwelcome sense too: accepted,
# not necessarily applied, on either transport.
_HTTP_TO_COAP: dict[int, int] = {
    200: 0x45,  # 2.05 Content
    201: 0x41,  # 2.01 Created
    204: 0x44,  # 2.04 Changed
    400: 0x80,  # 4.00 Bad Request
    401: 0x81,  # 4.01 Unauthorized
    403: 0x83,  # 4.03 Forbidden
    404: 0x84,  # 4.04 Not Found
    405: 0x85,  # 4.05 Method Not Allowed
}


def http_status_to_coap(status: int) -> int:
    """A CoAP response code for an HTTP status; an unmapped one keeps its
    class, and anything outside 2xx reads as a failure."""
    mapped = _HTTP_TO_COAP.get(status)
    if mapped is not None:
        return mapped
    if 200 <= status < 300:
        return 0x44  # 2.04 Changed -- accepted, nothing more claimed
    if status >= 500:
        return 0xA0  # 5.00 Internal Server Error
    return 0x80  # 4.00 Bad Request
