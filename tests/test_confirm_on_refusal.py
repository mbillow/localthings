"""Refused writes require descriptor opt-in and strict live confirmation."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import CONF_HOST, DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type.airconditioner import REGISTRY
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity, discover
from custom_components.localthings.registry.entities import ClimateDesc, NumberDesc
from custom_components.localthings.transport import Transport

POWER = "x.com.samsung.da.power"


@pytest.fixture
def power_coordinator(hass, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "192.0.2.1"})
    entry.add_to_hass(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    session = MagicMock()
    session.write.return_value = (0x80, None)
    coordinator._session = cast(Transport, session)
    coordinator.async_request_refresh = AsyncMock()
    monkeypatch.setattr("custom_components.localthings.coordinator.asyncio.sleep", AsyncMock())
    return coordinator, session


@pytest.mark.parametrize("power", ["On", "Off"])
async def test_bad_request_is_accepted_only_after_live_confirmation(power_coordinator, power):
    coordinator, session = power_coordinator
    session.read.return_value = (0x45, {POWER: power})
    await coordinator._async_put(
        ["power", "vs", "0"], {POWER: power}, "/power/vs/0", confirm_on_refusal=True
    )
    session.read.assert_called_once_with(["power", "vs", "0"], timeout=coordinator._POST_TIMEOUT_S)
    session.write.assert_called_once()


@pytest.mark.parametrize(
    "readback", [(0x45, {POWER: "Off"}), (0x80, {POWER: "On"}), (0x45, None), (0x45, {})]
)
async def test_unconfirmed_write_is_still_refused(power_coordinator, readback):
    coordinator, session = power_coordinator
    coordinator._observe.apply("/power/vs/0", {POWER: "On"}, source="poll")
    session.read.return_value = readback
    with pytest.raises(HomeAssistantError, match="refused"):
        await coordinator._async_put(
            ["power", "vs", "0"], {POWER: "On"}, "/power/vs/0", confirm_on_refusal=True
        )


async def test_failed_read_is_not_success(power_coordinator):
    coordinator, session = power_coordinator
    session.read.side_effect = TimeoutError
    with pytest.raises(HomeAssistantError):
        await coordinator._async_put(
            ["power", "vs", "0"], {POWER: "On"}, "/power/vs/0", confirm_on_refusal=True
        )


@pytest.mark.parametrize("code,opt_in", [(0x83, True), (0xA0, True), (0x80, False)])
async def test_other_refusals_are_unchanged(power_coordinator, code, opt_in):
    coordinator, session = power_coordinator
    session.write.return_value = (code, None)
    with pytest.raises(HomeAssistantError):
        await coordinator._async_put(
            ["power", "vs", "0"], {POWER: "On"}, "/power/vs/0", confirm_on_refusal=opt_in
        )
    session.read.assert_not_called()


async def test_accepted_write_needs_no_workaround(power_coordinator):
    coordinator, session = power_coordinator
    session.write.return_value = (0x44, None)
    await coordinator._async_put(
        ["power", "vs", "0"], {POWER: "On"}, "/power/vs/0", confirm_on_refusal=True
    )
    session.read.assert_not_called()


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("failure", ["timeout", "error_code", "no_session", "invalid_rep"])
async def test_strict_confirmation_does_not_change_default_read_failure_policy(
    power_coordinator, strict, failure
):
    coordinator, session = power_coordinator
    if failure == "timeout":
        session.read.side_effect = TimeoutError
    elif failure == "error_code":
        session.read.return_value = (0x80, {POWER: "On"})
    elif failure == "no_session":
        coordinator._session = None
    else:
        session.read.return_value = (0x45, None)
    assert await coordinator._took_effect(["power", "vs", "0"], {POWER: "On"}, strict=strict) is (
        not strict
    )


@pytest.mark.parametrize("opt_in", [False, True])
async def test_opt_in_is_generic_and_reaches_dispatch(power_coordinator, opt_in):
    coordinator, session = power_coordinator
    # A future board can opt a different resource in without coordinator edits.
    href = "/example/vs/1"
    desc = NumberDesc(
        key="example",
        field="value",
        confirm_on_refusal=opt_in,
        write_fn=lambda payload, rep, href=None, resources=None: (
            ["example", "vs", "1"],
            {"value": payload},
        ),
    )
    bound = BoundEntity(href=href, capability=Capability(href=href), desc=desc)
    session.read.return_value = (0x45, {"value": 7})
    if opt_in:
        await coordinator.async_send_command(bound, 7)
        session.read.assert_called_once()
    else:
        with pytest.raises(HomeAssistantError):
            await coordinator.async_send_command(bound, 7)
        session.read.assert_not_called()
    session.write.assert_called_once_with(
        ["example", "vs", "1"], {"value": 7}, timeout=coordinator._POST_TIMEOUT_S
    )


@pytest.mark.parametrize("model", ["ARTIK051_PRAC_20K|board", "TP6X_RAC_16K", ""])
@pytest.mark.parametrize("identity_href", ["/information/vs/0", "/oic/p"])
def test_all_ac_climates_opt_in_independently_of_model(model, identity_href):
    resources = {
        "/mode/vs/0": {"x.com.samsung.da.modes": ["Cool"]},
        identity_href: {
            ("mnmo" if identity_href == "/oic/p" else "x.com.samsung.da.modelNum"): model
        },
    }
    bound = discover(resources, REGISTRY.capabilities)
    climates = [b for b in bound if isinstance(b.desc, ClimateDesc)]
    assert len(climates) == 1
    assert climates[0].desc.confirm_on_refusal is True
    assert all(not b.desc.confirm_on_refusal for b in bound if not isinstance(b.desc, ClimateDesc))


async def test_same_state_refusal_is_a_confirmed_no_op(power_coordinator):
    coordinator, session = power_coordinator
    coordinator._observe.apply("/power/vs/0", {POWER: "On"}, source="poll")
    session.read.return_value = (0x45, {POWER: "On"})
    await coordinator._async_put(
        ["power", "vs", "0"], {POWER: "On"}, "/power/vs/0", confirm_on_refusal=True
    )
    session.read.assert_called_once()
    session.write.assert_called_once()
