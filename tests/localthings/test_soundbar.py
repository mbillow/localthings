"""HW-S61B soundbar: end-to-end through Home Assistant.

A PSK entry polled flat (no /device/0 anywhere), routed to the soundbar
registry by /oic/d alone, with its entities read off the live state
machine.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from homeassistant.core import HomeAssistant
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
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.observe import MODE_OBSERVE
from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.identity import DeviceIdentity
from tests.conftest import FIXTURES

MOCK_HOST = "10.0.0.63"
MOCK_PORT = 40294
MOCK_DI = "7b1f0c9e-2a44-4d6b-9f10-4c8e2b5a0d31"
MOCK_PSK_IDENTITY = "9f8e7d6c-5b4a-4321-8765-0123456789ab"

# A test constant; the DTLS handshake is faked below.
ENTRY_DATA = {
    CONF_HOST: MOCK_HOST,
    CONF_PORT: MOCK_PORT,
    CONF_AUTH_CARRIER: AUTH_PSK,
    CONF_PSK_PROFILE: PSK_PROFILE_OWNER,
    CONF_PSK_IDENTITY: MOCK_PSK_IDENTITY,
    CONF_PSK_KEY: "0123456789abcdef0123456789abcdef",
    "transport": TRANSPORT_DTLS,
    CONF_DEVICE_KEY: MOCK_DI,
    "ocf_device_id": MOCK_DI,
    CONF_SERIAL: MOCK_HOST,
    CONF_MODEL: "HW-S61B",
    CONF_MANUFACTURER: "Samsung Electronics",
}


def _soundbar_resources() -> dict:
    data = json.loads((FIXTURES / "soundbar_s61b_device.json").read_text())
    return parse_device0_batch(data["device0"])


_IDENTITY = DeviceIdentity(
    manufacturer="Samsung Electronics",
    model="HW-TEST",
    name="Test AV",
    serial=None,
    device_id="7b1f0c9e-2a44-4d6b-9f10-4c8e2b5a0d31",
    device_types=("oic.wk.d", "oic.d.networkaudio"),
    raw={
        "/oic/res": [{"href": href} for href in _soundbar_resources()],
        "/oic/p": {"mnfv": "HW-TESTWWB-1000.0", "mnmo": "HW-TEST"},
    },
)


class FakeSoundbarSession:
    """A flat device: /device/0 4.04s, every advertised href answers."""

    supports_observe = True

    def __init__(self) -> None:
        self.resources = _soundbar_resources()
        self.reads: list[str] = []
        self.writes: list[tuple[list[str], dict]] = []
        self.subscribed: list[str] = []

    def subscribe(self, path_segs):
        self.subscribed.append("/" + "/".join(path_segs))
        return b"\x01"

    def refresh_observes(self, paths):
        return None

    def connect(self):
        pass

    def read(self, path, timeout=None):
        href = "/" + "/".join(path)
        self.reads.append(href)
        if list(path) == ["device", "0"]:
            return 0x84, None
        rep = self.resources.get(href)
        return (0x45, dict(rep)) if rep is not None else (0x84, None)

    def write(self, path, body, timeout=None):
        self.writes.append((list(path), body))
        if isinstance(body, dict):
            href = "/" + "/".join(path)
            self.resources.setdefault(href, {}).update(body)
        return 0x44, None

    def pace(self):
        pass

    def diagnostics(self):
        return {}

    def close(self):
        pass


async def _setup_soundbar(hass: HomeAssistant, entry: MockConfigEntry) -> FakeSoundbarSession:
    session = FakeSoundbarSession()
    with (
        patch(
            "custom_components.localthings.coordinator.create_transport",
            return_value=session,
        ),
        patch(
            "custom_components.localthings.coordinator.read_identity",
            return_value=_IDENTITY,
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return session


def _mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id=f"localthings_{MOCK_DI}", version=4
    )
    entry.add_to_hass(hass)
    return entry


def _coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> LocalThingsCoordinator:
    return hass.data[DOMAIN][entry.entry_id]


async def test_av_boards_never_attempt_observe(hass: HomeAssistant) -> None:
    """No OBSERVE on this board (see by_type/soundbar.py), so it stays "polling"."""
    entry = _mock_entry(hass)
    session = await _setup_soundbar(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    assert coordinator.device_type_name == "soundbar"
    assert session.subscribed == []
    assert coordinator._observe.mode != MODE_OBSERVE


async def test_psk_entry_polls_flat(hass: HomeAssistant) -> None:
    entry = _mock_entry(hass)
    session = await _setup_soundbar(hass, entry)
    coordinator = _coordinator(hass, entry)

    assert coordinator._flat is True
    assert coordinator._write_settle_s == 6.0
    assert coordinator.device_type_name == "soundbar"
    # One batch read across all setup cycles, then per href.
    assert session.reads.count("/device/0") == 1
    # No /oic/sec/* GET ever left the integration, enumeration included.
    assert not any(href.startswith("/oic/sec/") for href in session.reads)
    # The directory the coordinator polls is the one /oic/res advertised,
    # but for the onboarding resources that carry tokens.
    from custom_components.localthings.registry.flat import _SKIP_EXACT

    assert set(_soundbar_resources()) - set(session.reads) == _SKIP_EXACT & set(
        _soundbar_resources()
    )
    assert not _SKIP_EXACT & set(session.reads)


async def test_device_registry_carries_firmware_but_no_uuid_serial(
    hass: HomeAssistant,
) -> None:
    """Firmware from /oic/p lands on the card; the OCF per-unit UUID is
    NOT a serial (an AV board has none -- resolve_serial's host fallback
    is an address, not identity) and must not surface as one."""
    from homeassistant.helpers import device_registry as dr

    entry = _mock_entry(hass)
    await _setup_soundbar(hass, entry)
    device = next(
        row
        for row in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if row.model == "HW-TEST"
    )
    assert device.sw_version == "HW-TESTWWB-1000.0"
    assert device.serial_number is None


async def test_standalone_entities_register(hass: HomeAssistant) -> None:
    entry = _mock_entry(hass)
    await _setup_soundbar(hass, entry)
    coordinator = _coordinator(hass, entry)

    keys = {b.desc.key for b in coordinator.bound}
    for expected in ("bass", "woofer_level", "night_mode", "network_connection", "wifi_signal"):
        assert expected in keys
    # Power, volume and input wait for the media player.
    for key in ("media_player", "power_switch", "volume", "mute", "input_source"):
        assert key not in keys


async def test_channel_level_write_keeps_the_other_channels(
    hass: HomeAssistant,
) -> None:
    """Writing one channel trim keeps the other channels in the optimistic cache."""
    from homeassistant.helpers import entity_registry as er

    entry = _mock_entry(hass)
    await _setup_soundbar(hass, entry)

    ent_reg = er.async_get(hass)
    by_unique = {e.unique_id: e.entity_id for e in ent_reg.entities.values()}
    center = next(eid for uid, eid in by_unique.items() if uid.endswith("channel_level_center"))
    rear = next(eid for uid, eid in by_unique.items() if uid.endswith("channel_level_rear"))
    flag = next(eid for uid, eid in by_unique.items() if uid.endswith("rear_speakers"))

    def _state_of(eid: str) -> str:
        state = hass.states.get(eid)
        assert state is not None, f"{eid} not in the state machine at all"
        return state.state

    # Fixture values before the write (center/side/rear all present at -6/-6/+6).
    assert float(_state_of(center)) == -6
    assert float(_state_of(rear)) == 6
    assert _state_of(flag) == "on"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": rear, "value": 3},
        blocking=True,
    )

    # Inside the settle window only the written channel changes.
    assert float(_state_of(rear)) == 3
    assert float(_state_of(center)) == -6
    assert _state_of(flag) == "on"
