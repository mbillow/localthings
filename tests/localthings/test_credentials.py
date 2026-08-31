"""Credential-shape and redaction tests for config-entry authentication."""

from __future__ import annotations

from dataclasses import is_dataclass
from unittest.mock import MagicMock, patch
from uuid import UUID

import cbor2
import pytest
from smartthings_local.errors import SessionError
from smartthings_local.protocol import auth as protocol_auth

from custom_components.localthings.const import (
    AUTH_CERTIFICATE,
    AUTH_OWNER_PSK,
    CONF_AUTH_TYPE,
    CONF_CA_CERT_PEM,
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_OWNER_PSK,
    CONF_OWNER_UUID,
    CONF_PORT,
)
from custom_components.localthings.credentials import (
    InvalidCredentialConfig,
    OwnerPskCredentials,
    OwnerPskDeviceMismatch,
    certificate_credentials_from_entry,
    normalize_owner_psk,
    normalize_owner_uuid,
    owner_psk_credentials_from_entry,
    require_matching_ocf_uuid,
)
from custom_components.localthings.registry.identity import read_ocf_device_id

OWNER_UUID = "12121212-3434-4656-8787-9a9a9a9a9a9a"
OWNER_PSK = "00112233445566778899aabbccddeeff"
DEVICE_UUID = "11111111-2222-4333-8444-555555555555"


def _owner_psk_data() -> dict:
    return {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 5684,
        CONF_AUTH_TYPE: AUTH_OWNER_PSK,
        CONF_OWNER_UUID: OWNER_UUID,
        CONF_OWNER_PSK: OWNER_PSK,
    }


def test_owner_uuid_and_key_are_canonicalized() -> None:
    assert normalize_owner_uuid(f"  {OWNER_UUID.upper()}  ") == OWNER_UUID
    assert normalize_owner_psk(f"  {OWNER_PSK.upper()}  ") == OWNER_PSK
    assert normalize_owner_psk("CD" * 32) == "cd" * 32


@pytest.mark.parametrize(
    "value",
    [
        "not-a-uuid",
        "00000000-0000-0000-0000-000000000000",
        # Valid text whose raw UUID contains a NUL byte, which OpenSSL cannot
        # represent as its NUL-terminated client identity.
        "01010101-0101-4101-8101-010101010100",
    ],
)
def test_owner_uuid_rejects_invalid_nil_and_nul_values(value: str) -> None:
    with pytest.raises(InvalidCredentialConfig, match=r"^invalid") as raised:
        normalize_owner_uuid(value)
    assert value not in str(raised.value)


@pytest.mark.parametrize(
    "value",
    [
        "ab" * 15,
        "ab" * 17,
        "ab" * 31,
        "ab" * 33,
        "gg" * 16,
        "11" * 7 + "  " + "11" * 8,
    ],
)
def test_owner_psk_rejects_non_hex_wrong_length_and_embedded_space(value: str) -> None:
    with pytest.raises(InvalidCredentialConfig, match=r"^invalid OwnerPSK$") as raised:
        normalize_owner_psk(value)
    assert value not in str(raised.value)


def test_owner_credentials_build_exact_protocol_bytes_without_repr_leak() -> None:
    provider = MagicMock(spec=protocol_auth.PskAuth)
    with patch.object(protocol_auth, "PskAuth", return_value=provider) as provider_class:
        credentials = OwnerPskCredentials.from_text(
            owner_uuid=OWNER_UUID,
            owner_psk=OWNER_PSK,
        )

    provider_class.assert_called_once_with(
        identity=UUID(OWNER_UUID).bytes,
        key=bytes.fromhex(OWNER_PSK),
    )
    assert credentials.authentication_provider() is provider
    assert repr(credentials) == "OwnerPskCredentials()"
    assert is_dataclass(credentials) is False
    with pytest.raises(TypeError):
        vars(credentials)
    with pytest.raises(AttributeError, match="immutable"):
        credentials.extra = "value"
    for value in (OWNER_UUID, OWNER_PSK):
        assert value not in repr(credentials)


def test_real_owner_provider_is_redacted() -> None:
    provider = OwnerPskCredentials.from_text(
        owner_uuid=OWNER_UUID,
        owner_psk=OWNER_PSK,
    ).authentication_provider()

    assert repr(provider) == "PskAuth()"
    assert OWNER_UUID not in repr(provider)
    assert OWNER_PSK not in repr(provider)


def test_legacy_entry_without_auth_type_stays_certificate_backed() -> None:
    assert certificate_credentials_from_entry(
        {
            CONF_LEAF_CERT_PEM: "CERTIFICATE",
            CONF_LEAF_KEY_PEM: "PRIVATE KEY",
        }
    ) == ("CERTIFICATE", "PRIVATE KEY")


@pytest.mark.parametrize(
    "data",
    [
        {CONF_AUTH_TYPE: "unknown"},
        {
            CONF_AUTH_TYPE: AUTH_CERTIFICATE,
            CONF_LEAF_CERT_PEM: "CERTIFICATE",
            CONF_LEAF_KEY_PEM: "PRIVATE KEY",
            CONF_OWNER_UUID: OWNER_UUID,
        },
        {
            **_owner_psk_data(),
            CONF_CA_CERT_PEM: "CERTIFICATE",
        },
        {
            CONF_AUTH_TYPE: AUTH_OWNER_PSK,
            CONF_OWNER_UUID: OWNER_UUID,
        },
    ],
)
def test_unknown_mixed_and_incomplete_entry_auth_fails_closed(data: dict) -> None:
    rendered = repr(data)
    with pytest.raises(InvalidCredentialConfig) as raised:
        if data.get(CONF_AUTH_TYPE) == AUTH_OWNER_PSK:
            owner_psk_credentials_from_entry(data)
        else:
            certificate_credentials_from_entry(data)

    assert OWNER_PSK not in str(raised.value)
    assert OWNER_UUID not in str(raised.value)
    assert rendered not in str(raised.value)


def test_authenticated_device_uuid_must_match_exactly() -> None:
    assert require_matching_ocf_uuid(DEVICE_UUID.upper(), DEVICE_UUID) == DEVICE_UUID

    with pytest.raises(OwnerPskDeviceMismatch, match=r"^authenticated OCF") as raised:
        require_matching_ocf_uuid(
            DEVICE_UUID,
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        )
    assert DEVICE_UUID not in str(raised.value)


def test_exact_ocf_device_read_returns_only_the_reported_di() -> None:
    session = MagicMock()
    session.get.return_value = (0x45, cbor2.dumps({"di": DEVICE_UUID, "pi": OWNER_UUID}))

    assert read_ocf_device_id(session, timeout=2.5) == DEVICE_UUID
    session.get.assert_called_once_with(["oic", "d"], timeout=2.5)


@pytest.mark.parametrize(
    "response",
    [
        (0x84, b""),
        (0x45, b"not-cbor"),
        (0x45, cbor2.dumps([{"di": DEVICE_UUID}])),
        (0x45, cbor2.dumps({"di": 1234})),
    ],
)
def test_exact_ocf_device_read_rejects_unusable_responses(response: tuple) -> None:
    session = MagicMock()
    session.get.return_value = response

    assert read_ocf_device_id(session) is None


def test_exact_ocf_device_read_propagates_transport_failures() -> None:
    session = MagicMock()
    session.get.side_effect = SessionError()

    with pytest.raises(SessionError):
        read_ocf_device_id(session)
