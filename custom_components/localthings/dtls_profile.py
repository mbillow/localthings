"""The narrow DTLS profile Samsung OCF appliances actually accept.

The library's default memory-BIO profile advertises the TLS 1.3 cipher suites
and TLS 1.3 extensions in its ClientHello. Samsung/TizenRT rejects that flight
with a fatal `handshake_failure` before a cookie exchange, while the same
appliance accepts a classic DTLS 1.2 flight carrying only
`ECDHE-ECDSA-AES128-GCM-SHA256`. This module installs that restriction on the
library's certificate paths without touching PSK: PSK sessions use their own
engine, not OpenSSL's TLS 1.3 suite list.

The restriction is applied as OpenSSL options rather than by changing the
library's cipher list:
  * `OP_NO_TLSv1_3` removes the TLS 1.3 cipher suites and their extensions
    from the ClientHello.
  * `OP_NO_TICKET` removes session-ticket advertisement, matching the
    appliance's accepted profile.

It is deliberately idempotent and only patches the certificate-backed paths:
`CertificateAuth.configure_context`, `ServerCertificateAuth.configure_context`,
the diagnostic context builder, and the stateless ClientHello flight builder.
"""

from __future__ import annotations

from OpenSSL import SSL
from smartthings_local.protocol import auth as protocol_auth
from smartthings_local.protocol import dtls_probe as protocol_probe
from smartthings_local.protocol.coap import split_dtls

# The one classic cipher suite every supported Samsung board accepts. `@SECLEVEL=0`
# is part of the library's profile because Samsung's server chain includes SHA-1.
_SAMSUNG_DTLS_CIPHERS = protocol_auth._DTLS_CIPHERS
_SAMSUNG_DTLS_OPTIONS = SSL.OP_NO_TLSv1_3 | SSL.OP_NO_TICKET

_READ_SIZE = 65535
_INSTALLED = False


def _restrict_context(context: SSL.Context) -> None:
    """Apply the Samsung DTLS profile to one OpenSSL context."""
    context.set_cipher_list(_SAMSUNG_DTLS_CIPHERS)
    context.set_options(_SAMSUNG_DTLS_OPTIONS)


def _restricted_certificate_configure_context(
    self,
    context: SSL.Context,
    _original=protocol_auth.CertificateAuth.configure_context,
) -> None:
    """Configure certificate authentication, then drop TLS 1.3."""
    _original(self, context)
    _restrict_context(context)


def _restricted_server_configure_context(
    self,
    context: SSL.Context,
    _original=protocol_auth.ServerCertificateAuth.configure_context,
) -> None:
    """Configure server-only certificate verification, then drop TLS 1.3."""
    _original(self, context)
    _restrict_context(context)


_ORIGINAL_DIAGNOSTIC_CONTEXT = protocol_probe._diagnostic_context


def _restricted_diagnostic_context(**kwargs):
    """Build the diagnostic context the library would build, then restrict it.

    A PSK provider configures the same context with its own cipher list and
    client callback; replacing that list with the certificate profile would
    break PSK diagnosis. Only certificate-backed diagnostics are narrowed.
    """
    context = _ORIGINAL_DIAGNOSTIC_CONTEXT(**kwargs)
    auth = kwargs.get("auth")
    if auth is None or isinstance(
        auth, (protocol_auth.CertificateAuth, protocol_auth.ServerCertificateAuth)
    ):
        _restrict_context(context)
    return context


def _restricted_client_hello_flight(*, mtu: int) -> tuple[bytes, ...]:
    """Freeze the same first flight as a session, but in the narrow profile."""
    context = SSL.Context(SSL.DTLS_METHOD)
    context.load_verify_locations(protocol_auth._OCF_ROOT_CA)
    context.set_verify(SSL.VERIFY_PEER, lambda *args: True)
    context.set_cipher_list(_SAMSUNG_DTLS_CIPHERS)
    context.set_options(_SAMSUNG_DTLS_OPTIONS)

    connection = SSL.Connection(context, None)
    connection.set_connect_state()
    connection.set_ciphertext_mtu(mtu)
    try:
        connection.do_handshake()
    except SSL.WantReadError:
        pass

    records = []
    while True:
        try:
            outbound = connection.bio_read(_READ_SIZE)
        except SSL.WantReadError:
            break
        if not outbound:
            break
        records.extend(split_dtls(outbound))
    if not records:
        raise protocol_probe.ProbeError()
    return tuple(records)


def install_samsung_dtls_profile() -> None:
    """Install the narrow profile on every certificate-backed library path.

    Idempotent so importing this module from both `transport` and `probing`
    patches exactly once. PSK providers are intentionally untouched: their
    `_create_dtls_connection` engine carries a single ECDHE-PSK suite and
    never emits OpenSSL's TLS 1.3 cipher list.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    protocol_auth.CertificateAuth.configure_context = (
        _restricted_certificate_configure_context
    )
    protocol_auth.ServerCertificateAuth.configure_context = (
        _restricted_server_configure_context
    )
    protocol_probe._diagnostic_context = _restricted_diagnostic_context
    protocol_probe._client_hello_flight = _restricted_client_hello_flight
