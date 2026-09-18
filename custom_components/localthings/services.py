"""Home Assistant services for direct OCF resource read/write access
(issue #300): a raw-transport escape hatch for reverse-engineering a
device's write contract -- an ordered multi-write sequence with settle
delays and a delayed re-read, which the single-write options-flow debug
panel can't express. Both sit on the same coordinator primitives the panel
now calls too (config_flow.py), so there is exactly one code path that
performs a raw write.

Kept thin on purpose: session/lock ownership lives on the coordinator
(coordinator.py). This module only resolves the service call's device
target to a `(coordinator, subdevice)` pair, translates canonical hrefs
through that subdevice, and shapes the response.
"""

from __future__ import annotations

from typing import Any, cast

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    SERVICE_READ_RESOURCE,
    SERVICE_START_CYCLE,
    SERVICE_WRITE_RESOURCE,
)
from .coordinator import LocalThingsCoordinator, normalize_href
from .registry.encode import json_safe
from .registry.subdevices import MAIN, Subdevice

ATTR_HREF = "href"
ATTR_PAYLOAD = "payload"
ATTR_SETTLE = "settle"
ATTR_READBACK = "readback"
ATTR_WRITES = "writes"
ATTR_VERIFY_AFTER = "verify_after"
ATTR_CYCLE = "cycle"
# The three settings this firmware also takes only alongside a start,
# mapped to the canonical field each belongs to.
_SETTING_FIELDS = {
    "water_temperature": "x.com.samsung.da.waterTemperature",
    "rinse_cycles": "x.com.samsung.da.rinseCycles",
    "spin_level": "x.com.samsung.da.spinLevel",
}
ATTR_HOLD_SESSION_LOCK = "hold_session_lock"
ATTR_DEVICE_ID = "device_id"

_WRITE_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_HREF): str,
        # Not `dict` here: a non-dict payload must fail the same way an
        # empty one does -- coordinator.async_raw_write_sequence's
        # ServiceValidationError -- not a raw schema vol.Invalid, so every
        # caller sees one consistent error shape regardless of which rule
        # a bad payload tripped.
        vol.Required(ATTR_PAYLOAD): object,
        vol.Optional(ATTR_SETTLE): vol.Coerce(float),
        vol.Optional(ATTR_READBACK): cv.boolean,
    }
)

# Structural validation only (types, and unwrapping a bare dict into a
# one-item list) -- the semantic checks (non-empty payload, non-root href,
# the 1..10/settle/verify_after ranges) live on
# LocalThingsCoordinator.async_raw_write_sequence, so every caller gets the
# same ServiceValidationError + translation key regardless of whether it
# reached the primitive through this service, the options-flow panel, or a
# future caller.
_WRITE_RESOURCE_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_WRITES): vol.All(cv.ensure_list, [_WRITE_ITEM_SCHEMA]),
        vol.Optional(ATTR_VERIFY_AFTER): vol.Coerce(float),
        vol.Optional(ATTR_HOLD_SESSION_LOCK): cv.boolean,
    }
)

_START_CYCLE_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_CYCLE): str,
        **{vol.Optional(name): str for name in _SETTING_FIELDS},
    }
)

_READ_RESOURCE_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Optional(ATTR_HREF): str,
    }
)


def _resolve_target(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[LocalThingsCoordinator, Subdevice, str]:
    """The one `(coordinator, subdevice, device_id)` a service call's
    device target names.

    Deliberately strict about count, not just presence: the `target:
    device:` selector in services.yaml still lets a user pick an area or
    label in the picker, and the frontend expands that into a `device_id`
    list before the call reaches here -- more than one entry means an
    area/label fanned this out across several appliances, which a raw
    debug write must never do silently (issue #300).
    """
    device_ids = cv.ensure_list(call.data.get(ATTR_DEVICE_ID) or [])
    if len(device_ids) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="service_device_target_invalid",
        )
    device_id = device_ids[0]

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="service_device_not_found",
        )

    for coordinator in hass.data.get(DOMAIN, {}).values():
        if device.identifiers & coordinator.device_info.get("identifiers", set()):
            return coordinator, MAIN, device_id
        for sub in coordinator.subdevices:
            if device.identifiers & coordinator.device_info_for(sub).get("identifiers", set()):
                return coordinator, sub, device_id

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="service_device_not_loaded",
    )


