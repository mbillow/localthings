"""Envelope translation for the legacy 8888/HTTPS appliance family (issue #168).

Some 2018-2022 Samsung appliances have nothing on UDP 49152-49160 and only
TCP 8888: nginx over TLS 1.0 with a mandatory client certificate, serving a
small REST bridge instead of an OCF server. The OCF URL space is not merely
unauthorised there, it is absent -- ``/oic/res``, ``/oic/d`` and
``/mode/vs/0`` all come back as nginx's own HTML 404, while an unknown path
*inside* the served space answers ``{"errorCode": "0", "errorDescription":
"Invalid resource"}`` from the application.

What that bridge serves, though, is the same vocabulary in a different
envelope: the same ``options``/``supportedOptions`` arrays, the same
``Course_5C`` tokens, the same single-token merge semantics on writes. Only
the spelling differs -- bare field names instead of ``x.com.samsung.da.*``,
and ``/devices/0/<resource>`` instead of ``/<resource>/vs/0``.

So this module is a table and four pure functions over it. Given one of
those appliances' responses it produces exactly the ``{href: rep}`` shape
``registry.batch.parse_device0_batch`` produces, which is what lets
``registry/``, ``discovery.py`` and ``adapter.py`` work on such a device
unchanged -- the expensive part, and the part this repository has already
solved.

Deliberately pure: no I/O, no Home Assistant, no session. Nothing imports it
at runtime yet; it is the half of issue #168's proposal that can be reviewed
and tested without a transport existing, and without any of the coordinator
churn a transport would need. See that issue for the staging.

Two rules carry the whole translation, and both are mechanical:

* a resource's fields take the ``x.com.samsung.da.`` prefix and keep their
  names;
* a resource maps onto the canonical href whose rep those fields belong in.

Everything that is *not* mechanical lives in the table as data, so an
appliance that disagrees costs a row rather than a branch. On the one
family measured so far that is three exceptions: ``Operation`` carries
power and the child lock alongside the operational state, ``Information``
spells two fields differently, and ``Alarms`` arrives as a bare list where
the OCF side carries an ``items`` array.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

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


# TP6X_WW6500 (EU), the one 8888 appliance measured end to end. Every row
# here was read off the hardware, not inferred: the codes in `Mode` are the
# same `Course_XX` tokens /course/vs/0 carries -- note it is *not*
# /mode/vs/0, which this firmware 404s -- and the fields in `Washer` are
# the same waterTemperature/rinseCycles/spinLevel this repository's washer
# registry already binds.
TP6X_WASHER: tuple[Resource, ...] = (
    Resource(
        endpoint="operation",
        wrapper="Operation",
        href="/operational/state/vs/0",
        # This firmware lumps the power state and the child lock in with
        # the operational state; on the OCF side they are their own
        # resources, and this repository's capabilities read them there.
        fan_out={"power": "/power/vs/0", "kidsLock": "/kidslock/vs/0"},
    ),
    Resource(endpoint="mode", wrapper="Mode", href="/course/vs/0"),
    Resource(endpoint="washer", wrapper="Washer", href="/washer/vs/0"),
    Resource(
        endpoint="configuration",
        wrapper="Configuration",
        href="/remotectrl/vs/0",
    ),
    Resource(
        endpoint="information",
        wrapper="Information",
        href="/information/vs/0",
        rename={"modelID": "modelNum", "serialNumber": "serialNum"},
    ),
    Resource(endpoint="diagnosis", wrapper="Diagnosis", href="/diagnosis/vs/0"),
    Resource(endpoint="alarms", wrapper="Alarms", href="/alarms/vs/0", as_items=True),
)


# Keyed by the appliance's own `description` (/devices/0/information's, e.g.
# 'TP6X_WASHER'). One family so far; a second one either fits this table or
# shows exactly where it doesn't, which is the point of keeping the
# translation declarative.
FAMILIES: dict[str, tuple[Resource, ...]] = {"TP6X_WASHER": TP6X_WASHER}


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


def unwrap(*responses: Mapping[str, Any]) -> dict[str, Any]:
    """The appliance's responses -> the wrapper-keyed mapping to_resources takes.

    Three shapes turn up and all three are handled here rather than by every
    caller: the aggregate ``{"Device": {...}}`` (``GET /devices/0``), its
    plural ``{"Devices": [{...}]}`` (``GET /devices``), and a single
    resource's own ``{"Configuration": {...}}``.

    The aggregate carries more than resources -- ``connected``, ``id``,
    ``name``, ``description``, ``resources``, a ``ConfigurationLink`` and
    an ``InformationLink`` to the two it does not embed, and an
    ``EnergyConsumption`` holding only a file path. None of those has a
    canonical href, so none has a table row and all are dropped by
    ``to_resources``. They are left in here rather than filtered, so a
    board carrying something in one of them is visible to a caller that
    goes looking.
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


