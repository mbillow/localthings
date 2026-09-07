"""The carrier/profile credential model (issue #435).

Two axes: the DTLS carrier (certificate or PSK) and, for PSK, what the key
is on the appliance (a per-appliance OwnerPSK, or an additive least-
privilege pairwise peer). Both PSK profiles are modelled from the start
because their import contracts differ even though their storage does not.

Nothing here can be reached by a real entry yet -- no flow writes a PSK
carrier. That is deliberate and cheap: absent means the legacy certificate
path, so this costs no migration, and the import flow lands with the port
work that makes a PSK device reachable at all.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from smartthings_local.protocol import auth as protocol_auth

from custom_components.localthings.const import (
    AUTH_CERTIFICATE,
    AUTH_PSK,
    CONF_AUTH_CARRIER,
    CONF_CA_CERT_PEM,
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_PORT,
    CONF_PSK_IDENTITY,
    CONF_PSK_KEY,
    CONF_PSK_PROFILE,
    PSK_PROFILE_OWNER,
    PSK_PROFILE_PEER,
)
from custom_components.localthings.credentials import (
    DeviceBinding,
    InvalidCredentialConfig,
    authentication_carrier,
    certificate_credentials,
    check_device_binding,
    identity_may_be_suggested_from_doxm,
    normalize_psk_identity,
    normalize_psk_key,
    psk_credentials,
    psk_profile,
    requires_authenticated_device_id,
)
from custom_components.localthings.session import (
    authentication_provider_from_entry,
    create_entry_session,
)

IDENTITY = "3771f8bf-c184-3a2d-d885-e4c9818736d2"
KEY_128 = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
KEY_256 = KEY_128 * 2


def _psk_entry(**extra) -> dict:
    return {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 49154,
        CONF_AUTH_CARRIER: AUTH_PSK,
        CONF_PSK_PROFILE: PSK_PROFILE_OWNER,
        CONF_PSK_IDENTITY: IDENTITY,
        CONF_PSK_KEY: KEY_128,
        **extra,
    }


def _certificate_entry(**extra) -> dict:
    return {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 49154,
        CONF_LEAF_CERT_PEM: "CERTIFICATE",
        CONF_LEAF_KEY_PEM: "PRIVATE KEY",
        **extra,
    }


# ---------------------------------------------------------------------------
# Carrier
# ---------------------------------------------------------------------------


def test_an_entry_that_says_nothing_is_the_legacy_certificate_path() -> None:
    """Why this needs no migration: it is the only thing an entry written
    before the field existed can be."""
    assert authentication_carrier({}) == AUTH_CERTIFICATE


def test_an_unrecognized_carrier_is_refused_rather_than_defaulted() -> None:
    """Defaulting would silently downgrade an entry a newer release wrote
    into the certificate path, which is a connection attempt with the wrong
    credential rather than a clean failure."""
    with pytest.raises(InvalidCredentialConfig):
        authentication_carrier({CONF_AUTH_CARRIER: "kerberos"})


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", [PSK_PROFILE_OWNER, PSK_PROFILE_PEER])
def test_both_psk_profiles_are_modelled(profile: str) -> None:
    assert psk_profile(_psk_entry(**{CONF_PSK_PROFILE: profile})) == profile


def test_a_psk_entry_must_say_which_profile() -> None:
    """No default. The profiles differ in what the credential grants and
    where its identity can come from, so an entry that does not say is not
    one we know how to use."""
    entry = _psk_entry()
    del entry[CONF_PSK_PROFILE]

    with pytest.raises(InvalidCredentialConfig):
        psk_profile(entry)


def test_asking_a_certificate_entry_for_a_psk_profile_is_an_error() -> None:
    with pytest.raises(InvalidCredentialConfig):
        psk_profile(_certificate_entry())


def test_only_ownerpsk_can_take_a_doxm_suggestion() -> None:
    """The one place the two profiles genuinely diverge. `devowneruuid` is
    the OwnerPSK identity, so a plaintext read can prefill it. A peer's
    identity is whatever the bootstrap that installed it chose and appears
    nowhere in DOXM, so an import flow has to take it with the key."""
    assert identity_may_be_suggested_from_doxm(PSK_PROFILE_OWNER) is True
    assert identity_may_be_suggested_from_doxm(PSK_PROFILE_PEER) is False


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_identity_is_canonicalized() -> None:
    assert normalize_psk_identity(f"  {IDENTITY.upper()}  ") == IDENTITY


@pytest.mark.parametrize("value", ["", "not-a-uuid", None, "00000000-0000-0000-0000-000000000000"])
def test_an_unusable_identity_is_refused(value) -> None:
    with pytest.raises(InvalidCredentialConfig):
        normalize_psk_identity(value)


def test_a_zero_byte_in_the_identity_gets_its_own_message() -> None:
    """Upstream's rule, and a real wire constraint: OpenSSL carries the PSK
    identity as a NUL-terminated byte string, so a zero byte truncates it.
    Roughly one UUID in sixteen has one, so this must not read as "your
    credential is malformed" -- the UUID is perfectly well formed, and for a
    generated peer identity the fix is to generate another.
    """
    zero_byte_uuid = "3771f8bf-0000-3a2d-d885-e4c9818736d2"
    assert b"\x00" in UUID(zero_byte_uuid).bytes

    with pytest.raises(InvalidCredentialConfig, match="zero byte"):
        normalize_psk_identity(zero_byte_uuid)


def test_upstream_refuses_the_same_identity_we_do() -> None:
    """The rule above is only worth enforcing early if it is really
    upstream's -- if PskAuth stopped caring, this would be us inventing a
    restriction on which UUIDs a user may own."""
    with pytest.raises(ValueError):
        protocol_auth.PskAuth(
            identity=UUID("3771f8bf-0000-3a2d-d885-e4c9818736d2").bytes,
            key=bytes.fromhex(KEY_128),
        )


@pytest.mark.parametrize("value", [KEY_128, KEY_256])
def test_both_key_sizes_upstream_accepts_are_accepted(value: str) -> None:
    assert normalize_psk_key(value.upper()) == value.lower()


@pytest.mark.parametrize("value", ["", "abcd", KEY_128 + "ff", "zz" * 16, None])
def test_a_key_that_is_not_128_or_256_bit_hex_is_refused(value) -> None:
    with pytest.raises(InvalidCredentialConfig):
        normalize_psk_key(value)


# ---------------------------------------------------------------------------
# Reading credentials off an entry
# ---------------------------------------------------------------------------


def test_certificate_credentials_come_back_unchanged() -> None:
    assert certificate_credentials(_certificate_entry()) == ("CERTIFICATE", "PRIVATE KEY")


def test_psk_credentials_come_back_normalized() -> None:
    entry = _psk_entry(**{CONF_PSK_IDENTITY: IDENTITY.upper(), CONF_PSK_KEY: KEY_128.upper()})

    assert psk_credentials(entry) == (IDENTITY, KEY_128)


@pytest.mark.parametrize("field", [CONF_LEAF_CERT_PEM, CONF_LEAF_KEY_PEM, CONF_CA_CERT_PEM])
def test_a_psk_entry_carrying_certificate_material_is_refused(field: str) -> None:
    """A half-converted entry holds two credentials and no statement of
    which is current; picking one would be a guess about what the appliance
    will accept."""
    with pytest.raises(InvalidCredentialConfig, match="more than one kind"):
        psk_credentials(_psk_entry(**{field: "LEFTOVER"}))


@pytest.mark.parametrize("field", [CONF_PSK_IDENTITY, CONF_PSK_KEY])
def test_a_certificate_entry_carrying_psk_material_is_refused(field: str) -> None:
    with pytest.raises(InvalidCredentialConfig, match="more than one kind"):
        certificate_credentials(_certificate_entry(**{field: "LEFTOVER"}))


@pytest.mark.parametrize("field", [CONF_PSK_IDENTITY, CONF_PSK_KEY])
def test_half_a_psk_credential_is_refused(field: str) -> None:
    entry = _psk_entry()
    del entry[field]

    with pytest.raises(InvalidCredentialConfig, match="incomplete"):
        psk_credentials(entry)


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_certificate_field_is_refused(value: str) -> None:
    with pytest.raises(InvalidCredentialConfig, match="incomplete"):
        certificate_credentials(_certificate_entry(**{CONF_LEAF_KEY_PEM: value}))


# ---------------------------------------------------------------------------
# Which profiles check who answered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", [PSK_PROFILE_OWNER, PSK_PROFILE_PEER])
def test_both_psk_profiles_demand_an_authenticated_device_id(profile: str) -> None:
    """ECDHE-PSK has no server certificate, so there is nothing to pin
    during the handshake and the post-handshake read is the only binding
    either profile can have."""
    assert requires_authenticated_device_id(_psk_entry(**{CONF_PSK_PROFILE: profile})) is True


def test_the_certificate_path_is_unchanged() -> None:
    """It has never done this check, and the library offers it a strictly
    better one (`SamsungServerProfile` pins the subject UUID inside the
    verification callback). Wiring that up is its own change."""
    assert requires_authenticated_device_id(_certificate_entry()) is False
    assert requires_authenticated_device_id({}) is False


def test_matching_device_ids_are_a_match() -> None:
    assert check_device_binding(IDENTITY, IDENTITY) is DeviceBinding.MATCHED


def test_different_device_ids_are_a_mismatch() -> None:
    assert check_device_binding(IDENTITY, "ccfd73b3-aeb4-792a-1100-68f06f5d603b") is (
        DeviceBinding.MISMATCHED
    )


@pytest.mark.parametrize(
    ("expected", "reported"),
    [
        (None, IDENTITY),  # first connection: nothing recorded yet
        (IDENTITY, None),  # authenticated, but served no usable `di`
        (None, None),
        ("", IDENTITY),
    ],
)
def test_an_absent_device_id_is_unproven_rather_than_a_mismatch(expected, reported) -> None:
    """The three-way distinction. Folding this into MISMATCHED would reject
    working credentials on firmware with an unusable identity field, and
    folding it into MATCHED would retire the check silently."""
    assert check_device_binding(expected, reported) is DeviceBinding.UNPROVEN


# ---------------------------------------------------------------------------
# Turning an entry into a provider
# ---------------------------------------------------------------------------


def test_a_certificate_entry_builds_a_certificate_provider() -> None:
    provider = authentication_provider_from_entry(_certificate_entry())

    assert isinstance(provider, protocol_auth.CertificateAuth)
    assert repr(provider) == "CertificateAuth()"


@pytest.mark.parametrize("profile", [PSK_PROFILE_OWNER, PSK_PROFILE_PEER])
def test_both_psk_profiles_build_the_same_kind_of_provider(profile: str) -> None:
    """The profiles differ in what the key grants on the appliance, not in
    how the handshake carries it -- which is why they can share storage."""
    provider = authentication_provider_from_entry(_psk_entry(**{CONF_PSK_PROFILE: profile}))

    assert isinstance(provider, protocol_auth.PskAuth)
    assert repr(provider) == "PskAuth()"


def test_a_provider_never_repeats_its_credential() -> None:
    """Credential material must not reach a log or a diagnostics dump
    through a repr."""
    entry = _psk_entry()

    assert IDENTITY not in repr(authentication_provider_from_entry(entry))
    assert KEY_128 not in repr(authentication_provider_from_entry(entry))


def test_an_entry_with_no_carrier_it_can_serve_is_refused() -> None:
    with pytest.raises(InvalidCredentialConfig):
        authentication_provider_from_entry({CONF_AUTH_CARRIER: "kerberos"})


# ---------------------------------------------------------------------------
# Session construction
# ---------------------------------------------------------------------------


def test_a_session_is_built_but_never_connected() -> None:
    """Construction only: lifetime and I/O stay with the coordinator."""
    session = create_entry_session(_psk_entry())

    assert isinstance(session.auth, protocol_auth.PskAuth)
    assert session.host == "192.0.2.10"
    assert session.port == 49154


@pytest.mark.parametrize("port", [None, 0, 65536, -1, "49154", True])
def test_an_unusable_port_fails_before_any_socket_is_opened(port) -> None:
    """`True` is in here on purpose: it is an `int`, and would otherwise
    pass as port 1."""
    with pytest.raises(InvalidCredentialConfig, match="port"):
        create_entry_session(_psk_entry(**{CONF_PORT: port}))


@pytest.mark.parametrize("host", [None, "", "   ", 42])
def test_an_unusable_host_fails_before_any_socket_is_opened(host) -> None:
    with pytest.raises(InvalidCredentialConfig, match="host"):
        create_entry_session(_psk_entry(**{CONF_HOST: host}))


# ---------------------------------------------------------------------------
# The coordinator's use of it
# ---------------------------------------------------------------------------


def _coordinator(hass, entry_data: dict):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.localthings.const import DOMAIN
    from custom_components.localthings.coordinator import LocalThingsCoordinator

    entry = MockConfigEntry(domain=DOMAIN, data=entry_data, version=4)
    entry.add_to_hass(hass)
    return LocalThingsCoordinator(hass, entry)


def _identity_reporting(device_id: str | None):
    from custom_components.localthings.registry.identity import DeviceIdentity

    return DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="AWM-KR-M64-24-WD86",
        name="Samsung Washer",
        serial=None,
        device_id=device_id,
    )


def test_the_certificate_path_never_runs_the_binding_check(hass) -> None:
    """Unchanged behaviour for every entry that exists today: a certificate
    entry connects to whatever answers, exactly as it always has."""
    from custom_components.localthings.const import CONF_OCF_DEVICE_ID

    coordinator = _coordinator(hass, _certificate_entry(**{CONF_OCF_DEVICE_ID: IDENTITY}))

    # A flatly wrong device id would be a mismatch under any PSK profile.
    coordinator._check_device_binding(_identity_reporting("ccfd73b3-aeb4-792a-1100-68f06f5d603b"))


def test_a_psk_entry_refuses_a_session_to_the_wrong_appliance(hass) -> None:
    from custom_components.localthings.const import CONF_OCF_DEVICE_ID
    from custom_components.localthings.credentials import DeviceIdentityMismatch

    coordinator = _coordinator(hass, _psk_entry(**{CONF_OCF_DEVICE_ID: IDENTITY}))

    with pytest.raises(DeviceIdentityMismatch):
        coordinator._check_device_binding(
            _identity_reporting("ccfd73b3-aeb4-792a-1100-68f06f5d603b")
        )


def test_a_psk_entry_accepts_the_appliance_it_is_bound_to(hass) -> None:
    from custom_components.localthings.const import CONF_OCF_DEVICE_ID

    coordinator = _coordinator(hass, _psk_entry(**{CONF_OCF_DEVICE_ID: IDENTITY}))

    coordinator._check_device_binding(_identity_reporting(IDENTITY))


def test_a_first_connection_has_nothing_to_compare_against(hass) -> None:
    """An entry that has never polled records no device id, so its first
    session cannot be checked -- and refusing it would mean no PSK entry
    could ever connect."""
    coordinator = _coordinator(hass, _psk_entry())

    coordinator._check_device_binding(_identity_reporting(IDENTITY))


def test_an_unreadable_device_id_is_not_treated_as_the_wrong_device(hass) -> None:
    """Firmware with an unusable identity field has told us nothing, which
    is a limitation of the binding rather than evidence against the peer."""
    from custom_components.localthings.const import CONF_OCF_DEVICE_ID

    coordinator = _coordinator(hass, _psk_entry(**{CONF_OCF_DEVICE_ID: IDENTITY}))

    coordinator._check_device_binding(_identity_reporting(None))
    coordinator._check_device_binding(None)


def test_a_mismatch_never_names_the_credential(hass) -> None:
    """The handshake succeeded, so this is not a credential problem and must
    not read as one -- the user cannot fix it by re-entering anything."""
    from custom_components.localthings.const import CONF_OCF_DEVICE_ID
    from custom_components.localthings.credentials import DeviceIdentityMismatch

    coordinator = _coordinator(hass, _psk_entry(**{CONF_OCF_DEVICE_ID: IDENTITY}))

    with pytest.raises(DeviceIdentityMismatch) as raised:
        coordinator._check_device_binding(
            _identity_reporting("ccfd73b3-aeb4-792a-1100-68f06f5d603b")
        )

    message = str(raised.value)
    assert KEY_128 not in message
    assert IDENTITY not in message
