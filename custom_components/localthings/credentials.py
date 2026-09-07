"""What a config entry authenticates with, and what its credential proves.

Two axes, not one (issue #435). The **carrier** is what the DTLS handshake
uses -- a client certificate or a pre-shared key. The **profile** is what a
PSK actually is on the appliance:

- ``owner``  -- a per-appliance OwnerPSK. We hold the OCF owner credential.
- ``peer``   -- an additive, least-privilege pairwise credential installed
  alongside Samsung's ownership, which stays in place.

Both are ECDHE-PSK on the wire and store identically, so the split costs
nothing at the transport layer. It exists because their *import* contracts
differ: an OwnerPSK identity can be suggested from plaintext
``devowneruuid`` where the firmware serves it, while a peer identity is not
in DOXM at all and can only arrive with its key. Modelling only the first
would buy a second migration the day the second lands, and there is
hardware for both.

Acquisition is out of scope -- no ownership transfer, no ``/oic/sec/*``
writes. This module reads an already-provisioned credential off an entry,
checks it is complete and unmixed, and says what identity binding its
profile demands. See ``docs/credential-acquisition.md`` for where the
credentials themselves come from.

Nothing here ever formats a credential into a message: an invalid one
raises a fixed string, so a malformed key cannot reach a log or a repair.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import Enum
from typing import Any
from uuid import UUID

from .const import (
    AUTH_CERTIFICATE,
    AUTH_PSK,
    CONF_AUTH_CARRIER,
    CONF_CA_CERT_PEM,
    CONF_CA_KEY_PEM,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_PSK_IDENTITY,
    CONF_PSK_KEY,
    CONF_PSK_PROFILE,
    PSK_PROFILE_OWNER,
    PSK_PROFILE_PEER,
)

# 128- or 256-bit, matching PskAuth's own 16-or-32-byte rule.
_PSK_KEY_RE = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{64}")

_CERTIFICATE_FIELDS = (
    CONF_CA_CERT_PEM,
    CONF_CA_KEY_PEM,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
)
_PSK_FIELDS = (CONF_PSK_IDENTITY, CONF_PSK_KEY)

_CARRIERS = (AUTH_CERTIFICATE, AUTH_PSK)
_PSK_PROFILES = (PSK_PROFILE_OWNER, PSK_PROFILE_PEER)


class InvalidCredentialConfig(ValueError):
    """An entry's stored credentials are unusable.

    Carries a fixed, credential-free message: this is raised while looking
    at credential material, so anything it formats in could reach a log.
    """


class DeviceIdentityMismatch(Exception):
    """A session authenticated, but against a different OCF device.

    Deliberately not a credential error. The handshake succeeded, so the
    stored key is valid -- what is wrong is which appliance holds it, which
    no amount of re-entering a credential fixes. Raising this rather than a
    reauthentication signal keeps the user out of a form that cannot help.
    """


class DeviceBinding(Enum):
    """What an authenticated session proved about *which* device answered.

    Three outcomes, deliberately not two (issue #435). ``UNPROVEN`` is not a
    failure: a device that authenticated but served no usable ``/oic/d`` di,
    or an entry that has not recorded one yet, has told us nothing either
    way. Collapsing it into ``MISMATCHED`` would reject working credentials
    on firmware with an unusable identity field, and collapsing it into
    ``MATCHED`` would silently retire the check. The caller decides what
    each means; none of them is "the credential was rejected", which is a
    separate thing the handshake itself reports.
    """

    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNPROVEN = "unproven"


def authentication_carrier(data: Mapping[str, Any]) -> str:
    """The carrier this entry authenticates with.

    Absent means the client-certificate path: it is the only thing an entry
    written before this field existed can be, which is why this needs no
    migration. An unrecognized value is rejected rather than defaulted --
    guessing would silently downgrade an entry a newer release wrote.
    """
    carrier = data.get(CONF_AUTH_CARRIER, AUTH_CERTIFICATE)
    if carrier not in _CARRIERS:
        raise InvalidCredentialConfig("unsupported authentication carrier")
    return carrier


def psk_profile(data: Mapping[str, Any]) -> str:
    """What role this entry's PSK plays on the appliance.

    Required, with no default: the two profiles differ in what a credential
    grants and where its identity can come from, so an entry that does not
    say is not a PSK entry we know how to use.
    """
    if authentication_carrier(data) != AUTH_PSK:
        raise InvalidCredentialConfig("not a pre-shared-key entry")
    profile = data.get(CONF_PSK_PROFILE)
    if profile not in _PSK_PROFILES:
        raise InvalidCredentialConfig("unsupported pre-shared-key profile")
    return profile


def identity_may_be_suggested_from_doxm(profile: str) -> bool:
    """Whether a plaintext ``/oic/sec/doxm`` read can prefill this profile's
    identity field.

    True only for OwnerPSK, whose identity is `devowneruuid`. A pairwise
    peer's identity is not in DOXM -- it is whatever the bootstrap that
    installed the credential chose -- so an import flow must take it from
    the user alongside the key rather than offering a guess.

    Even for OwnerPSK this is a suggestion and never proof: the value is
    readable without authenticating, so it says nothing about holding the
    matching key. The handshake and the binding check below settle that.
    """
    if profile not in _PSK_PROFILES:
        raise InvalidCredentialConfig("unsupported pre-shared-key profile")
    return profile == PSK_PROFILE_OWNER


def normalize_psk_identity(value: Any) -> str:
    """One canonical 16-byte OCF UUID, usable as a DTLS PSK identity.

    The NUL-byte rule is upstream's (``PskAuth`` refuses one) and it is a
    real constraint rather than defensiveness: OpenSSL carries the identity
    as a NUL-terminated byte string, so a zero byte truncates it on the
    wire. Roughly one UUID in sixteen contains one. It gets its own message
    because "your credential is malformed" is wrong and unhelpful for a
    perfectly well-formed UUID -- and because a *peer* identity is generated
    by whatever installed it, so the fix there is to generate another.
    """
    try:
        parsed = UUID(str(value).strip())
    except (AttributeError, TypeError, ValueError):
        raise InvalidCredentialConfig("PSK identity is not a UUID") from None
    if parsed.int == 0:
        raise InvalidCredentialConfig("PSK identity is not a UUID")
    if b"\x00" in parsed.bytes:
        raise InvalidCredentialConfig("PSK identity contains a zero byte, which DTLS cannot carry")
    return str(parsed)


def normalize_psk_key(value: Any) -> str:
    """A 128- or 256-bit key as lowercase hex, matching PskAuth's own rule."""
    normalized = str(value).strip()
    if _PSK_KEY_RE.fullmatch(normalized) is None:
        raise InvalidCredentialConfig("PSK key is not 128- or 256-bit hex")
    return normalized.lower()


def certificate_credentials(data: Mapping[str, Any]) -> tuple[str, str]:
    """This entry's leaf certificate and private key.

    Rejects an entry carrying PSK fields as well: a half-converted entry has
    two credentials and no statement of which is current, and picking one
    would be a guess about which the device will accept.
    """
    if authentication_carrier(data) != AUTH_CERTIFICATE:
        raise InvalidCredentialConfig("entry does not authenticate with a certificate")
    if any(field in data for field in _PSK_FIELDS):
        raise InvalidCredentialConfig("entry carries more than one kind of credential")
    certificate = data.get(CONF_LEAF_CERT_PEM)
    private_key = data.get(CONF_LEAF_KEY_PEM)
    if not isinstance(certificate, str) or not certificate.strip():
        raise InvalidCredentialConfig("certificate credentials are incomplete")
    if not isinstance(private_key, str) or not private_key.strip():
        raise InvalidCredentialConfig("certificate credentials are incomplete")
    return certificate, private_key


def psk_credentials(data: Mapping[str, Any]) -> tuple[str, str]:
    """This entry's PSK identity and key, normalized.

    Rejects an entry carrying certificate material for the same reason
    `certificate_credentials` rejects the mirror case.
    """
    psk_profile(data)  # rejects a missing or unknown profile before the key
    if any(field in data for field in _CERTIFICATE_FIELDS):
        raise InvalidCredentialConfig("entry carries more than one kind of credential")
    identity = data.get(CONF_PSK_IDENTITY)
    key = data.get(CONF_PSK_KEY)
    if identity is None or key is None:
        raise InvalidCredentialConfig("pre-shared-key credentials are incomplete")
    return normalize_psk_identity(identity), normalize_psk_key(key)


def requires_authenticated_device_id(data: Mapping[str, Any]) -> bool:
    """Whether this entry's profile has to check *which* device answered.

    True for both PSK profiles. ECDHE-PSK has no server certificate, so
    there is nothing to pin during the handshake and a post-handshake
    authenticated ``/oic/d`` read is the only binding available.

    False for the certificate path, which is unchanged: it has never done
    this check, and the library offers a strictly better one for it --
    `SamsungServerProfile` pins the subject UUID inside the certificate
    verification callback, before any application data flows. Wiring that
    up is its own change, not a side effect of this one.
    """
    return authentication_carrier(data) == AUTH_PSK


def check_device_binding(expected: str | None, reported: str | None) -> DeviceBinding:
    """Compare a stored OCF device id against what a session just proved.

    Both sides are already-normalized `/oic/d` `di` values -- see
    `registry.identity.proven_ocf_device_id`, which is deliberately narrower
    than the registry key's `di -> pi -> serial -> host` chain. Passing the
    key here instead would compare a platform UUID or a serial against a
    device UUID and report a mismatch on a correctly configured appliance.
    """
    if not expected or not reported:
        return DeviceBinding.UNPROVEN
    return DeviceBinding.MATCHED if expected == reported else DeviceBinding.MISMATCHED
