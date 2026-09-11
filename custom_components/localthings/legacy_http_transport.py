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
* **A cycle is accepted only together with `Operation.state`.** Writes are
  therefore composed into one aggregate `PUT /devices/0` body -- see
  `legacy_http.to_write` -- rather than one request per resource.

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
from collections.abc import Sequence
from typing import Any

from .legacy_http import (
    FAMILIES,
    TP6X_WASHER,
    Resource,
    http_status_to_coap,
    start_only_fields,
    to_resources,
    to_write,
)
from .legacy_http_tls import client_context

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
        family: str | tuple[Resource, ...] = TP6X_WASHER,
    ) -> None:
        self._host = host
        self._port = port
        self._cert_pem = cert_pem
        self._key_pem = key_pem
        self._token = token
        self._table = FAMILIES[family] if isinstance(family, str) else family
        self._by_href = _index_by_href(self._table)
        self._ctx: ssl.SSLContext | None = None

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
        return 0x45, to_resources(_unwrap_one(body), self._table).get(href, {})

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
            return http_status_to_coap(status), None
        bodies = _unwrap_one(body)
        for endpoint in _LINKED_ENDPOINTS:
            linked_status, linked = self._request("GET", f"/devices/0/{endpoint}", timeout=timeout)
            if linked_status == 200 and isinstance(linked, dict):
                bodies.update(_unwrap_one(linked))
            else:
                _LOGGER.debug("%s: /devices/0/%s answered %s", self._host, endpoint, linked_status)
        resources = to_resources(bodies, self._table)
        return 0x45, [{"href": href, "rep": rep} for href, rep in resources.items()]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def write(self, path_segs: Sequence[str], body: dict, timeout: float) -> int:
        href = "/" + "/".join(path_segs)
        aggregate = to_write([(href, body)], self._table)
        if not aggregate.get("Device"):
            # Nothing in the patch belongs to a resource this family
            # serves. Refusing is the honest answer: a guessed wrapper
            # would reach the appliance as a command nobody chose.
            _LOGGER.warning("%s: no 8888 resource for %s; write dropped", self._host, href)
            return 0x84
        offenders = start_only_fields(aggregate, self._table)
        if offenders:
            # The appliance would answer 204 and drop it, which reads as a
            # write that worked. Refusing says so instead -- and 4.05 is
            # what it means: the resource is there, this write is not
            # allowed on its own.
            _LOGGER.warning(
                "%s: %s is only accepted as part of a start command on this firmware; "
                "write refused rather than silently dropped",
                self._host,
                ", ".join(offenders),
            )
            return 0x85
        status, _ = self._request("PUT", "/devices/0", body=aggregate, timeout=timeout)
        return http_status_to_coap(status)

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
        if not raw:
            return response.status, None
        try:
            return response.status, json.loads(raw)
        except ValueError:
            return response.status, raw.decode("utf-8", "replace")


def _index_by_href(table: tuple[Resource, ...]) -> dict[str, Resource]:
    """Canonical href -> the resource serving it, fanned-out hrefs included,
    so the two directions can never name different endpoints."""
    index = {resource.href: resource for resource in table}
    for resource in table:
        for href in resource.fan_out.values():
            index[href] = resource
    return index


def _unwrap_one(body: dict) -> dict[str, Any]:
    from .legacy_http import unwrap

    return unwrap(body)
