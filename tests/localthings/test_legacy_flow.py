"""Config-flow tests for the legacy 8888 branch (issue #168).

The user types an address and nothing else: the flow finds the bridge on
TCP 8888, mints the same self-signed leaf the DTLS path now mints by
default, and then asks for the one credential that path never needs -- a
device token the appliance itself issues through a callback. These cover
that it routes there at all, both ways of getting a token, and what the
entry ends up carrying.

No AC14K_M CA is involved anywhere here: this family does not authenticate
the certificate at all (see the comment in `async_step_user`), so the leaf
is self-signed and the token is what authorizes.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.localthings import probing
from custom_components.localthings.const import (
    CONF_CA_CERT_PEM,
    CONF_CA_KEY_PEM,
    CONF_DEVICE_TOKEN,
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEGACY_FAMILY,
    CONF_PORT,
    CONF_TRANSPORT,
    DOMAIN,
    TRANSPORT_LEGACY_HTTP,
)

from .conftest import MOCK_HOST

LEGACY_DEVICE = {
    "port": 8888,
    "device_key": "05gd5esk700166f",
    "ocf_device_id": None,
    "serial": "05GD5ESK700166F",
    "model": "TP6X_WW6500",
    "manufacturer": "Samsung",
    "device_type_name": "washer",
    "device_type_recognized": True,
    "description": "TP6X_WASHER",
    # The 8888 bridge exposes no /wirelessinfo/vs/0, so `resolve_mac` finds
    # nothing -- the same None a DTLS board without that resource gives.
    "mac": None,
}


@pytest.fixture
def legacy_bridge():
    """An appliance that answers on 8888 and issues a token when asked."""
    with (
        patch(
            "custom_components.localthings.probing.look",
            return_value=probing.HostProbe(
                host=MOCK_HOST, candidates=[], confirmed=[], legacy_http=True
            ),
        ),
        patch(
            "custom_components.localthings.config_flow._mint_self_signed_credentials",
            return_value=("FULLCHAIN", "LEAFKEY"),
        ) as mint,
        patch(
            "custom_components.localthings.config_flow.obtain_device_token",
            return_value="tok123456",
        ) as token,
        patch(
            "custom_components.localthings.config_flow._probe_legacy",
            return_value=LEGACY_DEVICE,
        ) as probe,
    ):
        yield token, probe, mint


async def _start(hass: HomeAssistant):
    """Walk the first step, which asks for an address and nothing else."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: MOCK_HOST})


async def test_a_bridge_on_8888_routes_to_the_token_step(
    hass: HomeAssistant, legacy_bridge
) -> None:
    """And gets there without a DTLS handshake: probing.look already found
    no CoAP server, so there is nothing to handshake with."""
    with patch("custom_components.localthings.config_flow._handshake_and_read") as dtls:
        result = await _start(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "legacy_token"
    assert dtls.called is False


async def test_an_empty_field_asks_the_appliance_for_a_token(
    hass: HomeAssistant, legacy_bridge
) -> None:
    token, _, _mint = legacy_bridge
    result = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: ""}
    )

    assert token.called
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_TOKEN] == "tok123456"


async def test_a_pasted_token_is_used_as_is(hass: HomeAssistant, legacy_bridge) -> None:
    """The callback needs the appliance awake and inbound 8889 reaching
    Home Assistant; neither is true on every network."""
    token, _, _mint = legacy_bridge
    result = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: "  pasted1234  "}
    )

    assert token.called is False
    assert result["data"][CONF_DEVICE_TOKEN] == "pasted1234"


