"""Authentication providers and DTLS session construction."""

from __future__ import annotations

from collections.abc import Callable

from smartthings_local.protocol import auth as protocol_auth
from smartthings_local.protocol import dtls_session

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
