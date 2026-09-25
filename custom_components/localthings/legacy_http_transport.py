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
"""

from __future__ import annotations

import http.client
import json
import logging
import ssl
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .legacy_http import (
    Resource,
    StagedKey,
    add_staged,
    course_table,
    http_status_to_coap,
    is_mapped,
    is_start,
    split_start_only,
    staged_current,
    table_for,
    to_resources,
    to_write,
    unwrap,
    with_staged,
)
from .legacy_http_tls import client_context
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

# Every request carries it; the appliance answers 401 without one.
_AUTH_HEADER = "Authorization"

# How long a start is given to take before its state is read back. The
# composed start has been measured loading the programme without running it.
_START_SETTLE_S = 3.0

_RUN = {"Device": {"Operation": {"state": "Run"}}}

# The option-token prefix a course is held under (see legacy_http.StagedKey).
_COURSE_PREFIX = "Course"


@dataclass
class _ApplianceState:
    """What must outlive one transport object: the coordinator replaces its
    transport on every reconnect, and a held cycle has to survive that."""

    # The last sweep's bodies as the appliance sent them, before translation.
    last_bodies: dict[str, Any] = field(default_factory=dict)
    # Values held for the next start, each with what the appliance reported
    # for it when it was chosen (None until first read).
    staged: dict[StagedKey, tuple[Any, Any]] = field(default_factory=dict)


_STATE: dict[tuple[str, int], _ApplianceState] = {}


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
        self._state = _STATE.setdefault((host, port), _ApplianceState())

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

    def read(self, path_segs: Sequence[str], timeout: float) -> tuple[int, Any]:
        href = "/" + "/".join(path_segs)
        if href == _SEED_HREF:
            return self._read_seed(timeout)
        resource = self._by_href.get(href)
        if resource is None:
            # Including /oic/p and /oic/d, which nginx answers with HTML.
            return 0x84, None
        status, body = self._request("GET", f"/devices/0/{resource.endpoint}", timeout=timeout)
        if status != 200 or not isinstance(body, dict):
            return http_status_to_coap(status), None
        bodies = self._with_staged(unwrap(body))
        return 0x45, to_resources(bodies, self._table).get(href, {})

    def _read_seed(self, timeout: float) -> tuple[int, Any]:
        """The whole device in the shape a /device/0 batch arrives in: the
        aggregate plus the two resources it only links to. A linked resource
        that fails is left out rather than failing the sweep."""
        status, body = self._request("GET", "/devices/0", timeout=timeout)
        if status != 200 or not isinstance(body, dict):
            return http_status_to_coap(status), body if isinstance(body, str) else None
        bodies = unwrap(body)
        for endpoint in _LINKED_ENDPOINTS:
            linked_status, linked = self._request("GET", f"/devices/0/{endpoint}", timeout=timeout)
            if linked_status == 200 and isinstance(linked, dict):
                bodies.update(unwrap(linked))
            else:
                _LOGGER.debug("%s: /devices/0/%s answered %s", self._host, endpoint, linked_status)
        self._state.last_bodies = bodies
        resources = to_resources(self._with_staged(bodies), self._table)
        # Not served by this family; see legacy_http.FAMILY_COURSE_TABLES.
        resources.update(course_table(self._family))
        return 0x45, [{"href": href, "rep": rep} for href, rep in resources.items()]

    def write(self, path_segs: Sequence[str], body: dict | list, timeout: float) -> tuple[int, Any]:
        href = "/" + "/".join(path_segs)
        if not isinstance(body, dict):
            # A Collection batch (issue #473) has no counterpart on a bridge
            # with no Collections.
            _LOGGER.warning("%s: no batch writes over 8888; write to %s refused", self._host, href)
            return 0x85, None
        aggregate = to_write([(href, body)], self._table)
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
            for key in [k for k in self._state.staged if k[2] is None and k not in staged]:
                del self._state.staged[key]
        for key, value in staged.items():
            self._state.staged[key] = (value, staged_current(self._state.last_bodies, key))
            _LOGGER.info("%s: %s held until the next start", self._host, value)
        if is_start(sendable) and self._state.staged and self._idle():
            return self._start(sendable, timeout)
        if not sendable["Device"]:
            return 0x44, None
        # The body is the appliance's own account of a refusal (`"Control
        # fail, <...>"`), so it travels back with the code.
        status, response = self._request("PUT", "/devices/0", body=sendable, timeout=timeout)
        return http_status_to_coap(status), response

    def _idle(self) -> bool:
        operation = self._state.last_bodies.get("Operation") or {}
        return operation.get("state") == "Ready"

    def _start(self, aggregate: dict[str, Any], timeout: float) -> tuple[int, Any]:
        """Start with every held value in the same body -- the only way this
        firmware takes them -- then make sure it actually runs.

        Measured on a TP6X_WW6500: the composed body loads the programme and
        leaves the appliance in `Ready`; a plain `Run` afterwards starts it.
        That second `Run` is sent only when a fresh read says it isn't
        running.
        """
        body = add_staged(aggregate, {key: value for key, (value, _) in self._state.staged.items()})
        status, response = self._request("PUT", "/devices/0", body=body, timeout=timeout)
        if not 200 <= status < 300:
            return http_status_to_coap(status), response
        self._state.staged.clear()
        time.sleep(_START_SETTLE_S)
        state_status, state = self._request("GET", "/devices/0/operation", timeout=timeout)
        operation = unwrap(state).get("Operation") if isinstance(state, dict) else None
        if state_status == 200 and isinstance(operation, dict) and operation.get("state") == "Run":
            return http_status_to_coap(status), response
        _LOGGER.debug("%s: programme loaded but not running; sending Run", self._host)
        status, response = self._request("PUT", "/devices/0", body=_RUN, timeout=timeout)
        return http_status_to_coap(status), response

    def _with_staged(self, bodies: dict[str, Any]) -> dict[str, Any]:
        """`bodies` as the next start would leave them.

        A held value is dropped once the appliance reports something else
        for it than when it was chosen (the dial was turned), and all of them
        once it is no longer idle -- the appliance's own choice wins.
        """
        operation = bodies.get("Operation")
        if isinstance(operation, dict) and operation.get("state") not in (None, "Ready"):
            self._state.staged.clear()
        for key, (value, base) in list(self._state.staged.items()):
            if key[0] not in bodies:
                continue
            current = staged_current(bodies, key)
            if base is None:
                self._state.staged[key] = (value, current)
            elif current != base:
                _LOGGER.info(
                    "%s: %s changed at the appliance; dropping %s", self._host, key[1], value
                )
                del self._state.staged[key]
        return with_staged(bodies, {key: value for key, (value, _) in self._state.staged.items()})

    def diagnostics(self) -> dict[str, Any]:
        return {
            "transport": "legacy_http",
            "family": self._family,
            "family_mapped": is_mapped(self._family),
            # Resource bodies only: the aggregate's own scalars include a
            # `description` that can carry the serial and a user-set `name`.
            "bodies": {
                key: value
                for key, value in self._state.last_bodies.items()
                if isinstance(value, (dict, list)) or key == "type"
            },
        }

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
        return response.status, body


def _index_by_href(table: tuple[Resource, ...]) -> dict[str, Resource]:
    """Canonical href -> the resource serving it, fanned-out hrefs included,
    so the two directions can never name different endpoints."""
    index = {resource.href: resource for resource in table}
    for resource in table:
        for href in resource.fan_out.values():
            index[href] = resource
    return index