async def test_the_entry_records_the_transport_and_the_family(
    hass: HomeAssistant, legacy_bridge
) -> None:
    """`create_transport` reads all three back; without them the coordinator
    would open a DTLS session against an appliance that has no DTLS."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: ""}
    )

    data = result["data"]
    assert data[CONF_TRANSPORT] == TRANSPORT_LEGACY_HTTP
    assert data[CONF_PORT] == 8888
    assert data[CONF_LEGACY_FAMILY] == "TP6X_WASHER"
    assert data[CONF_LEAF_CERT_PEM] == "FULLCHAIN"


async def test_no_ca_is_asked_for_and_none_is_stored(hass: HomeAssistant, legacy_bridge) -> None:
    """This family authenticates nothing about the certificate, so the leaf
    is the self-signed one and the entry carries no CA.

    Measured against a TP6X_WW6500 on 8888: a self-signed leaf, one bearing
    a stranger's UUID and one with no `uuid:` RDN at all are each answered
    200, while a wrong or absent device token is 401 even under an
    AC14K_M-signed leaf. Storing a CA here would mean asking the user for
    material that provably does nothing.
    """
    _, _, mint = legacy_bridge
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: ""}
    )

    assert mint.called
    assert result["data"][CONF_CA_CERT_PEM] == ""
    assert result["data"][CONF_CA_KEY_PEM] == ""


async def test_no_token_re_shows_the_form_with_advice(hass: HomeAssistant, legacy_bridge) -> None:
    """Rather than ending the flow: a token obtained another way still
    works, and the message says so."""
    token, probe, _mint = legacy_bridge
    token.return_value = None
    result = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: ""}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "legacy_token"
    assert result["errors"] == {"base": "token_not_received"}
    assert probe.called is False


async def test_a_failed_probe_keeps_the_user_in_the_step(
    hass: HomeAssistant, legacy_bridge
) -> None:
    from custom_components.localthings.config_flow import UnexpectedResponse

    _, probe, _mint = legacy_bridge
    probe.side_effect = UnexpectedResponse("not a device we understand")
    result = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: "tok123456"}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unexpected_response"}


async def test_the_same_appliance_is_not_added_twice(hass: HomeAssistant, legacy_bridge) -> None:
    result = await _start(hass)
    await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_DEVICE_TOKEN: ""})

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: ""}
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_an_unmapped_family_is_confirmed_like_an_unknown_device(
    hass: HomeAssistant, legacy_bridge
) -> None:
    """No envelope table means no borrowed one: the flow asks, exactly as it
    does for a DTLS device nothing routes."""
    _, probe, _mint = legacy_bridge
    probe.return_value = {
        **LEGACY_DEVICE,
        "description": "TP6X_DRYER",
        "device_type_name": None,
        "device_type_recognized": False,
    }
    result = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: "tok123456"}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm_unknown_type"


def test_probe_reads_an_unmapped_family_for_identity_only(monkeypatch) -> None:
    from custom_components.localthings import config_flow

    families: list[str | None] = []

    class _Transport:
        def __init__(self, *args, family=None, **kwargs):
            families.append(family)

        def connect(self):
            pass

        def close(self):
            pass

    def _read(transport, host, port):
        return {**LEGACY_DEVICE, "description": "TP6X_DRYER"}

    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.LegacyHttpTransport", _Transport
    )
    monkeypatch.setattr(config_flow, "_read_device", _read)

    info = config_flow._probe_legacy(MOCK_HOST, "C", "K", "tok")

    assert families == [None]
    assert info["device_type_recognized"] is False
    assert info["device_type_name"] is None


def test_probe_reads_a_mapped_family_through_its_table(monkeypatch) -> None:
    from custom_components.localthings import config_flow

    families: list[str | None] = []

    class _Transport:
        def __init__(self, *args, family=None, **kwargs):
            families.append(family)

        def connect(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.LegacyHttpTransport", _Transport
    )
    monkeypatch.setattr(config_flow, "_read_device", lambda transport, host, port: LEGACY_DEVICE)

    info = config_flow._probe_legacy(MOCK_HOST, "C", "K", "tok")

    assert families == [None, "TP6X_WASHER"]
    assert info["device_type_recognized"] is True


def _legacy_entry(hass: HomeAssistant, **extra):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.localthings.const import CONF_DEVICE_KEY, CONF_LEAF_KEY_PEM, CONF_SERIAL

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: MOCK_HOST,
            CONF_PORT: 8888,
            CONF_LEAF_CERT_PEM: "FULLCHAIN",
            CONF_LEAF_KEY_PEM: "LEAFKEY",
            CONF_TRANSPORT: TRANSPORT_LEGACY_HTTP,
            CONF_LEGACY_FAMILY: "TP6X_WASHER",
            CONF_DEVICE_TOKEN: "old-token",
            CONF_DEVICE_KEY: LEGACY_DEVICE["device_key"],
            CONF_SERIAL: LEGACY_DEVICE["serial"],
            **extra,
        },
        unique_id=f"localthings_{LEGACY_DEVICE['device_key']}",
        version=4,
    )
    entry.add_to_hass(hass)
    return entry


async def test_a_pasted_token_the_appliance_rejects_says_so(
    hass: HomeAssistant, legacy_bridge
) -> None:
    from custom_components.localthings.transport import AuthRejected

    _, probe, _mint = legacy_bridge
    probe.side_effect = AuthRejected("401")
    result = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: "stale"}
    )

    assert result["step_id"] == "legacy_token"
    assert result["errors"] == {"base": "invalid_token"}


async def test_a_busy_callback_port_says_so(hass: HomeAssistant, legacy_bridge) -> None:
    """Usually an earlier exchange still waiting out its ninety seconds."""
    from custom_components.localthings.legacy_http_token import CallbackPortUnavailable

    token, _, _mint = legacy_bridge
    token.side_effect = CallbackPortUnavailable("Address already in use")
    result = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: ""}
    )

    assert result["step_id"] == "legacy_token"
    assert result["errors"] == {"base": "callback_port_in_use"}


async def test_reauth_stores_the_new_token(hass: HomeAssistant, legacy_bridge) -> None:
    entry = _legacy_entry(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: "new-token"}
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_DEVICE_TOKEN] == "new-token"


async def test_reauth_refuses_a_token_from_a_different_appliance(
    hass: HomeAssistant, legacy_bridge
) -> None:
    _, probe, _mint = legacy_bridge
    probe.return_value = {**LEGACY_DEVICE, "device_key": "someone-else", "serial": "OTHER"}
    entry = _legacy_entry(hass)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: "new-token"}
    )

    assert result["errors"] == {"base": "wrong_device"}
    assert entry.data[CONF_DEVICE_TOKEN] == "old-token"


async def test_reauth_is_only_for_the_legacy_family(hass: HomeAssistant) -> None:
    entry = _legacy_entry(hass, **{CONF_TRANSPORT: "dtls"})

    result = await entry.start_reauth_flow(hass)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_unsupported"


async def test_reconfigure_moves_a_legacy_entry_with_its_own_token(
    hass: HomeAssistant, legacy_bridge
) -> None:
    _, probe, _mint = legacy_bridge
    entry = _legacy_entry(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.99"}
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert probe.call_args.args == ("10.0.0.99", "FULLCHAIN", "LEAFKEY", "old-token")
    assert entry.data[CONF_HOST] == "10.0.0.99"
    assert entry.data[CONF_PORT] == 8888


async def test_reconfigure_of_a_dtls_entry_onto_an_8888_appliance_is_the_wrong_device(
    hass: HomeAssistant, legacy_bridge
) -> None:
    entry = _legacy_entry(hass, **{CONF_TRANSPORT: "dtls"})

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.99"}
    )

    assert result["errors"] == {"base": "wrong_device"}
