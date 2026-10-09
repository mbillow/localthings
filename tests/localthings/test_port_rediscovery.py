"""Following a secure port that moved while the appliance was off (#435).

The port is kernel-assigned, so a power cycle can leave the stored one
pointing at nothing while the appliance is fine on another. A failed
handshake asks the device which port it now advertises, backed off so an
appliance that is simply off isn't asked every poll.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from smartthings_local.errors import PeerInitiatedHandshakeError, SessionError

from custom_components.localthings import coordinator as coordinator_module
from custom_components.localthings import probing
from custom_components.localthings.const import (
    AUTH_PSK,
    CONF_AUTH_CARRIER,
    CONF_DEVICE_TOKEN,
    CONF_HOST,
    CONF_OCF_DEVICE_ID,
    CONF_PORT,
    CONF_PSK_IDENTITY,
    CONF_PSK_KEY,
    CONF_PSK_PROFILE,
    CONF_TRANSPORT,
    DOMAIN,
    MULTICAST_SECURE_PORT,
    PSK_PROFILE_OWNER,
    TRANSPORT_LEGACY_HTTP,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.credentials import DeviceIdentityMismatch
from custom_components.localthings.registry.identity import DeviceIdentity

HOST = "192.0.2.10"
DEVICE_ID = "6f0b6c1e-5a41-4f7e-9c2d-3b8a1d4e7f10"
OLD_PORT = 58227
NEW_PORT = 41820


def _entry_data(**extra) -> dict[str, Any]:
    return {
        CONF_HOST: HOST,
        CONF_PORT: OLD_PORT,
        CONF_AUTH_CARRIER: AUTH_PSK,
        CONF_PSK_PROFILE: PSK_PROFILE_OWNER,
        CONF_PSK_IDENTITY: "1a2b3c4d-5e6f-4a1b-8c9d-aebfc1d2e3f4",
        CONF_PSK_KEY: "00112233445566778899aabbccddeeff",
        CONF_OCF_DEVICE_ID: DEVICE_ID,
        **extra,
    }


class _Appliance:
    """Answers a handshake only on `live_port`, reporting `device_id`."""

    def __init__(self, live_port: int | None, device_id: str = DEVICE_ID) -> None:
        self.live_port = live_port
        self.device_id = device_id
        self.dialled: list[int] = []
        self.lookups = 0

    def transport(self, data, **kwargs):
        appliance = self
        port = data[CONF_PORT]

        class _Transport:
            def connect(self) -> None:
                appliance.dialled.append(port)
                if port != appliance.live_port:
                    raise TimeoutError("handshake timed out")

            def close(self) -> None:
                pass

        return _Transport()

    def advertised(self, host: str, current: int, device_id: str | None = None) -> int | None:
        self.lookups += 1
        if self.live_port is None or self.live_port == current:
            return None
        return self.live_port


@pytest.fixture
def appliance(monkeypatch):
    device = _Appliance(NEW_PORT)
    monkeypatch.setattr(coordinator_module, "create_transport", device.transport)
    monkeypatch.setattr(probing, "moved_secure_port", device.advertised)
    monkeypatch.setattr(
        coordinator_module,
        "read_identity",
        lambda sess, serial: DeviceIdentity(
            manufacturer="Samsung Electronics",
            model="AWM-KR-M64-24-WD86",
            name="Samsung Washer",
            serial=None,
            device_id=device.device_id,
        ),
    )
    return device


def _coordinator(hass: HomeAssistant, **extra) -> LocalThingsCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data=_entry_data(**extra), version=4)
    entry.add_to_hass(hass)
    return LocalThingsCoordinator(hass, entry)


async def test_a_moved_port_is_followed_and_recorded_once_a_poll_lands(
    hass: HomeAssistant, appliance
) -> None:
    coordinator = _coordinator(hass)

    await hass.async_add_executor_job(coordinator._connect_session)

    assert appliance.dialled == [OLD_PORT, NEW_PORT]
    assert coordinator._session is not None
    # Not written until a poll has come through on the new port.
    assert coordinator._entry.data[CONF_PORT] == OLD_PORT
    coordinator._mark_device_answered()
    assert coordinator._entry.data[CONF_PORT] == NEW_PORT


async def test_an_unreachable_appliance_is_looked_up_with_backoff(
    hass: HomeAssistant, appliance, monkeypatch
) -> None:
    """Off or asleep: one lookup, then none until the backoff step passes."""
    appliance.live_port = None
    coordinator = _coordinator(hass)
    clock = [1000.0]
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: clock[0])

    for _ in range(3):
        with pytest.raises(TimeoutError):
            coordinator._connect_session()
    assert appliance.lookups == 1

    clock[0] += coordinator._REDISCOVERY_BACKOFF_MIN_S
    with pytest.raises(TimeoutError):
        coordinator._connect_session()
    assert appliance.lookups == 2

    # The step doubled, so the same wait again isn't enough.
    clock[0] += coordinator._REDISCOVERY_BACKOFF_MIN_S
    with pytest.raises(TimeoutError):
        coordinator._connect_session()
    assert appliance.lookups == 2


async def test_the_backoff_is_capped(hass: HomeAssistant, appliance, monkeypatch) -> None:
    appliance.live_port = None
    coordinator = _coordinator(hass)
    clock = [0.0]
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: clock[0])

    for _ in range(12):
        clock[0] += coordinator._REDISCOVERY_BACKOFF_MAX_S
        with pytest.raises(TimeoutError):
            coordinator._connect_session()
    assert coordinator._rediscovery_backoff_s == coordinator._REDISCOVERY_BACKOFF_MAX_S


async def test_a_successful_connect_resets_the_backoff(
    hass: HomeAssistant, appliance, monkeypatch
) -> None:
    appliance.live_port = None
    coordinator = _coordinator(hass)
    clock = [1000.0]
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: clock[0])
    with pytest.raises(TimeoutError):
        coordinator._connect_session()

    appliance.live_port = OLD_PORT
    coordinator._connect_session()
    coordinator._close_session()

    # Down again straight away: looked up at once, not after the old step.
    appliance.live_port = NEW_PORT
    coordinator._connect_session()
    assert appliance.dialled[-2:] == [OLD_PORT, NEW_PORT]


async def test_a_followed_port_is_dropped_if_the_stored_one_answers_again(
    hass: HomeAssistant, appliance
) -> None:
    """Followed to the new port, but the next connect lands on the stored
    one: the unproven port must not be written on the next good poll."""
    coordinator = _coordinator(hass)
    coordinator._connect_session()
    coordinator._close_session()

    appliance.live_port = OLD_PORT
    coordinator._connect_session()
    coordinator._mark_device_answered()
    assert coordinator._entry.data[CONF_PORT] == OLD_PORT


async def test_the_new_port_still_has_to_pass_the_binding_check(
    hass: HomeAssistant, appliance
) -> None:
    appliance.device_id = "0d9c8b7a-6f5e-4d3c-8b2a-19f8e7d6c5b4"
    coordinator = _coordinator(hass)

    with pytest.raises(DeviceIdentityMismatch):
        coordinator._connect_session()
    assert coordinator._session is None
    coordinator._mark_device_answered()
    assert coordinator._entry.data[CONF_PORT] == OLD_PORT


async def test_a_legacy_bridge_entry_is_never_looked_up(hass: HomeAssistant, appliance) -> None:
    """The 8888 bridge has a fixed port and no CoAP to ask."""
    appliance.live_port = None
    coordinator = _coordinator(
        hass, **{CONF_TRANSPORT: TRANSPORT_LEGACY_HTTP, CONF_DEVICE_TOKEN: "t"}
    )
    with pytest.raises(TimeoutError):
        coordinator._connect_session()
    assert appliance.lookups == 0


def test_moved_secure_port_skips_the_current_port_and_5684(monkeypatch) -> None:
    advertised = (MULTICAST_SECURE_PORT, OLD_PORT, NEW_PORT)
    monkeypatch.setattr(probing, "_discover_advertised_ports", lambda host: (advertised, 5683))
    # The conftest stub replaces the function itself; call the real one.
    assert _real_moved_secure_port(HOST, OLD_PORT) == NEW_PORT

    advertised = (MULTICAST_SECURE_PORT, OLD_PORT)
    assert _real_moved_secure_port(HOST, OLD_PORT) is None


_real_moved_secure_port = probing.moved_secure_port


class _ScriptedAppliance(_Appliance):
    """Raises each of `failures` on successive handshakes, then connects."""

    def __init__(self, failures: list[Exception]) -> None:
        super().__init__(NEW_PORT)
        self.failures = failures

    def transport(self, data, **kwargs):
        appliance = self
        port = data[CONF_PORT]

        class _Transport:
            def connect(self) -> None:
                appliance.dialled.append(port)
                if appliance.failures:
                    raise appliance.failures.pop(0)

            def close(self) -> None:
                pass

        return _Transport()


def _scripted(monkeypatch, failures: list[Exception]) -> _ScriptedAppliance:
    device = _ScriptedAppliance(failures)
    monkeypatch.setattr(coordinator_module, "create_transport", device.transport)
    monkeypatch.setattr(probing, "moved_secure_port", device.advertised)
    monkeypatch.setattr(
        coordinator_module,
        "read_identity",
        lambda sess, serial: DeviceIdentity(
            manufacturer="Samsung Electronics",
            model="AWM-KR-M64-24-WD86",
            name="Samsung Washer",
            serial=None,
            device_id=DEVICE_ID,
        ),
    )
    return device


async def test_a_handshake_collision_is_retried_on_the_same_port(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Issue #567: the appliance's own concurrent handshake clears on an
    immediate retry, so it costs neither a cycle nor a port lookup."""
    device = _scripted(monkeypatch, [PeerInitiatedHandshakeError()])
    coordinator = _coordinator(hass)

    await hass.async_add_executor_job(coordinator._connect_session)

    assert device.dialled == [OLD_PORT, OLD_PORT]
    assert device.lookups == 0
    assert coordinator._session is not None


async def test_an_alert_from_the_stored_port_does_not_look_for_a_moved_one(
    hass: HomeAssistant, monkeypatch
) -> None:
    """An appliance that answered with an alert is still on this port; only
    silence or an unreachable endpoint suggests the port moved."""
    device = _scripted(monkeypatch, [SessionError()])
    coordinator = _coordinator(hass)

    with pytest.raises(SessionError):
        await hass.async_add_executor_job(coordinator._connect_session)

    assert device.dialled == [OLD_PORT]
    assert device.lookups == 0
