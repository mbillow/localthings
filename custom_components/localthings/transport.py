"""What this integration needs from a connection to an appliance (issue #168).

The coordinator, the config flow's probe, `registry.identity` and
`registry.subdevices` reach a device only through `Transport`: connect, read,
write, pace, close, and OBSERVE where the transport has it. `DtlsTransport`
is CoAP-DTLS; `legacy_http_transport.LegacyHttpTransport` is the 8888 bridge.

Decoding sits behind the seam, so nothing above it knows the wire format,
and `supports_observe` says whether `subscribe`/`refresh_observes` mean
anything.
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
    """A success response arrived, but its body could not be decoded.

    Distinct from a transport failure: the device answered. Carries the
    response code and the raw payload so a debug read can still report what
    arrived.
    """

    def __init__(self, message: str, *, code: int, payload: bytes) -> None:
        super().__init__(message)
        self.code = code
        self.payload = payload


class AuthRejected(Exception):
    """The device refused this entry's credentials outright -- the 8888
    bridge's 401 for a device token it no longer accepts. Only new
    credentials help, so the coordinator raises Home Assistant's reauth
    rather than retrying."""


def _is_success(code: int) -> bool:
    """A 2.xx response class -- the only one whose body is CBOR by contract.
    An error response often carries a plain diagnostic string instead."""
    return code >> 5 == 2


class Transport(Protocol):
    """The operations every transport must provide.

    `read` returns `(code, body)` with a CoAP response code -- an HTTP
    transport maps its statuses onto the same codes (see
    `legacy_http.http_status_to_coap`), so `_coap_accepted`,
    `_coap_code_str`, diagnostics and the debug services stay transport-
    agnostic. `body` is whatever the payload decoded to: a Property map for
    a Resource, a list for a Collection, `None` for an empty payload. An
    error response's body is left undecoded.

    `write` returns the same pair. Most boards answer a write with an empty
    payload, but the laundry firmware puts its reason for refusing one in
    the response body (`"Control fail, <...>"`), so a transport that dropped
    it would throw away the only account the device gives of its own refusal.
    """

    supports_observe: bool

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def pace(self) -> None: ...

    def read(self, path_segs: Sequence[str], timeout: float) -> tuple[int, Any]: ...

    def write(
        self, path_segs: Sequence[str], body: dict | list, timeout: float
    ) -> tuple[int, Any]: ...

    def subscribe(self, path_segs: Sequence[str]) -> Any: ...

    def refresh_observes(self, paths: Sequence[Sequence[str]]) -> None: ...

    def diagnostics(self) -> dict[str, Any]: ...


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
        if not _is_success(code):
            return code, payload
        try:
            return code, cbor2.loads(payload)
        except Exception as err:
            raise DecodeError(str(err), code=code, payload=payload) from err

    def write(self, path_segs: Sequence[str], body: dict | list, timeout: float) -> tuple[int, Any]:
        code, payload = self._live().post(list(path_segs), cbor2.dumps(body), timeout=timeout)
        if not payload:
            return code, None
        # Never raised: the write has already been sent, and an exception
        # here would reach the caller's reconnect-and-retry and send it twice.
        try:
            return code, cbor2.loads(payload)
        except Exception:
            return code, payload

    def subscribe(self, path_segs: Sequence[str]) -> Any:
        return self._live().subscribe(list(path_segs))

    def refresh_observes(self, paths: Sequence[Sequence[str]]) -> None:
        self._live().refresh_observes(paths)

    def diagnostics(self) -> dict[str, Any]:
        # Everything this transport sees is already in the resource dump.
        return {}


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
        from .legacy_http_transport import LegacyHttpTransport

        return LegacyHttpTransport(
            data[CONF_HOST],
            data[CONF_PORT],
            cert_pem=data[CONF_LEAF_CERT_PEM],
            key_pem=data[CONF_LEAF_KEY_PEM],
            token=data[CONF_DEVICE_TOKEN],
            family=data.get(CONF_LEGACY_FAMILY),
        )
    return DtlsTransport(
        data[CONF_HOST],
        data[CONF_PORT],
        cert_pem=data[CONF_LEAF_CERT_PEM],
        key_pem=data[CONF_LEAF_KEY_PEM],
        on_notification=on_notification,
        local_port=local_port,
    )


def translates_resources(data: Mapping[str, Any]) -> bool:
    """Whether this entry's transport can present the device's own resources.

    False only for an 8888 family with no envelope table, which is read for
    its identity alone; discovery treats that like any unrecognized device
    type rather than routing on the identity. Decided from the entry, not a
    live session, so a discovery replayed from the snapshot agrees.
    """
    if data.get(CONF_TRANSPORT) != TRANSPORT_LEGACY_HTTP:
        return True
    from .legacy_http import is_mapped

    return is_mapped(data.get(CONF_LEGACY_FAMILY))
