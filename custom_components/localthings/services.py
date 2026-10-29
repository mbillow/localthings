"""Home Assistant services: `start_cooking` (issue #473), and direct OCF
resource read/write access (issue #300) -- a raw-transport escape hatch for
reverse-engineering a device's write contract, as an ordered multi-write
sequence with settle delays and a delayed re-read, which the single-write
options-flow debug panel can't express. Both raw services sit on the same
coordinator primitives the panel now calls too (config_flow.py), so there
is exactly one code path that performs a raw write. `observe_resource`
(issue #607) is the same kind of escape hatch for OBSERVE: it listens to one
resource for a while, to find fields a board only pushes.

Kept thin on purpose: session/lock ownership lives on the coordinator
(coordinator.py). This module only resolves the service call's device
target to a `(coordinator, subdevice)` pair, translates canonical hrefs
through that subdevice, and shapes the response.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, cast

import voluptuous as vol
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError, Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    DOMAIN,
    SERVICE_OBSERVE_RESOURCE,
    SERVICE_READ_RESOURCE,
    SERVICE_START_COOKING,
    SERVICE_WRITE_RESOURCE,
)
from .coordinator import LocalThingsCoordinator, normalize_href, split_href_query
from .registry.capabilities import cook
from .registry.encode import json_safe
from .registry.subdevices import MAIN, Subdevice

ATTR_HREF = "href"
ATTR_PAYLOAD = "payload"
ATTR_SETTLE = "settle"
ATTR_READBACK = "readback"
ATTR_WRITES = "writes"
ATTR_VERIFY_AFTER = "verify_after"
ATTR_HOLD_SESSION_LOCK = "hold_session_lock"
ATTR_DEVICE_ID = "device_id"
ATTR_MODE = "mode"
ATTR_TEMPERATURE = "temperature"
ATTR_DURATION = "duration"

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

_READ_RESOURCE_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Optional(ATTR_HREF): str,
    }
)

# The duration's range is checked on the coordinator, like write_resource's.
_OBSERVE_RESOURCE_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_HREF): str,
        vol.Optional(ATTR_DURATION, default=30.0): vol.Coerce(float),
    }
)

_START_COOKING_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Optional(ATTR_MODE): cv.string,
        vol.Optional(ATTR_TEMPERATURE): vol.Coerce(float),
        vol.Optional(ATTR_DURATION): cv.positive_time_period,
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

    # A `?a=b&c=d` suffix becomes URI-Query options, e.g. `if=oic.if.b` to
    # ask a /device/<n> Collection for its batch rather than its links.
    path, query = split_href_query(href)
    # Same normalize-before-translate order as the write path above.
    canonical = normalize_href(path)
    actual_href = subdevice.to_actual(canonical)
    code, rep, body = await coordinator.async_raw_read(actual_href, query)
    read_result: dict[str, Any] = {
        "href": canonical,
        "actual_href": actual_href,
        "code": f"{code >> 5}.{code & 0x1F:02d}",
        "raw_code": code,
        "rep": rep,
    }
    if query:
        read_result["query"] = list(query)
    # `body` only when it isn't the Property map already in `rep` -- a
    # Collection (`/device/0`, `/sec/devices`) answers a CBOR list, which
    # `rep` can't carry and which used to vanish into an empty-looking
    # 2.05 (issue #335). Omitted for the ordinary map case rather than
    # duplicating every rep in every response.
    if body is not None and not isinstance(body, dict):
        read_result["body"] = body
    return cast(ServiceResponse, json_safe(read_result))


async def _async_observe_resource(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator, subdevice, _device_id = _resolve_target(hass, call)
    path, query = split_href_query(call.data[ATTR_HREF])
    canonical = normalize_href(path)
    actual_href = subdevice.to_actual(canonical)
    observed = await coordinator.async_observe_resource(
        actual_href, query, call.data[ATTR_DURATION]
    )
    code, rep, body = observed["get"]
    get_fields = set(rep)
    pushed_fields = {
        key for _t, body in observed["notifications"] if isinstance(body, dict) for key in body
    }
    result: dict[str, Any] = {
        "href": canonical,
        "actual_href": actual_href,
        "get": {"code": f"{code >> 5}.{code & 0x1F:02d}", "rep": rep},
        "notifications": [{"t": t, "rep": body} for t, body in observed["notifications"]],
        "fields_only_in_notifications": sorted(pushed_fields - get_fields),
        "already_observed": observed["already_observed"],
        "session_dropped": observed["session_dropped"],
    }
    # A Collection answers a list, which `rep` can't carry (issue #335).
    if body is not None and not isinstance(body, dict):
        result["get"]["body"] = body
    if query:
        result["query"] = list(query)
    if observed["truncated"]:
        result["truncated"] = True
    return cast(ServiceResponse, json_safe(result))


async def _async_start_cooking(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator, subdevice, _device_id = _resolve_target(hass, call)
    temperature = call.data.get(ATTR_TEMPERATURE)
    if temperature is not None:
        # Given in Home Assistant's unit, as the setpoint entity shows it;
        # the oven takes its own.
        unit = cook.device_unit(coordinator.device_resources(subdevice))
        if unit is not None:
            temperature = TemperatureConverter.convert(
                temperature,
                hass.config.units.temperature_unit,
                UnitOfTemperature.CELSIUS if unit == "Celsius" else UnitOfTemperature.FAHRENHEIT,
            )
    duration = call.data.get(ATTR_DURATION)
    await coordinator.async_start_cooking(
        subdevice,
        mode=call.data.get(ATTR_MODE),
        temperature=temperature,
        duration=int(duration.total_seconds()) if duration is not None else None,
    )


def _admin_only(
    hass: HomeAssistant, handler: Callable[[ServiceCall], Coroutine[Any, Any, ServiceResponse]]
) -> Callable[[ServiceCall], Coroutine[Any, Any, ServiceResponse]]:
    """Refuse a non-admin user, as HA's async_register_admin_service does.

    Not that helper itself: it only takes supports_response from HA 2025.6,
    and hacs.json still allows 2025.1. A call with no user, such as the
    options-flow debug panel's, passes.
    """

    async def _checked(call: ServiceCall) -> ServiceResponse:
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None:
                raise UnknownUser(context=call.context)
            if not user.is_admin:
                raise Unauthorized(context=call.context)
        return await handler(call)

    return _checked


def async_setup_services(hass: HomeAssistant) -> None:
    """Register start_cooking (issue #473), write_resource/read_resource
    (issue #300) and observe_resource (issue #607).

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

    async def _handle_observe(call: ServiceCall) -> ServiceResponse:
        return await _async_observe_resource(hass, call)

    async def _handle_start_cooking(call: ServiceCall) -> None:
        await _async_start_cooking(hass, call)

    # The raw actions are admin-only: a write skips the Remote Control
    # block and every entity check, and a read returns resources entities
    # never expose.
    hass.services.async_register(
        DOMAIN,
        SERVICE_WRITE_RESOURCE,
        _admin_only(hass, _handle_write),
        schema=_WRITE_RESOURCE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_READ_RESOURCE,
        _admin_only(hass, _handle_read),
        schema=_READ_RESOURCE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_OBSERVE_RESOURCE,
        _admin_only(hass, _handle_observe),
        schema=_OBSERVE_RESOURCE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_START_COOKING,
        _handle_start_cooking,
        schema=_START_COOKING_SCHEMA,
    )