def to_resources(
    bodies: Mapping[str, Any],
    table: tuple[Resource, ...] = TP6X_WASHER,
) -> dict[str, dict]:
    """8888 responses -> ``{canonical href: rep}``.

    ``bodies`` is keyed by wrapper name, which is how the appliance itself
    reports them: ``GET /devices/0`` answers ``{"Device": {"Operation":
    {...}, "Washer": {...}, ...}}``, and the two resources that aggregate
    carries only links to answer ``{"Configuration": {...}}`` and
    ``{"Information": {...}}`` on their own endpoints. A caller therefore
    merges what it read and hands the wrapper-keyed mapping here.

    A wrapper the appliance didn't report is simply absent from the result,
    the same as an href a ``/device/0`` batch didn't carry -- callers
    already treat that as "this board doesn't have it" rather than as an
    empty rep (see ``registry.batch.is_stub_rep`` for why the distinction
    matters).
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
            href = resource.fan_out.get(name, resource.href)
            out.setdefault(href, {})[_canonical_name(name, resource.rename)] = _canonical_value(
                value, resource.rename
            )
    return out


def _wire_field(href: str, name: str, table: tuple[Resource, ...]) -> tuple[str, str] | None:
    """``(wrapper, wire field name)`` for one canonical field, or None when
    this table has nowhere to put it."""
    bare = name[len(PREFIX) :] if name.startswith(PREFIX) else name
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
    steps: list[tuple[str, Mapping[str, Any]]],
    table: tuple[Resource, ...] = TP6X_WASHER,
) -> dict[str, Any]:
    """``[(canonical href, canonical patch), ...]`` -> one aggregate body.

    Several steps coalesce into a single ``PUT /devices/0`` rather than
    becoming several requests, because on this firmware some writes are
    only accepted together: the washer takes a cycle **only** in the same
    body as ``Operation.state``. A ``Course_`` token sent on its own is
    answered ``204`` and discarded in every shape tried -- including a
    two-token body alongside ``LaundryOutTime``, which applied the other
    token in the same call, so it is a firmware rule about that field
    rather than a syntax problem.

    That is not exotic to this transport: ``async_raw_write_sequence``
    exists in this repository (issue #300) because a wall oven discards
    settings writes while idle and keeps them only once a cycle is running.
    Same fact, different family, over CoAP.

    Returns the body only; the endpoint is always ``/devices/0``. The
    alternative -- a bare body on the resource endpoint, e.g.
    ``PUT /devices/0/mode {"options": [...]}`` -- is what two other public
    clients for this port use, but it has never been tested here, so
    nothing in this module produces it.
    """
    device: dict[str, dict] = {}
    for href, patch in steps:
        for name, value in patch.items():
            target = _wire_field(href, name, table)
            if target is None:
                continue
            wrapper, wire_name = target
            device.setdefault(wrapper, {})[wire_name] = value
    return {"Device": device}


# HTTP status -> CoAP response code, so everything above the transport
# keeps speaking one vocabulary: _coap_accepted, _coap_code_str, the
# diagnostics dump and the debug services all work unchanged.
#
# 204 mapping onto 2.04 Changed is exact in both senses, including the
# unwelcome one: on this family a 204 means the request was accepted, not
# that anything changed -- and a CoAP 2.04 means no more than that either
# (an ARTIK051 air conditioner answers 2.04 to `FilterTime_0`, echoes the
# token, and has discarded it a poll later). The read-back in
# _raw_write_blocking is the right answer on both transports.
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
    """A CoAP response code for an HTTP status.

    Anything unmapped becomes 5.00 or 4.00 by class, so an unfamiliar
    status still reads as a failure of the right kind rather than as a
    success.
    """
    mapped = _HTTP_TO_COAP.get(status)
    if mapped is not None:
        return mapped
    if status >= 500:
        return 0xA0  # 5.00 Internal Server Error
    if status >= 400:
        return 0x80  # 4.00 Bad Request
    return 0x45
