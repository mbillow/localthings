"""Authentication providers and DTLS session construction."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from smartthings_local.protocol import auth as protocol_auth
from smartthings_local.protocol import dtls_session

from .const import (
    AUTH_CERTIFICATE,
    CONF_HOST,
    CONF_PORT,
)
from .credentials import (
    OwnerPskCredentials,
    authentication_type,
    certificate_credentials_from_entry,
    owner_psk_credentials_from_entry,
)

NotificationCallback = Callable[[str, bytes], None]


def create_certificate_auth(
    certificate_pem: str,
    private_key_pem: str,
) -> protocol_auth.CertificateAuth:
    """Create the existing in-memory certificate authentication provider."""
    return protocol_auth.CertificateAuth.from_memory(certificate_pem, private_key_pem)


def create_dtls_session(
    host: str,
    port: int,
    *,
    auth: protocol_auth.AuthenticationProvider,
    on_notification: NotificationCallback | None = None,
    local_port: int | None = None,
) -> dtls_session.DtlsCoapSession:
    """Construct a DTLS session without starting network I/O."""
    return dtls_session.DtlsCoapSession(
        host,
        port,
        auth=auth,
        on_notification=on_notification,
        local_port=local_port,
    )


def create_certificate_session(
    host: str,
    port: int,
    *,
    certificate_pem: str,
    private_key_pem: str,
    on_notification: NotificationCallback | None = None,
    local_port: int | None = None,
) -> dtls_session.DtlsCoapSession:
    """Construct a session with the existing certificate authentication."""
    return create_dtls_session(
        host,
        port,
        auth=create_certificate_auth(certificate_pem, private_key_pem),
        on_notification=on_notification,
        local_port=local_port,
    )


def create_owner_psk_auth(
    owner_uuid: str,
    owner_psk: str,
) -> protocol_auth.PskAuth:
    """Create a PSK provider from an existing OCF owner credential."""
    return OwnerPskCredentials.from_text(
        owner_uuid=owner_uuid,
        owner_psk=owner_psk,
    ).authentication_provider()


def create_owner_psk_session(
    host: str,
    port: int,
    *,
    owner_uuid: str,
    owner_psk: str,
    on_notification: NotificationCallback | None = None,
    local_port: int | None = None,
) -> dtls_session.DtlsCoapSession:
    """Construct a session from an existing OCF OwnerPSK."""
    return create_dtls_session(
        host,
        port,
        auth=create_owner_psk_auth(owner_uuid, owner_psk),
        on_notification=on_notification,
        local_port=local_port,
    )


def authentication_provider_from_entry(
    data: Mapping[str, Any],
) -> protocol_auth.AuthenticationProvider:
    """Build exactly one provider through the shared authentication factories."""
    if authentication_type(data) == AUTH_CERTIFICATE:
        certificate, private_key = certificate_credentials_from_entry(data)
        return create_certificate_auth(certificate, private_key)
    owner_uuid, owner_psk = owner_psk_credentials_from_entry(data)
    return create_owner_psk_auth(owner_uuid, owner_psk)


def create_entry_session(
    data: Mapping[str, Any],
    *,
    on_notification: NotificationCallback | None = None,
    local_port: int | None = None,
) -> dtls_session.DtlsCoapSession:
    """Construct the session selected by one complete config entry."""
    host = data.get(CONF_HOST)
    port = data.get(CONF_PORT)
    if not isinstance(host, str) or not host:
        raise ValueError("missing session endpoint")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("invalid session endpoint")
    return create_dtls_session(
        host,
        port,
        auth=authentication_provider_from_entry(data),
        on_notification=on_notification,
        local_port=local_port,
    )
