"""Config-flow tests for importing a pre-shared key (issue #435).

The certificate is still tried first. When the appliance refuses it, a
menu offers the AC14K_M CA and both PSK profiles, ordered by what plaintext
doxm hinted. A PSK entry is created only once the key has completed a
handshake *and* the appliance has reported its `/oic/d` di over that
session, because that di is the only binding a PSK entry has.
"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import patch
from uuid import UUID

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import config_flow
from custom_components.localthings.const import (
    AUTH_PSK,
    CONF_AUTH_CARRIER,
    CONF_CA_CERT_PEM,
    CONF_CA_KEY_PEM,
    CONF_DEVICE_KEY,
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_OCF_DEVICE_ID,
    CONF_PORT,
    CONF_PSK_IDENTITY,
    CONF_PSK_KEY,
    CONF_PSK_PROFILE,
    DOMAIN,
    PSK_PROFILE_OWNER,
    PSK_PROFILE_PEER,
)
from custom_components.localthings.credentials import psk_credentials
from custom_components.localthings.probing import CredentialHint, HostProbe

from .conftest import MOCK_HOST
from .test_config_flow import WASHER_DEVICE0

DEVICE_ID = "6f0b6c1e-5a41-4f7e-9c2d-3b8a1d4e7f10"
OWNER_UUID = "1a2b3c4d-5e6f-4a1b-8c9d-aebfc1d2e3f4"
PEER_UUID = "9e8d7c6b-5a49-4837-a625-14f3e2d1c0b9"
KEY = "00112233445566778899aabbccddeeff"
PORT = 58227


class PskAppliance:
    """Stands in for `config_flow.DtlsTransport`: an appliance holding one PSK.

    A certificate is always refused, as on the WD86 in #405. A PSK
    completes the handshake only when both identity and key match.
    """

    identity: ClassVar[bytes] = b""
    key: ClassVar[bytes] = b""
    device_id: ClassVar[str | None] = DEVICE_ID
    opened: ClassVar[list[PskAppliance]] = []

    def __init__(self, host, port, *, cert_pem=None, key_pem=None, auth=None, **kwargs):
        self.port = port
        self.cert_pem = cert_pem
        self.auth = auth
        PskAppliance.opened.append(self)

    def connect(self):
        if self.auth is None:
            raise ConnectionError(
                "DTLS handshake error: [('SSL routines', '', 'tlsv1 alert unknown ca')]"
            )
        if (self.auth.identity, self.auth.key) != (self.identity, self.key):
            raise ConnectionError(
                "DTLS handshake error: [('SSL routines', '', 'tlsv1 alert unknown psk identity')]"
            )

    def read(self, path, timeout=15.0):
        if list(path) == ["oic", "d"]:
            return 0x45, ({"di": self.device_id} if self.device_id else {})
        if list(path) == ["device", "0"]:
            return 0x45, WASHER_DEVICE0
        return 0x84, None

    def close(self):
        pass


class _Psk:
    """`PskAuth` keeps its credential private; this exposes it to the fake."""

    def __init__(self, identity: str, key: str) -> None:
        self.identity = UUID(identity).bytes
        self.key = bytes.fromhex(key)


def _hold(identity: str, key: str = KEY) -> None:
    PskAppliance.identity = UUID(identity).bytes
    PskAppliance.key = bytes.fromhex(key)


@pytest.fixture
def appliance(monkeypatch):
    PskAppliance.opened = []
    PskAppliance.device_id = DEVICE_ID
    _hold(OWNER_UUID)
    scan = HostProbe(MOCK_HOST, [PORT], [PORT], advertised=(PORT,))
    monkeypatch.setattr(config_flow.probing, "look", lambda host: scan)
    monkeypatch.setattr(config_flow, "DtlsTransport", PskAppliance)
    monkeypatch.setattr(config_flow, "psk_provider", _Psk)
    monkeypatch.setattr(config_flow, "_source_port_bindable", lambda host, port: True)
    monkeypatch.setattr(config_flow, "_fetch_samsung_uuid", lambda: "test-uuid")
    monkeypatch.setattr(config_flow, "_mint_self_signed", lambda uuid: ("SELFSIGNED", "SELFKEY"))
    # No real diagnostic handshake: the refusal's alert is in the exception.
    monkeypatch.setattr(config_flow, "_diagnostic_alert", lambda *a, **k: None)
    with patch("custom_components.localthings.async_setup_entry", return_value=True):
        yield PskAppliance


def _hint(monkeypatch, hint: CredentialHint) -> None:
    monkeypatch.setattr(config_flow.probing, "read_credential_hint", lambda host, port=None: hint)


async def _refused(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: MOCK_HOST})


async def _choose(hass: HomeAssistant, result, option: str):
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": option}
    )


async def test_psk_hint_says_so_on_the_menu(hass: HomeAssistant, monkeypatch, appliance) -> None:
    _hint(monkeypatch, CredentialHint(sct=1))
    result = await _refused(hass)
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "credential_psk"
    assert result["menu_options"] == ["psk_owner", "psk_peer", "fallback_ca"]


@pytest.mark.parametrize("sct", [None, 8, 9])
async def test_no_psk_hint_still_puts_the_ca_last(
    hass: HomeAssistant, monkeypatch, appliance, sct
) -> None:
    """Unknown, certificate-only and mixed bits change the wording, not the
    order: no refusing appliance has been reported to accept AC14K_M."""
    _hint(monkeypatch, CredentialHint(sct=sct))
    result = await _refused(hass)
    assert result["step_id"] == "credential"
    assert result["menu_options"] == ["psk_owner", "psk_peer", "fallback_ca"]


async def test_hint_is_read_on_the_appliances_own_plaintext_port(
    hass: HomeAssistant, monkeypatch, appliance
) -> None:
    """Where another OCF stack answers 5683 (#540), the doxm that orders the
    menu is the appliance's, on the plaintext port the probe found it on."""
    scan = HostProbe(MOCK_HOST, [PORT], [PORT], advertised=(PORT,), plaintext_port=60137)
    monkeypatch.setattr(config_flow.probing, "look", lambda host: scan)
    asked: list[int | None] = []

    def _read(host, port=None):
        asked.append(port)
        return CredentialHint(sct=1)

    monkeypatch.setattr(config_flow.probing, "read_credential_hint", _read)

    result = await _refused(hass)

    assert result["step_id"] == "credential_psk"
    assert asked == [60137]


async def test_owner_psk_creates_a_psk_entry_bound_to_the_proven_di(
    hass: HomeAssistant, monkeypatch, appliance
) -> None:
    _hint(monkeypatch, CredentialHint(sct=1, owner_uuid=OWNER_UUID))
    result = await _choose(hass, await _refused(hass), "psk_owner")
    assert result["type"] == FlowResultType.FORM
    # devowneruuid is offered as the identity, never the key.
    schema = result["data_schema"]
    assert schema is not None
    suggested = {
        str(field): (field.description or {}).get("suggested_value") for field in schema.schema
    }
    assert suggested == {CONF_PSK_IDENTITY: OWNER_UUID, CONF_PSK_KEY: None}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PSK_IDENTITY: OWNER_UUID.upper(), CONF_PSK_KEY: KEY.upper()},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_AUTH_CARRIER] == AUTH_PSK
    assert data[CONF_PSK_PROFILE] == PSK_PROFILE_OWNER
    assert data[CONF_PSK_IDENTITY] == OWNER_UUID
    assert data[CONF_PSK_KEY] == KEY
    assert data[CONF_OCF_DEVICE_ID] == DEVICE_ID
    assert data[CONF_PORT] == PORT
    for field in (CONF_CA_CERT_PEM, CONF_CA_KEY_PEM, CONF_LEAF_CERT_PEM, CONF_LEAF_KEY_PEM):
        assert field not in data
    # What the coordinator will build its session from.
    assert psk_credentials(data) == (OWNER_UUID, KEY)


