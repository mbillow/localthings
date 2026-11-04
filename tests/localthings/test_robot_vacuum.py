"""POWERbot R7040 robot vacuum (issue #659): end-to-end through Home
Assistant, from /oic/d routing to a vacuum service call on the wire."""

from __future__ import annotations

import json
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    AUTH_PSK,
    CONF_AUTH_CARRIER,
    CONF_DEVICE_KEY,
    CONF_HOST,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_PORT,
    CONF_PSK_IDENTITY,
    CONF_PSK_KEY,
    CONF_PSK_PROFILE,
    CONF_SERIAL,
    DOMAIN,
    PSK_PROFILE_OWNER,
    TRANSPORT_DTLS,
)
from custom_components.localthings.registry.identity import DeviceIdentity
from tests.conftest import FIXTURES

MOCK_HOST = "10.0.0.64"
MOCK_DI = "3c2d1e0f-1111-4222-8333-944455566677"

ENTRY_DATA = {
    CONF_HOST: MOCK_HOST,
    CONF_PORT: 45609,
    CONF_AUTH_CARRIER: AUTH_PSK,
    CONF_PSK_PROFILE: PSK_PROFILE_OWNER,
    CONF_PSK_IDENTITY: "9f8e7d6c-5b4a-4321-8765-0123456789ab",
    CONF_PSK_KEY: "0123456789abcdef0123456789abcdef",
    "transport": TRANSPORT_DTLS,
    CONF_DEVICE_KEY: MOCK_DI,
    "ocf_device_id": MOCK_DI,
    CONF_SERIAL: MOCK_HOST,
    CONF_MODEL: "powerbot_7000_17M",
    CONF_MANUFACTURER: "Samsung Electronics",
}

_DEVICE0 = json.loads((FIXTURES / "robot_vacuum_powerbot_r7040_device.json").read_text())["device0"]

_IDENTITY = DeviceIdentity(
    manufacturer="Samsung Electronics",
    model="powerbot_7000_17M",
    name="POWERbot",
    serial=None,
    device_id=MOCK_DI,
    device_types=("oic.wk.d", "oic.d.robotcleaner"),
    raw={},
)


class FakeRobotSession:
    supports_observe = False

    def __init__(self) -> None:
        self.writes: list[tuple[list[str], dict]] = []

    def connect(self):
        pass

    def read(self, path, timeout=None):
        if list(path) == ["device", "0"]:
            return 0x45, json.loads(json.dumps(_DEVICE0))
        return 0x84, None

    def write(self, path, body, timeout=None):
        self.writes.append((list(path), body))
        return 0x44, None

    def pace(self):
        pass

    def diagnostics(self):
        return {}

    def close(self):
        pass


async def test_robot_vacuum_entity_reads_and_writes(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_DI}", version=4
    )
    entry.add_to_hass(hass)
    session = FakeRobotSession()
    with (
        patch("custom_components.localthings.coordinator.create_transport", return_value=session),
        patch("custom_components.localthings.coordinator.read_identity", return_value=_IDENTITY),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.data[DOMAIN][entry.entry_id].device_type_name == "robot_vacuum"
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id("vacuum", DOMAIN, f"{DOMAIN}_{MOCK_DI}_vacuum")
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "docked"
        assert state.attributes["fan_speed"] == "normal"
        assert state.attributes["fan_speed_list"] == ["turbo", "normal"]

        await hass.services.async_call(
            "vacuum", "return_to_base", {"entity_id": entity_id}, blocking=True
        )

    assert (["mode", "vs", "0"], {"x.com.samsung.da.modes": ["Control_Homing"]}) in session.writes
