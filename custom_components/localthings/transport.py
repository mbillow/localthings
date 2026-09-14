"""What this integration needs from a connection to an appliance (issue #168).

The coordinator, the config flow's probe, `registry.identity` and
`registry.subdevices` all talk to a device through the same handful of
operations: connect, read a resource, write a patch to one, pace a burst of
them, close. `DtlsTransport` below is that surface over CoAP-DTLS -- the
only transport today, and the one every device this integration supports
speaks.

It exists as an interface because a 2018-2022 appliance family speaks the
same vocabulary over a different transport entirely (nginx on TCP 8888,
JSON over TLS 1.0, no OCF URL space at all), and `legacy_http` translates
that envelope into the reps the rest of this integration already reads. An
HTTP transport implementing this protocol is what would connect the two;
none is proposed here.

Two things are deliberately behind the seam rather than above it:

* **Decoding.** `read` returns the decoded body, so nothing upstream knows
  the wire is CBOR. That is what keeps a second transport from adding a
  parallel code path to every caller.
* **OBSERVE.** `subscribe`/`refresh_observes` are CoAP-specific and a
  request/response transport has no equivalent, so `supports_observe`
  says whether they mean anything. `observe.py` needs no notion of any of
  this: `ObserveRefreshTask` only ever calls `refresh_observes`, which
  this class forwards.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

import cbor2
from smartthings_local.protocol.dtls_session import DtlsCoapSession

from .const import (
    CONF_DEVICE_TOKEN,
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_LEGACY_FAMILY,
    CONF_PORT,
    CONF_TRANSPORT,
    TRANSPORT_LEGACY_HTTP,
)


class DecodeError(ValueError):
    """A response arrived, but its body could not be decoded.

    Distinct from a transport failure: the device answered. Callers that
    already tolerate a resource going quiet (a sibling subdevice, a probe
    href) treat it the same as any other failed read; the summary poll
    reports it as its own thing, since a device whose /device/0 doesn't
    decode is a different problem from one that isn't answering.
    """


class Transport(Protocol):
    """The operations every transport must provide.

    `read` returns `(code, body)` with a CoAP response code -- an HTTP
    transport maps its statuses onto the same codes (see
    `legacy_http.http_status_to_coap`), so `_coap_accepted`,
    `_coap_code_str`, diagnostics and the debug services stay transport-
    agnostic. `body` is whatever the payload decoded to: a Property map for
    a Resource, a list for a Collection, `None` for an empty payload.
    """

    supports_observe: bool

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def pace(self) -> None: ...

    def read(self, path_segs: Sequence[str], timeout: float) -> tuple[int, Any]: ...

    def write(self, path_segs: Sequence[str], body: dict, timeout: float) -> int: ...

    def subscribe(self, path_segs: Sequence[str]) -> Any: ...

    def refresh_observes(self, paths: Sequence[Sequence[str]]) -> None: ...


class DtlsTransport:
    """CoAP over DTLS -- the transport every supported device speaks.

    A thin wrapper over `DtlsCoapSession`: it owns the session's lifetime
    and the CBOR codec, and forwards everything else. Blocking throughout,
    exactly as the session it wraps is -- callers run it in an executor.
    """

    supports_observe = True

    def __init__(
        self,
        host: str,
        port: int,
        *,
        cert_pem: str,
        key_pem: str,
        on_notification: Callable[[str, bytes], None] | None = None,
        local_port: int | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._cert_pem = cert_pem
        self._key_pem = key_pem
        self._on_notification = on_notification
        self._local_port = local_port
        self._session: DtlsCoapSession | None = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    def connect(self) -> None:
        kwargs: dict[str, Any] = {"cert_pem": self._cert_pem, "key_pem": self._key_pem}
        if self._on_notification is not None:
            kwargs["on_notification"] = self._on_notification
        if self._local_port is not None:
            kwargs["local_port"] = self._local_port
        session = DtlsCoapSession(self._host, self._port, **kwargs)
        session.connect()
        session.start_reader()
        self._session = session

    def close(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            session.close()

    def _live(self) -> DtlsCoapSession:
        if self._session is None:
            raise RuntimeError("no session")
        return self._session

    def pace(self) -> None:
        self._live().pace()

    def read(self, path_segs: Sequence[str], timeout: float) -> tuple[int, Any]:
        code, payload = self._live().get(list(path_segs), timeout=timeout)
        if not payload:
            return code, None
        try:
            return code, cbor2.loads(payload)
        except Exception as err:
            raise DecodeError(str(err)) from err

    def write(self, path_segs: Sequence[str], body: dict, timeout: float) -> int:
        code, _ = self._live().post(list(path_segs), cbor2.dumps(body), timeout=timeout)
        return code

    def subscribe(self, path_segs: Sequence[str]) -> Any:
        return self._live().subscribe(list(path_segs))

    def refresh_observes(self, paths: Sequence[Sequence[str]]) -> None:
        self._live().refresh_observes(paths)


def create_transport(
    data: Mapping[str, Any],
    *,
    on_notification: Callable[[str, bytes], None] | None = None,
    local_port: int | None = None,
) -> Transport:
    """The transport an entry's appliance speaks.

    Keyed on what the config flow recorded rather than sniffed here: an
    entry that predates a second transport carries no marker at all, and
    the DTLS default is what it has always been. `legacy_http` is imported
    lazily so an install with no such device never loads it.
    """
    if data.get(CONF_TRANSPORT) == TRANSPORT_LEGACY_HTTP:
        from .legacy_http import FAMILIES, TP6X_WASHER
        from .legacy_http_transport import LegacyHttpTransport

        family = data.get(CONF_LEGACY_FAMILY)
        return LegacyHttpTransport(
            data[CONF_HOST],
            data[CONF_PORT],
            cert_pem=data[CONF_LEAF_CERT_PEM],
            key_pem=data[CONF_LEAF_KEY_PEM],
            token=data[CONF_DEVICE_TOKEN],
            family=FAMILIES.get(family, TP6X_WASHER) if family else TP6X_WASHER,
        )
    return DtlsTransport(
        data[CONF_HOST],
        data[CONF_PORT],
        cert_pem=data[CONF_LEAF_CERT_PEM],
        key_pem=data[CONF_LEAF_KEY_PEM],
        on_notification=on_notification,
        local_port=local_port,
    )
