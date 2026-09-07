"""Building a DTLS session from a config entry's stored credentials.

One place decides which authentication provider an entry gets, so adding a
carrier does not mean teaching every caller about it. The library's
provider API (`smartthings_local.protocol.auth`) is what makes that
possible: `CertificateAuth` and `PskAuth` are interchangeable behind
`AuthenticationProvider`, and `DtlsCoapSession(auth=...)` takes either.

Deliberately construction only. Connecting, starting the reader, checking
what the peer turned out to be, and owning the session's lifetime all stay
with the coordinator -- a factory that opened sockets would leave every
caller guessing who closes them.

The legacy `cert_pem=`/`key_pem=` constructor arguments are not used here.
They still work upstream, but they can only ever express one carrier, and
routing certificates through the same provider seam as everything else is
what keeps the two paths honest about being the same shape.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from smartthings_local.protocol import auth as protocol_auth
from smartthings_local.protocol import dtls_session

from .const import AUTH_CERTIFICATE, CONF_HOST, CONF_PORT
from .credentials import (
    InvalidCredentialConfig,
    authentication_carrier,
    certificate_credentials,
    psk_credentials,
)

# fn(href, payload_bytes), called on the DTLS reader thread.
NotificationCallback = Callable[[str, bytes], None]


def certificate_provider(
    certificate_pem: str, private_key_pem: str
) -> protocol_auth.CertificateAuth:
    """The in-memory certificate provider the existing entries use."""
    return protocol_auth.CertificateAuth.from_memory(certificate_pem, private_key_pem)


def psk_provider(identity: str, key: str) -> protocol_auth.PskAuth:
    """A pre-shared-key provider from an already-normalized identity and key.

    Shared by both PSK profiles: an OwnerPSK and a pairwise peer differ in
    what they grant on the appliance, not in how the handshake carries them.

    `identity` is a canonical UUID string and becomes the raw 16 bytes
    upstream expects; `key` is lowercase hex. Both have already been through
    `credentials.normalize_psk_*`, which enforces the same rules `PskAuth`
    does so a bad value is rejected while validating an entry rather than
    part-way through a connection attempt.
    """
    return protocol_auth.PskAuth(
        identity=UUID(identity).bytes,
        key=bytes.fromhex(key),
    )


def authentication_provider_from_entry(
    data: Mapping[str, Any],
) -> protocol_auth.AuthenticationProvider:
    """The one provider this entry's stored credentials describe.

    Raises `InvalidCredentialConfig` rather than falling back to another
    carrier: an entry that cannot say what it authenticates with is not one
    to guess at, and a wrong guess reaches the appliance as a failed
    handshake that looks like a rejected credential.
    """
    if authentication_carrier(data) == AUTH_CERTIFICATE:
        return certificate_provider(*certificate_credentials(data))
    return psk_provider(*psk_credentials(data))


def create_entry_session(
    data: Mapping[str, Any],
    *,
    on_notification: NotificationCallback | None = None,
    local_port: int | None = None,
) -> dtls_session.DtlsCoapSession:
    """An unconnected session for the endpoint and credentials on `data`.

    The endpoint is validated here because a session built on a missing or
    nonsense port fails inside the library with an error that reads like the
    device is unreachable. `isinstance(port, bool)` is checked explicitly:
    `True` is an `int` and would otherwise pass as port 1.
    """
    host = data.get(CONF_HOST)
    port = data.get(CONF_PORT)
    if not isinstance(host, str) or not host.strip():
        raise InvalidCredentialConfig("entry has no host")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise InvalidCredentialConfig("entry has no usable port")
    return dtls_session.DtlsCoapSession(
        host,
        port,
        auth=authentication_provider_from_entry(data),
        on_notification=on_notification,
        local_port=local_port,
    )
