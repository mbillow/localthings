"""The serial number and firmware version on the device card.

Set only from a poll this entry adopted, and only when the device reported
them: a key left out keeps what the device registry already has.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_DEVICE_KEY,
    CONF_HOST,
    CONF_PORT,
    CONF_SERIAL,
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.identity import (
    DeviceIdentity,
    display_serial,
    resolve_firmware,
)
from custom_components.localthings.registry.subdevices import Subdevice
from tests.conftest import _load_device

FIRMWARE = "DA_DW_TEST_20260101"


def _coordinator(
    hass: HomeAssistant, platform: dict, device_id: str = "unit-a"
) -> LocalThingsCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="localthings_unit-a",
        data={
            CONF_HOST: "10.0.0.5",
            CONF_PORT: 49154,
            CONF_DEVICE_KEY: "unit-a",
            CONF_SERIAL: "SERIAL-A",
        },
        version=4,
    )
    entry.add_to_hass(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    coordinator._identity = DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="",
        name="",
        serial=None,
        device_id=device_id,
        device_types=(),
        raw={"/oic/p": platform, "/oic/d": {}, "/oic/res": []},
    )
    return coordinator


def _dishwasher(serial: str | None) -> dict[str, dict]:
    resources = dict(_load_device("dishwasher"))
    info = dict(resources["/information/vs/0"])
    info.pop("x.com.samsung.da.serialNum", None)
    if serial is not None:
        info["x.com.samsung.da.serialNum"] = serial
    resources["/information/vs/0"] = info
    return resources


async def test_a_poll_puts_the_serial_and_firmware_on_the_card(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, {"mnfv": f" {FIRMWARE} "})

    coordinator._run_discovery(_dishwasher("0A1B2C3D4E5F"))

    assert coordinator.device_info["serial_number"] == "0A1B2C3D4E5F"
    assert coordinator.device_info["sw_version"] == FIRMWARE


async def test_what_the_device_did_not_report_is_left_to_the_registry(
    hass: HomeAssistant,
) -> None:
    """A snapshot replay with no /oic/p, or a placeholder serial, must not
    clear what a live poll put on the card."""
    coordinator = _coordinator(hass, {})

    coordinator._run_discovery(_dishwasher("FFFFFFFFFFFF"), from_snapshot=True)

    assert "serial_number" not in coordinator.device_info
    assert "sw_version" not in coordinator.device_info


async def test_an_appliance_the_entry_did_not_adopt_adds_nothing(hass: HomeAssistant) -> None:
    """Another unit answering at this address (a moved lease) is not this
    entry's device, so none of its identity lands on this card."""
    coordinator = _coordinator(hass, {"mnfv": FIRMWARE}, device_id="unit-b")

    coordinator._run_discovery(_dishwasher("0A1B2C3D4E5F"))

    assert coordinator.device_key == "unit-a"  # refused, not re-keyed
    assert "serial_number" not in coordinator.device_info
    assert "sw_version" not in coordinator.device_info


async def test_a_subdevice_card_shows_only_a_real_serial(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, {})
    coordinator.device_key = "unit-a"
    sub = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    coordinator.subdevices = [sub]

    coordinator._observe.apply(
        "/information/vs/1", {"x.com.samsung.da.serialNum": "0A1B2C3D4E60"}, source="poll"
    )
    assert coordinator.device_info_for(sub)["serial_number"] == "0A1B2C3D4E60"

    coordinator._observe.apply(
        "/information/vs/1", {"x.com.samsung.da.serialNum": "Nothing(SVC)"}, source="poll"
    )
    assert "serial_number" not in coordinator.device_info_for(sub)


def test_display_serial_shows_only_a_real_reported_serial() -> None:
    assert display_serial(" 0A1B2C3D4E5F ") == "0A1B2C3D4E5F"
    assert display_serial(None) is None
    assert display_serial("") is None
    assert display_serial("Nothing(SVC)") is None
    assert display_serial("FFFFFFFFFFFF") is None


def test_resolve_firmware_reads_only_a_reported_mnfv() -> None:
    def ident(platform):
        return DeviceIdentity(
            manufacturer="", model="", name="", serial=None, raw={"/oic/p": platform}
        )

    assert resolve_firmware(ident({"mnfv": f" {FIRMWARE} "})) == FIRMWARE
    assert resolve_firmware(ident({"mnfv": "  "})) is None
    assert resolve_firmware(ident({})) is None
    assert resolve_firmware(ident("not a map")) is None
    assert resolve_firmware(None) is None
