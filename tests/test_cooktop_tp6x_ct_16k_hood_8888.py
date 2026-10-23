"""NZ30K7570RS cooktop with a paired NK9300KM hood over 8888 (issue #676).

The hood answers on /devices/1, the slot the NZ36K7570R reports empty (#642).
Paired, it must read as a hood rather than a second cooktop.
"""

import json
from pathlib import Path
from typing import ClassVar, cast

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.fan import LocalThingsPairedHoodFan
from custom_components.localthings.legacy_http import TP6X_CT, to_resources
from custom_components.localthings.legacy_http_transport import LegacyHttpTransport
from custom_components.localthings.registry.adapter import _key, flatten
from custom_components.localthings.registry.capabilities.cooktop import (
    LEGACY_HOOD_MODE,
    LEGACY_HOOD_POWER,
)
from custom_components.localthings.registry.entities import FanDesc, LightDesc
from custom_components.localthings.registry.subdevices import Subdevice
from tests.conftest import _discover_full, _load_device_full

NAME = "cooktop_tp6x_ct_16k_hood_8888"
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / f"{NAME}_device.json").read_text(encoding="utf-8")
)


def test_the_fixture_is_the_translation_of_its_bodies():
    device0 = {e["href"]: e["rep"] for e in FIXTURE["device0"]}
    assert device0 == to_resources(FIXTURE["bodies"], TP6X_CT)
    hood = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    seed = {hood.to_canonical(e["href"]): e["rep"] for e in FIXTURE["seeds"]["/device/1"]}
    seed.pop("/connected/vs/0")
    assert seed == to_resources(FIXTURE["device_bodies"]["1"], TP6X_CT)


def _discover():
    resources, oic_res, seeds = _load_device_full(NAME)
    bound, materialized, _skipped, full, _name = _discover_full(resources, oic_res, seeds)
    state = flatten(bound, full)
    return bound, materialized, state


def test_the_hood_is_subdevice_1_with_hood_entities_only():
    bound, materialized, _state = _discover()

    assert [s.key for s in materialized] == ["1"]
    hood_keys = {b.desc.key for b in bound if b.subdevice.key == "1"}
    assert {
        "paired_hood_connected",
        "paired_hood_fan",
        "paired_hood_fan_setting",
        "paired_hood_light",
        "paired_hood_model",
        "paired_hood_shutoff_timer_state",
        "paired_hood_shutoff_timer_set",
        "paired_hood_shutoff_timer_remaining",
    } <= hood_keys
    assert not any(k.startswith(("burner_", "main_timer")) for k in hood_keys)
    assert "any_burner_active" not in hood_keys
    assert "power_state" not in hood_keys


def test_the_hood_reads_its_options():
    bound, _materialized, state = _discover()
    hood = {b.desc.key: state[_key(b)] for b in bound if b.subdevice.key == "1"}

    assert hood["paired_hood_connected"] is True
    assert hood["paired_hood_fan_setting"] == "low"
    assert hood["paired_hood_light"] is False
    assert hood["paired_hood_model"] == "NK9300KM"
    assert hood["paired_hood_fan"] == "Off"
    assert hood["paired_hood_shutoff_timer_state"] == "Ready"
    assert hood["paired_hood_shutoff_timer_set"] == 0
    assert hood["paired_hood_shutoff_timer_remaining"] == 0


# /devices/1's Mode options from the reporter's second dump, taken with the
# hood running.
_RUNNING_OPTIONS = [
    "DeviceType_NK9300KM",
    "Bluetooth_On",
    "FanSpeed_2",
    "Lamp_On",
    "ShutOffTimerSet_10",
    "ShutOffTimerCurrent_9",
    "ShutOffTimerState_Run",
]


def test_the_running_hood_reads_its_options():
    hood = {desc.key: desc.value_fn(_RUNNING_OPTIONS) for desc in LEGACY_HOOD_MODE.entities}

    assert hood["paired_hood_fan_setting"] == "medium"
    assert hood["paired_hood_light"] is True
    assert hood["paired_hood_shutoff_timer_state"] == "Run"
    assert hood["paired_hood_shutoff_timer_set"] == 10
    assert hood["paired_hood_shutoff_timer_remaining"] == 9


# The reporter's last dump, taken once the hood had turned off: power reads
# Off, the fan keeps its High setting and the light, which runs on its own,
# stays on.
_SHUTOFF_OPTIONS = [
    "DeviceType_NK9300KM",
    "Bluetooth_On",
    "FanSpeed_3",
    "Lamp_On",
    "ShutOffTimerSet_10",
    "ShutOffTimerCurrent_0",
    "ShutOffTimerState_Ready",
]


def test_the_hood_after_shutoff_keeps_its_fan_setting_and_light():
    hood = {desc.key: desc.value_fn(_SHUTOFF_OPTIONS) for desc in LEGACY_HOOD_MODE.entities}

    assert hood["paired_hood_fan_setting"] == "high"
    assert hood["paired_hood_light"] is True
    assert hood["paired_hood_shutoff_timer_state"] == "Ready"
    assert hood["paired_hood_shutoff_timer_set"] == 10
    assert hood["paired_hood_shutoff_timer_remaining"] == 0


# The reporter's dump with the fan on Boost and no shutoff timer running.
_BOOST_OPTIONS = [
    "DeviceType_NK9300KM",
    "Bluetooth_On",
    "FanSpeed_4",
    "Lamp_Off",
    "ShutOffTimerSet_10",
    "ShutOffTimerCurrent_0",
    "ShutOffTimerState_Ready",
]


