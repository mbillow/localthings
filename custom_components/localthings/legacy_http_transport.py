"""The 8888/HTTPS transport for the legacy appliance family (issue #168).

`Transport` over the bridge these appliances serve instead of OCF, with
`legacy_http` doing the envelope translation. What the firmware forces:

* Its responses aren't valid HTTP (`X-API-Version : v1.0.0`, a space before
  the colon), which `aiohttp` rejects; `http.client` tolerates it.
* TLS 1.0 only (see legacy_http_tls). No OBSERVE, so the coordinator polls.
* A cycle and the washer's settings are taken only in the same body as a
  start; sent alone they are answered `204` and discarded. Choosing one is
  held here and shown on reads, and the Start button's write carries it.
* The appliance leaves the network soon after going idle unless Remote
  Control is on, and answers `403 SHE-001` while it is off -- both read as
  an ordinary outage.
* A sibling device answers at `/devices/<n>` and reads as the indexed
  subdevice OCF boards present. `/devices` lists it only while it is in use:
  the Flex Duo's lower cavity drops out with the divider (#572).
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import ssl
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .legacy_http import (
    TP6X_RAC,
    Resource,
    StagedKey,
    add_staged,
    batch_steps,
    course_table,
    has_cavities,
    http_status_to_coap,
    is_mapped,
    is_start,
    model_settings,
    split_start_only,
    staged_current,
    table_for,
    to_resources,
    to_write,
    unmapped_wrappers,
    unwrap,
    with_cavity_modes,
    with_staged,
)
from .legacy_http_tls import client_context
from .registry.capabilities.laundry import (
    OPTION_KIND_RINSE,
    OPTION_KIND_SPIN,
    OPTION_KIND_WATER_TEMPERATURE,
    course_option_mask,
)
from .registry.subdevices import Subdevice
from .transport import AuthRejected

_LOGGER = logging.getLogger(__name__)

# The bridge's own port. Unlike the DTLS range there is nothing to sweep:
# this is the only port these appliances open.
LEGACY_HTTP_PORT = 8888

# The coordinator's whole-device sweep; assembled here, since this firmware
# has no Collection resource.
_SEED_HREF = "/device/0"

# Served by their own endpoint rather than embedded in the aggregate.
_LINKED_ENDPOINTS = ("configuration", "information")

# Where a sibling carries whether `/devices` lists it -- for a lower cavity,
# whether the divider is in, as the OCF dual-cavity oven reports it (#324).
_LISTED_HREF = "/connected/vs/0"
_LISTED_FIELD = "x.com.samsung.da.connected"

# A sibling's record may extend its information endpoint's description with
# a suffix (`LCD_OV_WALL_16K_DIV`, #572); only such a suffix is carried over,
# since the record's description can otherwise carry the serial.
_RECORD_SUFFIX = re.compile(r"_[A-Z]+")

# Every request carries it; the appliance answers 401 without one.
_AUTH_HEADER = "Authorization"

# How long a start is given to take before its state is read back. The
# composed start has been measured loading the programme without running it.
_START_SETTLE_S = 3.0

_RUN = {"Device": {"Operation": {"state": "Run"}}}
# What the stop button sends; see _already_ready.
_READY = {"Device": {"Operation": {"state": "Ready"}}}

# The option-token prefix a course is held under (see legacy_http.StagedKey).
_COURSE_PREFIX = "Course"

# The washer settings a course's supportedOptions record states defaults for.
_WASHER_WRAPPER = "Washer"
_COURSE_DEFAULTS = (
    ("waterTemperature", OPTION_KIND_WATER_TEMPERATURE),
    ("rinseCycles", OPTION_KIND_RINSE),
    ("spinLevel", OPTION_KIND_SPIN),
)


@dataclass
class _ApplianceState:
    """What must outlive one transport object: the coordinator replaces its
    transport on every reconnect, and a held cycle has to survive that."""

    # The last sweep's bodies as the appliance sent them, before translation.
    last_bodies: dict[str, Any] = field(default_factory=dict)
    # Values held for the next start, each with what the appliance reported
    # for it when it was chosen (None until first read).
    staged: dict[StagedKey, tuple[Any, Any]] = field(default_factory=dict)
    # For a sibling: whether `/devices` listed it at the last read.
    listed: bool | None = None


# Keyed by (host, port, device index).
_STATE: dict[tuple[str, int, int], _ApplianceState] = {}


def _device_path(index: int) -> str:
    return f"/devices/{index}"


class LegacyHttpTransport:
    """`Transport` over the 8888 bridge. Blocking, like the seam it implements."""

    supports_observe = False

    def __init__(
        self,
        host: str,
        port: int,
        *,
        cert_pem: str,
        key_pem: str,
        token: str,
        family: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._cert_pem = cert_pem
        self._key_pem = key_pem
        self._token = token
        self._family = family or ""
        self._table = table_for(family)
        self._by_href = _index_by_href(self._table)
        self._ctx: ssl.SSLContext | None = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    # The appliance closes the connection after every response, so there is
    # no session to hold: "connect" is only the TLS context.

    def connect(self) -> None:
        self._ctx = client_context(self._cert_pem, self._key_pem)

    def close(self) -> None:
        self._ctx = None

    def pace(self) -> None:
        """Nothing to pace: each request is its own connection."""

    def _state_at(self, index: int) -> _ApplianceState:
        return _STATE.setdefault((self._host, self._port, index), _ApplianceState())

    def _locate(self, href: str) -> tuple[int, str]:
        """The device index an href names and its canonical form. A sibling
        device's hrefs carry its index in place of the trailing 0, as on the
        OCF boards (registry/subdevices.py's indexed pattern)."""
        head, sep, tail = href.rpartition("/")
        if tail.isdigit() and int(tail) > 0:
            return int(tail), f"{head}{sep}0"
        return 0, href

    def read(self, path_segs: Sequence[str], timeout: float) -> tuple[int, Any]:
        index, href = self._locate("/" + "/".join(path_segs))
        if href == _SEED_HREF:
            return self._read_seed(index, timeout)
        if index and href == _LISTED_HREF:
            code, devices = self._list_devices(timeout)
            if devices is None:
                self._state_at(index).listed = None
                return code, None
            return 0x45, self._listed_rep(index, devices)
        resource = self._by_href.get(href)
        if resource is None:
            # Including /oic/p and /oic/d, which nginx answers with HTML.
            return 0x84, None
        path = f"{_device_path(index)}/{resource.endpoint}"
        status, body = self._request("GET", path, timeout=timeout)
        if status != 200 or not isinstance(body, dict):
            return http_status_to_coap(status), None
        bodies = self._with_modes(index, self._with_staged(index, unwrap(body)))
        return 0x45, to_resources(bodies, self._table).get(href, {})

    def _read_seed(self, index: int, timeout: float) -> tuple[int, Any]:
        """The whole device in the shape a /device/<n> batch arrives in: the
        aggregate plus the two resources it only links to. A linked resource
        that fails is left out rather than failing the sweep.

        A sibling's requests share `timeout` between them: setup probes
        /device/1 and /device/2 on every appliance, within one budget."""
        if index:
            deadline = time.monotonic() + timeout
            try:
                code, record, devices = self._sibling_record(index, deadline)
            except TimeoutError:
                return 0xA4, None  # 5.04: the budget ran out
            if record is None:
                return code, None
        else:
            deadline = None
            status, body = self._request("GET", _device_path(0), timeout=timeout)
            if status != 200 or not isinstance(body, dict):
                return http_status_to_coap(status), body if isinstance(body, str) else None
            record, devices = unwrap(body), None
            if has_cavities(self._family):
                # Whether the divider is in decides which modes the upper
                # cavity offers (with_cavity_modes).
                _, listing = self._list_devices(timeout)
                if listing is not None:
                    self._listed_rep(1, listing)
        base = _device_path(index)
        bodies = dict(record)
        for endpoint in _LINKED_ENDPOINTS:
            try:
                linked_timeout = timeout if deadline is None else _remaining(deadline)
            except TimeoutError:
                _LOGGER.debug("%s: no time left for %s's linked resources", self._host, base)
                break
            linked_status, linked = self._request(
                "GET", f"{base}/{endpoint}", timeout=linked_timeout
            )
            if linked_status == 200 and isinstance(linked, dict):
                bodies.update(unwrap(linked))
            else:
                _LOGGER.debug("%s: %s/%s answered %s", self._host, base, endpoint, linked_status)
        if index:
            bodies = _with_record_description(bodies, record.get("description"))
        self._state_at(index).last_bodies = bodies
        resources = to_resources(
            self._with_modes(index, self._with_staged(index, bodies)), self._table
        )
        if index:
            if devices is not None:
                resources[_LISTED_HREF] = self._listed_rep(index, devices)
            sibling = Subdevice(kind="indexed", key=str(index), seed_path=("device", str(index)))
            resources = {sibling.to_actual(href): rep for href, rep in resources.items()}
        else:
            # Not served by this family; see legacy_http.FAMILY_COURSE_TABLES.
            resources.update(course_table(self._family))
            # Nor is /wm/setinfo/vs/0; the power flag rides in modelID instead.
            resources.update(model_settings(self._family, bodies))
        return 0x45, [{"href": href, "rep": rep} for href, rep in resources.items()]

    def _sibling_record(
        self, index: int, deadline: float
    ) -> tuple[int, dict[str, Any] | None, list | None]:
        """A sibling's own record, from `/devices` while it lists it and from
        `/devices/<n>` otherwise, with the `/devices` list (None when that
        read failed, so whether it is listed is unknown). A record naming
        another id is refused, so a bridge echoing device 0 at any index
        can't produce a phantom sibling."""
        _, devices = self._list_devices(_remaining(deadline))
        if devices is None:
            self._state_at(index).listed = None
        record = next(
            (d for d in devices or () if isinstance(d, dict) and d.get("id") == str(index)),
            None,
        )
        if record is None:
            path = _device_path(index)
            status, body = self._request("GET", path, timeout=_remaining(deadline))
            if status != 200 or not isinstance(body, dict):
                return http_status_to_coap(status), None, devices
            record = unwrap(body)
            if record.get("id") != str(index):
                return 0x84, None, devices
        return 0x45, record, devices

    def _list_devices(self, timeout: float) -> tuple[int, list | None]:
        """`/devices`'s list, or None with the code its failure reads as."""
        status, body = self._request("GET", "/devices", timeout=timeout)
        if status != 200:
            return http_status_to_coap(status), None
        devices = body.get("Devices") if isinstance(body, dict) else None
        if not isinstance(devices, list):
            return 0xA2, None  # 5.02: the bridge answered, with no device list
        return 0x45, devices

    def _listed_rep(self, index: int, devices: list) -> dict[str, str]:
        listed = any(isinstance(d, dict) and d.get("id") == str(index) for d in devices)
        self._state_at(index).listed = listed
        return {_LISTED_FIELD: "On" if listed else "Off"}

    def write(self, path_segs: Sequence[str], body: dict | list, timeout: float) -> tuple[int, Any]:
        index, href = self._locate("/" + "/".join(path_segs))
        state = self._state_at(index)
        if index and state.listed is False:
            # Untested what the appliance does with it; the Flex Duo still
            # answers /devices/1 with the divider out (#572).
            return 0x83, f"The appliance does not list device {index} now"
        steps: list[tuple[str, Mapping[str, Any]]] | None
        if isinstance(body, list):
            # A cook start's Collection batch (#473) is the same coalesced
            # write the aggregate PUT already takes, so it goes as one body
            # (#572). The RAC's writes are per endpoint, with no aggregate.
            if href != _SEED_HREF or self._table is TP6X_RAC:
                _LOGGER.warning("%s: no batch writes to %s over 8888", self._host, href)
                return 0x85, None
            batch = self._canonical_batch(index, body)
            steps = None if batch is None else batch_steps(batch, self._table)
            if steps is None:
                hrefs = [e.get("href") for e in body if isinstance(e, dict)]
                _LOGGER.warning(
                    "%s: no 8888 resource for all of %s; batch refused", self._host, hrefs
                )
                return 0x84, None
        else:
            steps = [(href, body)]
        aggregate = to_write(steps, self._table)
        if not aggregate.get("Device"):
            # A guessed wrapper would reach the appliance as a command nobody
            # chose, so a patch this family has no resource for is refused.
            _LOGGER.warning("%s: no 8888 resource for %s; write dropped", self._host, href)
            return 0x84, None
        sendable, staged = split_start_only(aggregate, self._table)
        if any(prefix == _COURSE_PREFIX for _, _, prefix in staged):
            # A new course brings its own settings, on the panel as well, so
            # one held for the previous course would be sent to a course that
            # may not take it.
            for key in [k for k in state.staged if k[2] is None and k not in staged]:
                del state.staged[key]
        for key, value in staged.items():
            state.staged[key] = (value, staged_current(state.last_bodies, key))
            _LOGGER.info("%s: %s held until the next start", self._host, value)
        if is_start(sendable) and state.staged and self._idle(index):
            return self._start(index, sendable, timeout)
        if not sendable["Device"]:
            return 0x44, None
        if sendable == _READY and self._already_ready(index, timeout):
            _LOGGER.debug("%s: already idle; stop not sent", self._host)
            return 0x44, None
        target = self._write_target(index, href, sendable)
        if target is None:
            _LOGGER.warning("%s: invalid 8888 write shape for %s", self._host, href)
            return 0x80, None
        path, payload = target

        # The body is the appliance's own account of a refusal (`"Control
        # fail, <...>"`), so it travels back with the code.
        status, response = self._request("PUT", path, body=payload, timeout=timeout)
        return http_status_to_coap(status), response

    def _canonical_batch(self, index: int, batch: list) -> list | None:
        """A batch's element hrefs in canonical form, or None when one names
        another device's resource: it would land on the wrong cavity."""
        out = []
        for element in batch:
            href = element.get("href") if isinstance(element, dict) else None
            if isinstance(href, str) and element.get("rep") is not None:
                element_index, canonical = self._locate(href)
                if element_index != index:
                    return None
                element = {**element, "href": canonical}
            out.append(element)
        return out

    def _write_target(
        self, index: int, href: str, aggregate: dict[str, Any]
    ) -> tuple[str, dict[str, Any]] | None:
        """The HTTP path and body for one translated write.

        Washers take the aggregate ``{"Device": ...}`` envelope at
        ``/devices/0``; TP6X_RAC takes Operation there unwrapped, and Mode,
        Wind and one Temperatures item on their own endpoints (confirmed on
        TP6X_RAC_16K hardware, #576).
        """
        if self._table is not TP6X_RAC:
            return _device_path(index), aggregate

        resource = self._by_href.get(href)
        if resource is None:
            return None

        device = aggregate.get("Device")
        if not isinstance(device, dict):
            return None

        wire = device.get(resource.wrapper)

        if resource.endpoint == "operation":
            if not isinstance(wire, dict):
                return None
            return _device_path(index), {resource.wrapper: wire}

        if resource.endpoint == "temperatures":
            if not isinstance(wire, list) or len(wire) != 1 or not isinstance(wire[0], dict):
                return None
            item = dict(wire[0])
            item_id = item.pop("id", None)
            if item_id is None:
                return None
            return f"{_device_path(index)}/temperatures/{item_id}", item

        if resource.endpoint in {"mode", "wind"}:
            if not isinstance(wire, dict):
                return None
            return f"{_device_path(index)}/{resource.endpoint}", wire

        # No direct-write behavior has been confirmed for the remaining
        # TP6X_RAC resources. Preserve the existing aggregate path rather
        # than guessing.
        return _device_path(index), aggregate

    def _idle(self, index: int) -> bool:
        operation = self._state_at(index).last_bodies.get("Operation") or {}
        return operation.get("state") == "Ready"

    def _already_ready(self, index: int, timeout: float) -> bool:
        """Whether a fresh read says the appliance is idle.

        Measured on a TP6X_WW6500: `Ready` from `Run` cancels the cycle,
        but on an idle appliance it is no no-op -- it moves it to `Pause`
        and resets temperature, rinses and spin to the course's defaults,
        throwing away what was dialled in at the panel. Read fresh rather
        than from the last sweep, which can be a poll interval old.
        """
        return self._operation_state(index, timeout) == "Ready"

    def _start(self, index: int, aggregate: dict[str, Any], timeout: float) -> tuple[int, Any]:
        """Start with every held value in the same body -- the only way this
        firmware takes them -- then make sure it actually runs.

        Measured on a TP6X_WW6500: the composed body loads the programme and
        leaves the appliance in `Ready`; a plain `Run` afterwards starts it.
        That second `Run` is sent only when a fresh read says it isn't
        running.
        """
        held = self._state_at(index).staged
        base = _device_path(index)
        body = add_staged(aggregate, {key: value for key, (value, _) in held.items()})
        status, response = self._request("PUT", base, body=body, timeout=timeout)
        if not 200 <= status < 300:
            return http_status_to_coap(status), response
        held.clear()
        time.sleep(_START_SETTLE_S)
        if self._running(index, timeout):
            return http_status_to_coap(status), response
        _LOGGER.debug("%s: programme loaded but not running; sending Run", self._host)
        run_status, run_response = self._request("PUT", base, body=_RUN, timeout=timeout)
        if not 200 <= run_status < 300 and self._running(index, timeout):
            # It got there by itself between the read and the Run.
            return http_status_to_coap(status), response
        return http_status_to_coap(run_status), run_response

    def _running(self, index: int, timeout: float) -> bool:
        return self._operation_state(index, timeout) == "Run"

    def _operation_state(self, index: int, timeout: float) -> str | None:
        """A fresh read of the device's Operation state, None if unreadable."""
        status, body = self._request("GET", f"{_device_path(index)}/operation", timeout=timeout)
        operation = unwrap(body).get("Operation") if isinstance(body, dict) else None
        return operation.get("state") if status == 200 and isinstance(operation, dict) else None

    def _with_staged(self, index: int, bodies: dict[str, Any]) -> dict[str, Any]:
        """`bodies` as the next start would leave them.

        A held value is dropped once the appliance reports something else
        for it than when it was chosen (the dial was turned), and all of them
        once it is no longer idle -- the appliance's own choice wins.
        """
        state = self._state_at(index)
        operation = bodies.get("Operation")
        if isinstance(operation, dict) and operation.get("state") not in (None, "Ready"):
            state.staged.clear()
        for key, (value, base) in list(state.staged.items()):
            if key[0] not in bodies:
                continue
            current = staged_current(bodies, key)
            if base is None:
                state.staged[key] = (value, current)
            elif current != base:
                _LOGGER.info(
                    "%s: %s changed at the appliance; dropping %s", self._host, key[1], value
                )
                del state.staged[key]
        held = with_staged(bodies, {key: value for key, (value, _) in state.staged.items()})
        return self._with_course_defaults(state, held)

    def _with_modes(self, index: int, bodies: dict[str, Any]) -> dict[str, Any]:
        divided = True if index else self._state_at(1).listed
        return with_cavity_modes(self._family, bodies, index, divided)

    def _with_course_defaults(
        self, state: _ApplianceState, bodies: dict[str, Any]
    ) -> dict[str, Any]:
        """A held course reads with its own default settings, as the panel
        does when the dial turns, for every setting not held itself.

        Shown only: the start leaves them out and the appliance applies its
        own. Measured on a WW6500 -- the composed Drum Clean start came up
        at 60C/2/400, the defaults its record states.
        """
        staged = state.staged
        if not any(prefix == _COURSE_PREFIX for _, _, prefix in staged):
            return bodies
        washer = bodies.get(_WASHER_WRAPPER)
        if not isinstance(washer, dict):
            return bodies
        # A single-resource read carries no Mode; the course's record is the
        # last sweep's, with the held course in place.
        context = with_staged(
            {**state.last_bodies, **bodies},
            {key: value for key, (value, _) in staged.items()},
        )
        resources = to_resources(context, self._table)
        washer = dict(washer)
        for name, kind in _COURSE_DEFAULTS:
            if (_WASHER_WRAPPER, name, None) in staged:
                continue
            mask = course_option_mask(resources, kind)
            supported = washer.get(f"supported{name[0].upper()}{name[1:]}")
            if mask is None or not isinstance(supported, list):
                continue
            default, allowed = mask
            if default >= len(supported):
                continue
            # A course with no such setting allows nothing and points its
            # default at the list's "None" -- Rinse+Spin's temperature, which
            # the appliance itself reports as "None" once it runs. Without
            # this the held course would show the previous course's value.
            if default in allowed or (not allowed and supported[default] == "None"):
                washer[name] = supported[default]
        return {**bodies, _WASHER_WRAPPER: washer}

    def diagnostics(self) -> dict[str, Any]:
        # Only devices whose record was read: a state is also created for
        # any index a write or a rejected probe names.
        states = {
            index: state
            for (host, port, index), state in _STATE.items()
            if (host, port) == (self._host, self._port) and (state.last_bodies or not index)
        }
        main = states.get(0, _ApplianceState()).last_bodies
        out: dict[str, Any] = {
            "transport": "legacy_http",
            "family": self._family,
            "family_mapped": is_mapped(self._family),
            "unmapped_resources": sorted(
                {
                    wrapper
                    for state in states.values()
                    for wrapper in unmapped_wrappers(state.last_bodies, self._table)
                }
            ),
            "bodies": _resource_bodies(main),
        }
        siblings = {index: state for index, state in states.items() if index}
        if siblings:
            out["device_bodies"] = {
                str(index): _resource_bodies(state.last_bodies)
                for index, state in sorted(siblings.items())
            }
        return out

    def subscribe(self, path_segs: Sequence[str]) -> Any:
        raise NotImplementedError("the 8888 bridge has no OBSERVE")

    def refresh_observes(self, paths: Sequence[Sequence[str]]) -> None:
        raise NotImplementedError("the 8888 bridge has no OBSERVE")

    def _request(
        self, method: str, path: str, *, body: dict | None = None, timeout: float
    ) -> tuple[int, Any]:
        """One request/response. Raises for anything below HTTP itself, so
        the coordinator's existing "the device is not answering" handling
        applies unchanged; an HTTP status the appliance did answer with is
        returned rather than raised."""
        if self._ctx is None:
            raise RuntimeError("no session")
        payload = None if body is None else json.dumps(body).encode()
        headers = {_AUTH_HEADER: f"Bearer {self._token}", "Accept": "*/*"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPSConnection(
            self._host, self._port, context=self._ctx, timeout=timeout
        )
        try:
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
        finally:
            conn.close()
        if response.status == 401:
            raise AuthRejected(f"{self._host} refused the device token")
        if not raw:
            return response.status, None
        try:
            body = json.loads(raw)
        except ValueError:
            return response.status, raw.decode("utf-8", "replace")
        if response.status == 403 and isinstance(body, dict) and body.get("errorCode") == "SHE-001":
            # What every request gets while Remote Control is off at the
            # panel -- the everyday case, so it reads as a reason, not a code.
            return response.status, "Remote Control is off at the appliance"
        if response.status >= 400 and isinstance(body, dict):
            # The bridge's `Control fail, <...>`, as text: the shape the DTLS
            # boards give it in (transport.Transport).
            description = body.get("errorDescription")
            if isinstance(description, str) and description:
                return response.status, description
        return response.status, body


def _remaining(deadline: float) -> float:
    """What is left of a shared deadline; spent reads as a timeout."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("the request budget is spent")
    return left


def _with_record_description(bodies: dict[str, Any], description: Any) -> dict[str, Any]:
    """`bodies` with a sibling record's description on its Information, when
    it is that Information's own description plus a suffix (_RECORD_SUFFIX)."""
    information = bodies.get("Information")
    if not isinstance(information, dict):
        return bodies
    own = information.get("description")
    if not (isinstance(own, str) and isinstance(description, str)):
        return bodies
    if not (description.startswith(own) and _RECORD_SUFFIX.fullmatch(description[len(own) :])):
        return bodies
    return {**bodies, "Information": {**information, "description": description}}


def _resource_bodies(bodies: Mapping[str, Any]) -> dict[str, Any]:
    """Resource bodies only: the aggregate's own scalars include a
    `description` that can carry the serial and a user-set `name`."""
    return {
        key: value
        for key, value in bodies.items()
        if isinstance(value, (dict, list)) or key == "type"
    }


def _index_by_href(table: tuple[Resource, ...]) -> dict[str, Resource]:
    """Canonical href -> the resource serving it, fanned-out hrefs included,
    so the two directions can never name different endpoints."""
    index = {resource.href: resource for resource in table}
    for resource in table:
        for href in resource.fan_out.values():
            index[href] = resource
    return index
