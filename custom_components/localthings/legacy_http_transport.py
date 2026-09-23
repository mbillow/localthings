"""The 8888/HTTPS transport for the legacy appliance family (issue #168).

A 2018-2022 Samsung appliance has no OCF server: nothing answers on UDP
49152-49160, and TCP 8888 serves a small REST bridge instead -- nginx over
TLS 1.0, a mandatory client certificate, and a device token. `legacy_http`
translates that envelope into the reps the registry already reads; this is
the transport that fetches them, implementing the same `Transport` protocol
`DtlsTransport` does, so the coordinator, the registry and the entity layer
need no notion of which one they are talking to.

Four things about this firmware shape the code, all measured rather than
assumed:

* **The response is not valid HTTP.** It emits `X-API-Version : v1.0.0`,
  with a space before the colon; `aiohttp` rejects the entire response over
  it. `http.client` is lenient about header lines, which is why this is
  written on top of it -- and it suits the seam, which is blocking and runs
  in an executor anyway.
* **TLS 1.0 only**, and a modern OpenSSL needs `DEFAULT@SECLEVEL=0` to
  speak to it at all.
* **There is no OBSERVE.** The bridge is request/response, so this
  transport declares `supports_observe = False` and the coordinator stays
  on its poll cadence.
* **A cycle and the washer's settings are accepted only with a start.**
  Sent alone they are answered `204` and discarded. So choosing one holds
  it here, reads show the held value, and the Start button's write carries
  everything held in one `PUT /devices/0`.

The appliance is also absent by design: it leaves the network minutes after
going idle unless Remote Control is on, and answers `403 SHE-001` to every
request while that is switched off. Both surface as ordinary failed reads,
which the coordinator already treats as an outage rather than an error.
"""

from __future__ import annotations

import http.client
import json
import logging
import ssl
import time
from collections.abc import Sequence
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

# The href the coordinator polls for a whole-device sweep. This firmware has
# no Collection resource; the aggregate plus the two resources it only links
# to are assembled into the same batch shape `parse_device0_batch` reads.
_SEED_HREF = "/device/0"

# Served by their own endpoint rather than embedded in the aggregate.
_LINKED_ENDPOINTS = ("configuration", "information")

# Every request carries it; the appliance answers 401 without one.
_AUTH_HEADER = "Authorization"

# How long a start is given to take before its state is read back. The
# composed start has been measured loading the programme without running it.
_START_SETTLE_S = 3.0

_RUN = {"Device": {"Operation": {"state": "Run"}}}


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
        # The last sweep's bodies as the appliance sent them, before
        # translation -- what diagnostics needs to map a family that isn't.
        self._last_bodies: dict[str, Any] = {}
        # Values held for the next start, each with what the appliance
        # reported for it when it was chosen (None until first read).
        self._staged: dict[StagedKey, tuple[Any, Any]] = {}

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    # ------------------------------------------------------------------
    # Lifetime. There is no session to hold: the appliance closes the
    # connection after every response (`Connection: close` is its own
    # behavior, not a choice here), so each request stands alone and
    # "connect" is only the TLS context.
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._ctx = client_context(self._cert_pem, self._key_pem)

    def close(self) -> None:
        self._ctx = None

    def pace(self) -> None:
        """Nothing to pace: each request opens its own connection, and the
        rate limiter the DTLS session needs has no counterpart here."""

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def read(self, path_segs: Sequence[str], timeout: float) -> tuple[int, Any]:
        href = "/" + "/".join(path_segs)
        if href == _SEED_HREF:
            return self._read_seed(timeout)
        resource = self._by_href.get(href)
        if resource is None:
            # Every OCF href this firmware does not serve, including
            # /oic/p and /oic/d -- nginx answers its own HTML 404 for
            # those, and read_identity is defensive about it.
            return 0x84, None
        status, body = self._request("GET", f"/devices/0/{resource.endpoint}", timeout=timeout)
        if status != 200 or not isinstance(body, dict):
            return http_status_to_coap(status), None
        bodies = self._with_staged(unwrap(body))
        return 0x45, to_resources(bodies, self._table).get(href, {})

    def _read_seed(self, timeout: float) -> tuple[int, Any]:
        """The whole device in the shape a /device/0 batch arrives in.

        Three requests, not one per resource: the aggregate carries
        Operation, Washer, Mode, Alarms and Diagnosis together, and only
        Configuration and Information need fetching separately. A linked
        resource that fails is left out rather than failing the sweep --
        the same posture the DTLS path takes for a resource that goes
        quiet.
        """
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
        self._last_bodies = bodies
        resources = to_resources(self._with_staged(bodies), self._table)
        # Not served by this family; see legacy_http.FAMILY_COURSE_TABLES.
        resources.update(course_table(self._family))
        return 0x45, [{"href": href, "rep": rep} for href, rep in resources.items()]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

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
        for key, value in staged.items():
            self._staged[key] = (value, staged_current(self._last_bodies, key))
            _LOGGER.info("%s: %s held until the next start", self._host, value)
        if is_start(sendable) and self._staged and self._idle():
            return self._start(sendable, timeout)
        if not sendable["Device"]:
            return 0x44, None
        # The body is the appliance's own account of a refusal (`"Control
        # fail, <...>"`), so it travels back with the code.
        status, response = self._request("PUT", "/devices/0", body=sendable, timeout=timeout)
        return http_status_to_coap(status), response

    def _idle(self) -> bool:
        operation = self._last_bodies.get("Operation") or {}
        return operation.get("state") == "Ready"

    def _start(self, aggregate: dict[str, Any], timeout: float) -> tuple[int, Any]:
        """Start with every held value in the same body -- the only way this
        firmware takes them -- then make sure it actually runs.

        Measured on a TP6X_WW6500: the composed body loads the programme and
        leaves the appliance in `Ready`; a plain `Run` afterwards starts it.
        That second `Run` is sent only when a fresh read says it isn't
        running.
        """
        body = add_staged(aggregate, {key: value for key, (value, _) in self._staged.items()})
        status, response = self._request("PUT", "/devices/0", body=body, timeout=timeout)
        if not 200 <= status < 300:
            return http_status_to_coap(status), response
        self._staged.clear()
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
            self._staged.clear()
        for key, (value, base) in list(self._staged.items()):
            if key[0] not in bodies:
                continue
            current = staged_current(bodies, key)
            if base is None:
                self._staged[key] = (value, current)
            elif current != base:
                _LOGGER.info(
                    "%s: %s changed at the appliance; dropping %s", self._host, key[1], value
                )
                del self._staged[key]
        return with_staged(bodies, {key: value for key, (value, _) in self._staged.items()})

    def diagnostics(self) -> dict[str, Any]:
        return {
            "transport": "legacy_http",
            "family": self._family,
            "family_mapped": is_mapped(self._family),
            # Resource bodies only: the aggregate's own scalars include a
            # `description` that can carry the serial and a user-set `name`.
            "bodies": {
                key: value
                for key, value in self._last_bodies.items()
                if isinstance(value, (dict, list)) or key == "type"
            },
        }

    # ------------------------------------------------------------------
    # OBSERVE -- not available on this transport; `supports_observe` says
    # so, and these exist only so the protocol is satisfied.
    # ------------------------------------------------------------------

    def subscribe(self, path_segs: Sequence[str]) -> Any:
        raise NotImplementedError("the 8888 bridge has no OBSERVE")

    def refresh_observes(self, paths: Sequence[Sequence[str]]) -> None:
        raise NotImplementedError("the 8888 bridge has no OBSERVE")

    # ------------------------------------------------------------------

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