async def test_peer_psk_is_never_prefilled_from_doxm(
    hass: HomeAssistant, monkeypatch, appliance
) -> None:
    _hold(PEER_UUID)
    _hint(monkeypatch, CredentialHint(sct=1, owner_uuid=OWNER_UUID))
    result = await _choose(hass, await _refused(hass), "psk_peer")
    schema = result["data_schema"]
    assert schema is not None
    assert all(not (field.description or {}).get("suggested_value") for field in schema.schema)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PSK_IDENTITY: PEER_UUID, CONF_PSK_KEY: KEY}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PSK_PROFILE] == PSK_PROFILE_PEER


async def test_wrong_key_is_reported_as_a_rejected_psk(
    hass: HomeAssistant, monkeypatch, appliance
) -> None:
    _hint(monkeypatch, CredentialHint(sct=1))
    result = await _choose(hass, await _refused(hass), "psk_owner")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PSK_IDENTITY: OWNER_UUID, CONF_PSK_KEY: "ff" * 16}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "psk_rejected"}
    # The key the user typed is not put back into the form.
    schema = result["data_schema"]
    assert schema is not None
    suggested = {
        str(field): (field.description or {}).get("suggested_value") for field in schema.schema
    }
    assert suggested[CONF_PSK_KEY] is None


