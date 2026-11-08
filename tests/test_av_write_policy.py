"""The coordinator's write policy for AV boards, with no platform named.

Commands that must not be resent after a lost reply, and prepared payloads.
"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import CONF_HOST, DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import NumberDesc
from custom_components.localthings.transport import Transport

HREF = "/capability/mediaPlayback/main/0"
PATH = ["capability", "mediaPlayback", "main", "0"]


@pytest.fixture
def coordinator(hass, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "192.0.2.1"})
    entry.add_to_hass(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    session = MagicMock()
    session.write.return_value = (0x44, None)
    coordinator._session = cast(Transport, session)
    coordinator.async_request_refresh = AsyncMock()
    monkeypatch.setattr("custom_components.localthings.coordinator.asyncio.sleep", AsyncMock())
    return coordinator, session


def _bound(**desc_fields) -> BoundEntity:
    desc = NumberDesc(
        key="test",
        field="position",
        write_fn=lambda value, rep, href=None: (list(PATH), {"position": value}),
        **desc_fields,
    )
    return BoundEntity(href=HREF, capability=Capability(href=HREF, entities=(desc,)), desc=desc)


async def test_a_resend_unsafe_write_is_not_resent_after_a_lost_reply(coordinator, monkeypatch):
    coordinator, session = coordinator
    session.write.side_effect = TimeoutError("reply lost")
    reconnect = MagicMock()
    monkeypatch.setattr(coordinator, "_connect_session", reconnect)
    bound = _bound(resend_unsafe_fn=lambda payload: True)
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_send_command(bound, 15_000)
    assert err.value.translation_key == "command_failed"
    session.write.assert_called_once()
    reconnect.assert_not_called()


async def test_a_resend_unsafe_write_that_never_left_is_retried(coordinator, monkeypatch):
    """A dead session or failed handshake sent nothing, so a retry is safe."""
    coordinator, session = coordinator
    coordinator._session = None
    coordinator._RECONNECT_PAUSE_S = 0
    attempts = []

    def connect():
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError("handshake timed out")
        coordinator._session = cast(Transport, session)

    monkeypatch.setattr(coordinator, "_connect_session", connect)
    await coordinator.async_send_command(_bound(resend_unsafe_fn=lambda payload: True), 15_000)
    session.write.assert_called_once_with(
        PATH, {"position": 15_000}, timeout=coordinator._POST_TIMEOUT_S
    )


async def test_cache_after_ack_holds_the_optimistic_value_until_acknowledged(coordinator):
    coordinator, session = coordinator
    coordinator._observe.apply(HREF, {"position": 1}, source="poll")
    session.write.return_value = (0x80, None)
    bound = _bound(cache_after_ack_fn=lambda payload: True)
    with pytest.raises(HomeAssistantError):
        await coordinator.async_send_command(bound, 2, optimistic_body={"position": 2})
    assert coordinator.resource(HREF)["position"] == 1

    session.write.return_value = (0x44, None)
    await coordinator.async_send_command(bound, 2, optimistic_body={"position": 2})
    assert coordinator.resource(HREF)["position"] == 2


async def test_a_prepared_payload_must_keep_its_path(coordinator):
    coordinator, session = coordinator
    paths = iter([list(PATH), ["elsewhere"]])
    desc = NumberDesc(
        key="test",
        field="position",
        write_fn=lambda value, rep, href=None: (next(paths), {"position": value}),
    )
    bound = BoundEntity(href=HREF, capability=Capability(href=HREF, entities=(desc,)), desc=desc)
    await coordinator.async_send_command(bound, 0, prepare_payload=lambda: 5)
    session.write.assert_not_called()


async def test_a_prepared_payload_is_what_goes_out(coordinator):
    coordinator, session = coordinator
    await coordinator.async_send_command(_bound(), 0, prepare_payload=lambda: 5)
    session.write.assert_called_once_with(
        PATH, {"position": 5}, timeout=coordinator._POST_TIMEOUT_S
    )