def test_the_hood_reads_boost():
    hood = {desc.key: desc.value_fn(_BOOST_OPTIONS) for desc in LEGACY_HOOD_MODE.entities}

    assert hood["paired_hood_fan_setting"] == "boost"
    assert hood["paired_hood_light"] is False
    assert hood["paired_hood_shutoff_timer_state"] == "Ready"


def test_the_cooktop_keeps_its_burners():
    bound, _materialized, _state = _discover()
    main_keys = {b.desc.key for b in bound if b.subdevice.key != "1"}

    assert {"any_burner_active", "burner_3_pan_size", "main_timer_set", "power_state"} <= main_keys
    assert not any(k.startswith("paired_hood_") for k in main_keys)


# Writes, as verified on the reporter's hood: each is a one-token PUT to the
# hood's slot. Power On starts the fan on Low and a speed written while the
# fan is off is ignored, so a speed goes after power On.
def test_the_fan_and_light_write_one_token_each():
    (fan,) = LEGACY_HOOD_POWER.entities
    light = next(d for d in LEGACY_HOOD_MODE.entities if d.key == "paired_hood_light")
    assert isinstance(fan, FanDesc) and fan.write_fn is not None
    assert isinstance(light, LightDesc) and light.write_fn is not None

    assert fan.write_fn(("power", True), {}) == (
        ["power", "vs", "0"],
        {"x.com.samsung.da.power": "On"},
    )
    assert fan.write_fn(("power", False), {}) == (
        ["power", "vs", "0"],
        {"x.com.samsung.da.power": "Off"},
    )
    assert fan.write_fn(("speed", "4"), {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["FanSpeed_4"]},
    )
    assert fan.write_fn(("speed", "0"), {}) is None
    assert light.write_fn(True, {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["Lamp_On"]},
    )
    assert light.write_fn(False, {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["Lamp_Off"]},
    )


class _FakeCoordinator:
    device_key = "TEST-COOKTOP"
    data: ClassVar[dict] = {}

    def __init__(self, power, options):
        # The hood's own canonical view; the cooktop's /power/vs/0 reads
        # On so a fan reading the wrong subdevice would show it.
        self.hood = {
            "/power/vs/0": {"x.com.samsung.da.power": power},
            "/mode/vs/0": {"x.com.samsung.da.options": options},
        }
        self.commands = []

    def resource(self, href):
        return {"x.com.samsung.da.power": "On"} if href == "/power/vs/0" else {}

    def canonical_resources(self, subdevice):
        assert subdevice.key == "1"
        return self.hood

    def device_info_for(self, subdevice):
        return {}

    async def async_send_command(self, bound, payload):
        self.commands.append(payload)


def _fan(power, options):
    bound, _materialized, _state = _discover()
    fan_bound = next(b for b in bound if b.desc.key == "paired_hood_fan")
    coordinator = _FakeCoordinator(power, options)
    return LocalThingsPairedHoodFan(
        cast(LocalThingsCoordinator, coordinator), fan_bound
    ), coordinator


def test_the_fan_reads_the_hood_slot():
    fan, _coordinator = _fan("Off", _SHUTOFF_OPTIONS)
    assert fan.is_on is False
    assert fan.percentage == 0
    assert fan.speed_count == 4

    fan, _coordinator = _fan("On", _BOOST_OPTIONS)
    assert fan.is_on is True
    assert fan.percentage == 100

    fan, _coordinator = _fan("On", _RUNNING_OPTIONS)
    assert fan.percentage == 50


async def test_a_speed_from_off_sends_power_then_the_speed():
    fan, coordinator = _fan("Off", _SHUTOFF_OPTIONS)

    await fan.async_set_percentage(75)

    assert coordinator.commands == [("power", True), ("speed", "3")]


async def test_turning_on_with_a_speed_sends_power_then_the_speed():
    fan, coordinator = _fan("Off", _SHUTOFF_OPTIONS)

    await fan.async_turn_on(percentage=50)

    assert coordinator.commands == [("power", True), ("speed", "2")]


async def test_a_running_fan_changes_speed_alone_and_turns_off_by_power():
    fan, coordinator = _fan("On", _RUNNING_OPTIONS)

    await fan.async_set_percentage(100)
    await fan.async_set_percentage(0)
    await fan.async_turn_on()

    assert coordinator.commands == [("speed", "4"), ("power", False), ("power", True)]


def test_the_transport_sends_hood_writes_to_its_slot(monkeypatch):
    monkeypatch.setattr("custom_components.localthings.legacy_http_transport._STATE", {})
    transport = LegacyHttpTransport(
        "10.0.0.7", 8888, cert_pem="CERT", key_pem="KEY", token="tok123456", family="TP6X_CT"
    )
    sent = []

    def request(method, path, *, body=None, timeout):
        sent.append((method, path, body))
        return 204, None

    monkeypatch.setattr(transport, "_request", request)

    transport.write(["power", "vs", "1"], {"x.com.samsung.da.power": "On"}, timeout=10.0)
    transport.write(["mode", "vs", "1"], {"x.com.samsung.da.options": ["FanSpeed_2"]}, timeout=10.0)
    transport.write(["mode", "vs", "1"], {"x.com.samsung.da.options": ["Lamp_On"]}, timeout=10.0)

    assert sent == [
        ("PUT", "/devices/1", {"Device": {"Operation": {"power": "On"}}}),
        ("PUT", "/devices/1", {"Device": {"Mode": {"options": ["FanSpeed_2"]}}}),
        ("PUT", "/devices/1", {"Device": {"Mode": {"options": ["Lamp_On"]}}}),
    ]