async def _async_write_resource(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator, subdevice, device_id = _resolve_target(hass, call)
    writes_in: list[dict[str, Any]] = call.data[ATTR_WRITES]

    # Canonical -> actual translation happens here, not in the coordinator
    # (issue #177), whose raw-write primitive has no notion of subdevices --
    # identity transform for MAIN. Normalized first, or a trailing slash
    # slips past to_actual onto the master (see coordinator.normalize_href).
    canonicals = [normalize_href(w[ATTR_HREF]) for w in writes_in]
    raw_writes = [
        {
            "href": subdevice.to_actual(canonical),
            "payload": w.get(ATTR_PAYLOAD),
            "settle": w.get(ATTR_SETTLE, 0.0),
            "readback": w.get(ATTR_READBACK, True),
        }
        for canonical, w in zip(canonicals, writes_in, strict=True)
    ]
    sequence = await coordinator.async_raw_write_sequence(
        raw_writes,
        verify_after=call.data.get(ATTR_VERIFY_AFTER, 0.0),
        hold_session_lock=call.data.get(ATTR_HOLD_SESSION_LOCK, True),
    )

    results = [
        {
            "href": canonical,
            "actual_href": result["href"],
            "code": result["code"],
            "raw_code": result["raw_code"],
            "accepted": result["accepted"],
            "response_body": result["response_body"],
            "before": result["before"],
            "readback": result["readback"],
            "after": result["after"],
            "changed": result["changed"],
        }
        for canonical, result in zip(canonicals, sequence["results"], strict=True)
    ]
    response: dict[str, Any] = {"device_id": device_id, "results": results}
    if "verified" in sequence:
        # Keyed off the same normalized canonicals the sequence was built
        # from, so the lookup can't miss and return an actual href where the
        # contract promises a canonical one.
        canonical_by_actual = {subdevice.to_actual(c): c for c in canonicals}
        response["verified"] = {
            canonical_by_actual.get(actual_href, actual_href): verified
            for actual_href, verified in sequence["verified"].items()
        }
    # One json_safe at the boundary rather than per field: `before`/`after`
    # and every verified rep are whatever the appliance sent, and a service
    # response ends at a JSON encoder (see registry/encode.py).
    return cast(ServiceResponse, json_safe(response))


async def _async_read_resource(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator, subdevice, _device_id = _resolve_target(hass, call)
    href = call.data.get(ATTR_HREF)
    if not href:
        # No href -> the cached snapshot, not a live sweep of every known
        # href: lets a user enumerate what exists without hammering the
        # device (see this module's docstring and the coordinator's
        # canonical_resources).
        # device_resources, not canonical_resources: this response is what
        # the appliance reported, without the fields this integration merges
        # on for its own use (see coordinator.entity_resources).
        snapshot: dict[str, Any] = {"resources": coordinator.device_resources(subdevice)}
        return cast(ServiceResponse, json_safe(snapshot))

    # Same normalize-before-translate order as the write path above.
    canonical = normalize_href(href)
    actual_href = subdevice.to_actual(canonical)
    code, rep, body = await coordinator.async_raw_read(actual_href)
    read_result: dict[str, Any] = {
        "href": canonical,
        "actual_href": actual_href,
        "code": f"{code >> 5}.{code & 0x1F:02d}",
        "raw_code": code,
        "rep": rep,
    }
    # `body` only when it isn't the Property map already in `rep` -- a
    # Collection (`/device/0`, `/sec/devices`) answers a CBOR list, which
    # `rep` can't carry and which used to vanish into an empty-looking
    # 2.05 (issue #335). Omitted for the ordinary map case rather than
    # duplicating every rep in every response.
    if body is not None and not isinstance(body, dict):
        read_result["body"] = body
    return cast(ServiceResponse, json_safe(read_result))


async def _async_start_cycle(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Programme and start, as one write.

    The cycle is always sent, never left to the dial: a service that
    started whatever the panel happened to be set to would be a Start
    button with extra steps, and the whole reason this exists is that the
    two cannot be sent apart on this firmware.

    Settings are sent only when given. The appliance applies a programme's
    own defaults to anything absent, and a start body carrying settings has
    been measured to load the programme and then *not* run it on at least
    one board -- so the fewer fields, the closer this is to the shape known
    to work.
    """
    coordinator, subdevice, _device_id = _resolve_target(hass, call)

    cycle = call.data[ATTR_CYCLE].strip()
    if not cycle:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="start_cycle_needs_a_cycle"
        )

    def _segs(canonical: str) -> list[str]:
        """Canonical href -> the path segments this device actually serves.

        Same two hops the write_resource service takes: normalize, then
        translate through the subdevice, so a composite aimed at a
        subdevice lands there rather than slipping onto the master.
        """
        return subdevice.to_actual(normalize_href(canonical)).strip("/").split("/")

    steps: list[tuple[list[str], dict]] = [
        (_segs("/course/vs/0"), {"x.com.samsung.da.options": [f"Course_{cycle.upper()}"]})
    ]
    settings = {
        field: call.data[name] for name, field in _SETTING_FIELDS.items() if name in call.data
    }
    if settings:
        steps.append((_segs("/washer/vs/0"), settings))
    steps.append((_segs("/operational/state/vs/0"), {"x.com.samsung.da.state": "Run"}))

    result = await coordinator.async_write_composite(steps, action=SERVICE_START_CYCLE)

    # Measured on a TP6X_WW6500: the composite loads the programme -- the
    # appliance switches to it and reports its duration -- and then does not
    # run it, leaving `Ready`. A plain `Operation.state = Run` on its own
    # afterwards does start it, which is the same write the Start button
    # sends and is known to work from `Ready`.
    #
    # Sent only when the device itself says it isn't running, never blind:
    # a second Run against an appliance that did start is a write nobody
    # asked for. `async_write_composite` has already refreshed, so the
    # cache below is post-write.
    state_href = normalize_href("/operational/state/vs/0")
    started = coordinator.entity_rep(subdevice.to_actual(state_href)).get("x.com.samsung.da.state")
    if started is not None and started != "Run":
        result = await coordinator.async_write_composite(
            [(_segs("/operational/state/vs/0"), {"x.com.samsung.da.state": "Run"})],
            action=f"{SERVICE_START_CYCLE} (follow-up run)",
        )

    return cast(
        ServiceResponse, {"code": result.get("code"), "response": json_safe(result.get("body"))}
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the write_resource/read_resource services (issue #300).

    Called once from `async_setup`, not per config entry: services are
    process-global, and `hass.services.async_register` on an
    already-registered name just replaces the handler, so re-registering
    on every entry setup would silently rebind to whichever entry loaded
    last. `async_unload_entry` must never call the inverse of this.
    """

    async def _handle_write(call: ServiceCall) -> ServiceResponse:
        return await _async_write_resource(hass, call)

    async def _handle_read(call: ServiceCall) -> ServiceResponse:
        return await _async_read_resource(hass, call)

    async def _handle_start_cycle(call: ServiceCall) -> ServiceResponse:
        return await _async_start_cycle(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_WRITE_RESOURCE,
        _handle_write,
        schema=_WRITE_RESOURCE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_READ_RESOURCE,
        _handle_read,
        schema=_READ_RESOURCE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_START_CYCLE,
        _handle_start_cycle,
        schema=_START_CYCLE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
