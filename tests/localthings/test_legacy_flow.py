"""Config-flow tests for the legacy 8888 branch (issue #168).

The user types an address and nothing else: the flow finds the bridge on
TCP 8888, mints the same leaf the DTLS path mints, and then asks for the one
credential that path never needs -- a device token the appliance itself
issues through a callback. These cover that it routes there at all, both
ways of getting a token, and what the entry ends up carrying.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

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

from .conftest import MOCK_CA_CERT_PEM, MOCK_CA_KEY_PEM, MOCK_HOST

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
}


@pytest.fixture
def legacy_bridge():
    """An appliance that answers on 8888 and issues a token when asked."""
    with (
        patch(
            "custom_components.localthings.config_flow._legacy_http_open",
            return_value=True,
        ),
        patch(
            "custom_components.localthings.config_flow._mint_credentials",
            return_value=("FULLCHAIN", "LEAFKEY"),
        ),
        patch(
            "custom_components.localthings.config_flow.obtain_device_token",
            return_value="tok123456",
        ) as token,
        patch(
            "custom_components.localthings.config_flow._probe_legacy",
            return_value=LEGACY_DEVICE,
        ) as probe,
    ):
        yield token, probe


async def _start(hass: HomeAssistant, *, with_ca: bool = True):
    """Walk the first step. `with_ca` off is the shape the form takes once
    an entry exists: the CA is reused and only an address is asked for."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    user_input = {CONF_HOST: MOCK_HOST}
    if with_ca:
        user_input |= {CONF_CA_CERT_PEM: MOCK_CA_CERT_PEM, CONF_CA_KEY_PEM: MOCK_CA_KEY_PEM}
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_a_bridge_on_8888_routes_to_the_token_step(
    hass: HomeAssistant, legacy_bridge
) -> None:
    """And gets there without the DTLS probe running at all: the two
    families are mutually exclusive, and that probe is the expensive one."""
    with patch("custom_components.localthings.config_flow._probe_and_validate") as dtls:
        result = await _start(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "legacy_token"
    assert dtls.called is False


async def test_an_empty_field_asks_the_appliance_for_a_token(
    hass: HomeAssistant, legacy_bridge
) -> None:
    token, _ = legacy_bridge
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
    token, _ = legacy_bridge
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


async def test_no_token_re_shows_the_form_with_advice(hass: HomeAssistant, legacy_bridge) -> None:
    """Rather than ending the flow: a token obtained another way still
    works, and the message says so."""
    token, probe = legacy_bridge
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

    _, probe = legacy_bridge
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

    result = await _start(hass, with_ca=False)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_TOKEN: ""}
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
