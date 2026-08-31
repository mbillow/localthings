"""Tests for DTLS authentication and session construction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from smartthings_local.protocol import auth as protocol_auth
from smartthings_local.protocol import dtls_session

from custom_components.localthings import session as session_factory
from custom_components.localthings.const import (
    AUTH_CERTIFICATE,
    AUTH_OWNER_PSK,
    CONF_AUTH_TYPE,
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_OWNER_PSK,
    CONF_OWNER_UUID,
    CONF_PORT,
)


def test_create_certificate_auth_uses_in_memory_credentials() -> None:
    """Stored certificate entries stay on the existing in-memory provider."""
    auth = MagicMock(spec=protocol_auth.CertificateAuth)

    with patch.object(
        protocol_auth.CertificateAuth,
        "from_memory",
        return_value=auth,
    ) as from_memory:
        result = session_factory.create_certificate_auth("CERTIFICATE", "PRIVATE KEY")

    assert result is auth
    from_memory.assert_called_once_with("CERTIFICATE", "PRIVATE KEY")


def test_create_dtls_session_passes_only_the_explicit_auth_provider() -> None:
    """The session factory must not mix auth with legacy certificate arguments."""
    auth = MagicMock(spec=protocol_auth.AuthenticationProvider)
    callback = MagicMock()
    session = MagicMock(spec=dtls_session.DtlsCoapSession)

    with patch.object(
        dtls_session,
        "DtlsCoapSession",
        return_value=session,
    ) as session_class:
        result = session_factory.create_dtls_session(
            "192.0.2.10",
            49154,
            auth=auth,
            on_notification=callback,
            local_port=49742,
        )

    assert result is session
    session_class.assert_called_once_with(
        "192.0.2.10",
        49154,
        auth=auth,
        on_notification=callback,
        local_port=49742,
    )
    assert "cert_pem" not in session_class.call_args.kwargs
    assert "key_pem" not in session_class.call_args.kwargs


def test_create_certificate_session_composes_the_two_factories() -> None:
    """Certificate callers use the shared session path without lifecycle I/O."""
    auth = MagicMock(spec=protocol_auth.AuthenticationProvider)
    callback = MagicMock()
    session = MagicMock(spec=dtls_session.DtlsCoapSession)

    with (
        patch.object(
            session_factory,
            "create_certificate_auth",
            return_value=auth,
        ) as auth_factory,
        patch.object(
            session_factory,
            "create_dtls_session",
            return_value=session,
        ) as session_builder,
    ):
        result = session_factory.create_certificate_session(
            "192.0.2.10",
            49154,
            certificate_pem="CERTIFICATE",
            private_key_pem="PRIVATE KEY",
            on_notification=callback,
            local_port=49742,
        )

    assert result is session
    auth_factory.assert_called_once_with("CERTIFICATE", "PRIVATE KEY")
    session_builder.assert_called_once_with(
        "192.0.2.10",
        49154,
        auth=auth,
        on_notification=callback,
        local_port=49742,
    )
    session.connect.assert_not_called()
    session.start_reader.assert_not_called()


def test_create_owner_psk_session_composes_without_lifecycle_io() -> None:
    """OwnerPSK construction uses the same provider-based session seam."""
    auth = MagicMock(spec=protocol_auth.PskAuth)
    callback = MagicMock()
    session = MagicMock(spec=dtls_session.DtlsCoapSession)

    with (
        patch.object(
            session_factory,
            "create_owner_psk_auth",
            return_value=auth,
        ) as auth_factory,
        patch.object(
            session_factory,
            "create_dtls_session",
            return_value=session,
        ) as session_builder,
    ):
        result = session_factory.create_owner_psk_session(
            "192.0.2.10",
            5684,
            owner_uuid="12121212-3434-4656-8787-9a9a9a9a9a9a",
            owner_psk="00112233445566778899aabbccddeeff",
            on_notification=callback,
            local_port=49742,
        )

    assert result is session
    auth_factory.assert_called_once_with(
        "12121212-3434-4656-8787-9a9a9a9a9a9a",
        "00112233445566778899aabbccddeeff",
    )
    session_builder.assert_called_once_with(
        "192.0.2.10",
        5684,
        auth=auth,
        on_notification=callback,
        local_port=49742,
    )
    session.connect.assert_not_called()
    session.start_reader.assert_not_called()


def test_entry_authentication_uses_the_certificate_factory() -> None:
    data = {
        CONF_AUTH_TYPE: AUTH_CERTIFICATE,
        CONF_LEAF_CERT_PEM: "CERTIFICATE",
        CONF_LEAF_KEY_PEM: "PRIVATE KEY",
    }
    auth = MagicMock(spec=protocol_auth.CertificateAuth)

    with patch.object(
        session_factory,
        "create_certificate_auth",
        return_value=auth,
    ) as certificate_factory:
        result = session_factory.authentication_provider_from_entry(data)

    assert result is auth
    certificate_factory.assert_called_once_with("CERTIFICATE", "PRIVATE KEY")


def test_entry_authentication_uses_the_owner_psk_factory() -> None:
    data = {
        CONF_AUTH_TYPE: AUTH_OWNER_PSK,
        CONF_OWNER_UUID: "12121212-3434-4656-8787-9a9a9a9a9a9a",
        CONF_OWNER_PSK: "00112233445566778899aabbccddeeff",
    }
    auth = MagicMock(spec=protocol_auth.PskAuth)

    with patch.object(
        session_factory,
        "create_owner_psk_auth",
        return_value=auth,
    ) as owner_psk_factory:
        result = session_factory.authentication_provider_from_entry(data)

    assert result is auth
    owner_psk_factory.assert_called_once_with(
        "12121212-3434-4656-8787-9a9a9a9a9a9a",
        "00112233445566778899aabbccddeeff",
    )


def test_create_entry_session_uses_one_validated_provider_and_endpoint() -> None:
    data = {CONF_HOST: "192.0.2.10", CONF_PORT: 5684, CONF_AUTH_TYPE: AUTH_OWNER_PSK}
    auth = MagicMock(spec=protocol_auth.PskAuth)
    callback = MagicMock()
    session = MagicMock(spec=dtls_session.DtlsCoapSession)

    with (
        patch.object(
            session_factory,
            "authentication_provider_from_entry",
            return_value=auth,
        ) as provider_from_entry,
        patch.object(
            session_factory,
            "create_dtls_session",
            return_value=session,
        ) as session_builder,
    ):
        result = session_factory.create_entry_session(
            data,
            on_notification=callback,
            local_port=49742,
        )

    assert result is session
    provider_from_entry.assert_called_once_with(data)
    session_builder.assert_called_once_with(
        "192.0.2.10",
        5684,
        auth=auth,
        on_notification=callback,
        local_port=49742,
    )


@pytest.mark.parametrize(
    "data",
    [
        {},
        {CONF_HOST: "", CONF_PORT: 5684},
        {CONF_HOST: "192.0.2.10", CONF_PORT: True},
        {CONF_HOST: "192.0.2.10", CONF_PORT: 0},
        {CONF_HOST: "192.0.2.10", CONF_PORT: 65536},
    ],
)
def test_create_entry_session_rejects_invalid_endpoints_before_auth(data: dict) -> None:
    with (
        patch.object(session_factory, "authentication_provider_from_entry") as provider,
        pytest.raises(ValueError, match="session endpoint"),
    ):
        session_factory.create_entry_session(data)

    provider.assert_not_called()


def test_real_certificate_provider_is_session_compatible_and_redacted() -> None:
    """The published provider API accepts the existing in-memory credential shape."""
    session = session_factory.create_certificate_session(
        "192.0.2.10",
        49154,
        certificate_pem="SYNTHETIC CERTIFICATE",
        private_key_pem="SYNTHETIC PRIVATE KEY",
    )

    assert isinstance(session.auth, protocol_auth.CertificateAuth)
    assert repr(session.auth) == "CertificateAuth()"
    assert "SYNTHETIC" not in repr(session.auth)
