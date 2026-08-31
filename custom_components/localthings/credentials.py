"""Config-entry authentication models that keep credential values opaque."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from smartthings_local.protocol import auth as protocol_auth

from .const import (
    AUTH_CERTIFICATE,
    AUTH_OWNER_PSK,
    CONF_AUTH_TYPE,
    CONF_CA_CERT_PEM,
    CONF_CA_KEY_PEM,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_OWNER_PSK,
    CONF_OWNER_UUID,
)

_OWNER_PSK_HEX_RE = re.compile(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{64})\Z")
_CERTIFICATE_FIELDS = (
    CONF_CA_CERT_PEM,
    CONF_CA_KEY_PEM,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
)
_OWNER_PSK_FIELDS = (CONF_OWNER_UUID, CONF_OWNER_PSK)


class InvalidCredentialConfig(ValueError):
    """A fixed, credential-safe config-entry validation error."""


class OwnerPskDeviceMismatch(Exception):
    """An authenticated OwnerPSK session reported a different OCF device."""


def authentication_type(data: Mapping[str, Any]) -> str:
    """Return the explicit mode, treating all pre-migration entries as certificates."""
    value = data.get(CONF_AUTH_TYPE, AUTH_CERTIFICATE)
    if value not in (AUTH_CERTIFICATE, AUTH_OWNER_PSK):
        raise InvalidCredentialConfig("unsupported authentication type")
    return value


def is_owner_psk(data: Mapping[str, Any]) -> bool:
    """Return whether one entry uses an existing OCF OwnerPSK."""
    return authentication_type(data) == AUTH_OWNER_PSK


def normalize_ocf_uuid(value: str) -> str:
    """Return one canonical, non-zero OCF UUID without echoing invalid input."""
    try:
        parsed = UUID(str(value).strip())
    except (AttributeError, TypeError, ValueError):
        raise InvalidCredentialConfig("invalid OCF UUID") from None
    if parsed.int == 0:
        raise InvalidCredentialConfig("invalid OCF UUID")
    return str(parsed)


def normalize_owner_uuid(value: str) -> str:
    """Normalize the UUID used as the DTLS PSK client identity."""
    canonical = normalize_ocf_uuid(value)
    if b"\x00" in UUID(canonical).bytes:
        # OpenSSL exposes the identity as a NUL-terminated byte string.
        raise InvalidCredentialConfig("invalid owner UUID")
    return canonical


def normalize_owner_psk(value: str) -> str:
    """Normalize a strict 128- or 256-bit OwnerPSK hex string."""
    normalized = str(value).strip()
    if _OWNER_PSK_HEX_RE.fullmatch(normalized) is None:
        raise InvalidCredentialConfig("invalid OwnerPSK")
    return normalized.lower()


def require_matching_ocf_uuid(expected: str, reported: str | None) -> str:
    """Return the expected UUID only when the authenticated peer matches it."""
    expected_uuid = normalize_ocf_uuid(expected)
    try:
        reported_uuid = normalize_ocf_uuid(reported or "")
    except InvalidCredentialConfig:
        raise OwnerPskDeviceMismatch("authenticated OCF device did not match") from None
    if reported_uuid != expected_uuid:
        raise OwnerPskDeviceMismatch("authenticated OCF device did not match")
    return expected_uuid


class OwnerPskCredentials:
    """Immutable OwnerPSK credentials with no value-bearing representation."""

    __slots__ = ("_provider",)
    _provider: protocol_auth.PskAuth

    def __init__(self, provider: protocol_auth.PskAuth) -> None:
        object.__setattr__(self, "_provider", provider)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("OwnerPskCredentials is immutable")

    def __delattr__(self, _name: str) -> None:
        raise AttributeError("OwnerPskCredentials is immutable")

    @classmethod
    def from_text(cls, *, owner_uuid: str, owner_psk: str) -> OwnerPskCredentials:
        """Parse config-entry strings into the narrow protocol provider."""
        identity = UUID(normalize_owner_uuid(owner_uuid)).bytes
        key = bytes.fromhex(normalize_owner_psk(owner_psk))
        return cls(protocol_auth.PskAuth(identity=identity, key=key))

    def authentication_provider(self) -> protocol_auth.PskAuth:
        """Return the immutable provider without exposing its credential bytes."""
        return self._provider

    def __repr__(self) -> str:
        return "OwnerPskCredentials()"


def certificate_credentials_from_entry(data: Mapping[str, Any]) -> tuple[str, str]:
    """Return the one complete, unmixed certificate credential pair."""
    if authentication_type(data) != AUTH_CERTIFICATE:
        raise InvalidCredentialConfig("unexpected authentication credentials")
    if any(field in data for field in _OWNER_PSK_FIELDS):
        raise InvalidCredentialConfig("mixed authentication credentials")
    certificate = data.get(CONF_LEAF_CERT_PEM)
    private_key = data.get(CONF_LEAF_KEY_PEM)
    if not isinstance(certificate, str) or not certificate:
        raise InvalidCredentialConfig("missing certificate credentials")
    if not isinstance(private_key, str) or not private_key:
        raise InvalidCredentialConfig("missing certificate credentials")
    return certificate, private_key


def owner_psk_credentials_from_entry(data: Mapping[str, Any]) -> tuple[str, str]:
    """Return the one complete, unmixed OwnerPSK credential pair."""
    if authentication_type(data) != AUTH_OWNER_PSK:
        raise InvalidCredentialConfig("unexpected authentication credentials")
    if any(field in data for field in _CERTIFICATE_FIELDS):
        raise InvalidCredentialConfig("mixed authentication credentials")
    owner_uuid = data.get(CONF_OWNER_UUID)
    owner_psk = data.get(CONF_OWNER_PSK)
    if not isinstance(owner_uuid, str) or not isinstance(owner_psk, str):
        raise InvalidCredentialConfig("missing OwnerPSK credentials")
    return owner_uuid, owner_psk