async def test_missing_di_refuses_the_entry(hass: HomeAssistant, monkeypatch, appliance) -> None:
    """A key that works on an appliance that won't say which it is creates
    nothing: there would be no binding for the coordinator to check."""
    PskAppliance.device_id = None
    _hint(monkeypatch, CredentialHint(sct=1))
    result = await _choose(hass, await _refused(hass), "psk_owner")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PSK_IDENTITY: OWNER_UUID, CONF_PSK_KEY: KEY}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "identity_unproven"}
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    ("identity", "key", "errors"),
    [
        ("not-a-uuid", KEY, {CONF_PSK_IDENTITY: "invalid_psk_identity"}),
        ("00000000-0000-0000-0000-000000000000", KEY, {CONF_PSK_IDENTITY: "invalid_psk_identity"}),
        (
            "12345678-0000-4000-8000-123456789abc",
            KEY,
            {CONF_PSK_IDENTITY: "psk_identity_zero_byte"},
        ),
        (OWNER_UUID, "abc", {CONF_PSK_KEY: "invalid_psk_key"}),
        ("x", "y", {CONF_PSK_IDENTITY: "invalid_psk_identity", CONF_PSK_KEY: "invalid_psk_key"}),
    ],
)
async def test_malformed_credentials_never_reach_the_appliance(
    hass: HomeAssistant, monkeypatch, appliance, identity, key, errors
) -> None:
    if errors.get(CONF_PSK_IDENTITY) == "psk_identity_zero_byte":
        from smartthings_local.protocol.auth import PskAuth

        def unsupported(_identity):
            raise ValueError("binary identity backend unavailable")

        monkeypatch.setattr(PskAuth, "validate_identity", unsupported)
    _hint(monkeypatch, CredentialHint(sct=1))
    result = await _choose(hass, await _refused(hass), "psk_owner")
    opened = len(PskAppliance.opened)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PSK_IDENTITY: identity, CONF_PSK_KEY: key}
    )
    assert result["errors"] == errors
    assert len(PskAppliance.opened) == opened


async def test_a_psk_entry_is_not_reused_for_the_next_appliance(
    hass: HomeAssistant, appliance
) -> None:
    """Only a certificate is shared between appliances. With nothing but a
    PSK entry installed, the next setup starts from the first-device form."""
    MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "10.0.0.9",
            CONF_PORT: PORT,
            CONF_AUTH_CARRIER: AUTH_PSK,
            CONF_PSK_PROFILE: PSK_PROFILE_OWNER,
            CONF_PSK_IDENTITY: OWNER_UUID,
            CONF_PSK_KEY: KEY,
        },
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "user"


def _psk_entry(**extra) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"localthings_{DEVICE_ID}",
        data={
            CONF_HOST: "10.0.0.9",
            CONF_PORT: 41820,
            CONF_AUTH_CARRIER: AUTH_PSK,
            CONF_PSK_PROFILE: PSK_PROFILE_OWNER,
            CONF_PSK_IDENTITY: OWNER_UUID,
            CONF_PSK_KEY: KEY,
            CONF_DEVICE_KEY: DEVICE_ID,
            CONF_OCF_DEVICE_ID: DEVICE_ID,
            **extra,
        },
    )


async def test_reconfigure_a_psk_entry_keeps_its_credential(hass: HomeAssistant, appliance) -> None:
    """Laundry ports move across a power cycle (#435), so reconfigure has to
    reach a PSK entry with its own key and add no certificate to it."""
    entry = _psk_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: MOCK_HOST}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == MOCK_HOST
    assert entry.data[CONF_PORT] == PORT
    assert CONF_LEAF_CERT_PEM not in entry.data
    assert psk_credentials(entry.data) == (OWNER_UUID, KEY)
    assert all(session.auth is not None for session in PskAppliance.opened)


async def test_reconfigure_a_psk_entry_needs_a_matching_di(hass: HomeAssistant, appliance) -> None:
    entry = _psk_entry()
    entry.add_to_hass(hass)
    PskAppliance.device_id = "0d9c8b7a-6f5e-4d3c-8b2a-19f8e7d6c5b4"
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: MOCK_HOST}
    )
    assert result["errors"] == {"base": "wrong_device"}
    assert entry.data[CONF_HOST] == "10.0.0.9"


def test_psk_alerts_are_not_read_as_a_certificate_rejection() -> None:
    """decrypt_error means "bad certificate" only when one was offered."""
    scan = HostProbe(MOCK_HOST, [PORT], [PORT])
    failure: list[tuple[int, Exception]] = [(PORT, ConnectionError("x"))]
    for alert in ("unknown_psk_identity", "decrypt_error", "bad_record_mac"):
        err = config_flow._classify_handshake_failure(
            MOCK_HOST, scan, failure, {PORT: alert}, psk=True
        )
        assert isinstance(err, config_flow.PskRejected), alert
    err = config_flow._classify_handshake_failure(MOCK_HOST, scan, failure, {PORT: "decrypt_error"})
    assert isinstance(err, config_flow.CertRejected)


async def test_every_psk_screen_links_the_tracking_issue(
    hass: HomeAssistant, monkeypatch, appliance
) -> None:
    """Each PSK screen's text links #435 through {issue_url}, so every one
    of them has to be given it."""
    from custom_components.localthings.const import PSK_TRACKING_ISSUE_URL

    _hint(monkeypatch, CredentialHint(sct=1))
    menu = await _refused(hass)
    assert menu["description_placeholders"]["issue_url"] == PSK_TRACKING_ISSUE_URL
    form = await _choose(hass, menu, "psk_peer")
    assert form["description_placeholders"]["issue_url"] == PSK_TRACKING_ISSUE_URL
