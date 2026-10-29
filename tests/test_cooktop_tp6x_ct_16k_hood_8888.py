"""NZ30K7570RS cooktop with a paired NK9300KM hood over 8888 (issue #676).

The hood answers on /devices/1, the slot the NZ36K7570R reports empty (#642).
Paired, it must read as a hood rather than a second cooktop.
"""

import json
from pathlib import Path
from typing import ClassVar, cast

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.fan import LocalThingsPairedHoodFan
from custom_components.localthings.legacy_http import table_for, to_resources
from custom_components.localthings.legacy_http_transport import (
    LegacyHttpTransport,
    _with_record_description,
    _with_record_name,
)
from custom_components.localthings.number import LocalThingsPairedHoodTimer
from custom_components.localthings.registry.adapter import _key, flatten
from custom_components.localthings.registry.capabilities import cooktop
from custom_components.localthings.registry.capabilities.cooktop import (
    LEGACY_HOOD_MODE,
    LEGACY_HOOD_POWER,
)
from custom_components.localthings.registry.entities import FanDesc, LightDesc, NumberDesc
from custom_components.localthings.registry.subdevices import Subdevice
from tests.conftest import _discover_full, _load_device_full
from tests.test_coordinator_cook_start import bare_coordinator  # noqa: F401
from tests.test_legacy_http_transport import _FakeConnection, _swept_as

NAME = "cooktop_tp6x_ct_16k_hood_8888"
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / f"{NAME}_device.json").read_text(encoding="utf-8")
)


def test_the_fixture_is_the_translation_of_its_bodies():
    device0 = {e["href"]: e["rep"] for e in FIXTURE["device0"]}
    assert device0 == to_resources(FIXTURE["bodies"], table_for("Cooktop"))
    hood = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    seed = {hood.to_canonical(e["href"]): e["rep"] for e in FIXTURE["seeds"]["/device/1"]}
    seed.pop("/connected/vs/0")
    assert seed == to_resources(FIXTURE["device_bodies"]["1"], table_for("Cooktop"))


HOOD = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))


def _hood_view():
    return {HOOD.to_canonical(e["href"]): e["rep"] for e in FIXTURE["seeds"]["/device/1"]}


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
        "paired_hood_shutoff_timer",
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
    assert hood["paired_hood_shutoff_timer"] == 0


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
    assert hood["paired_hood_shutoff_timer"] == 9


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
    assert hood["paired_hood_shutoff_timer"] == 0


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


# Timer writes, as verified on a second NK9300KM (PR #677): the same
# one-token PUT, 0 cancelling. The hood wraps above 255, so nothing past the
# panel's 99 is sent.
def test_the_shutoff_timer_writes_one_token():
    timer = next(d for d in LEGACY_HOOD_MODE.entities if d.key == "paired_hood_shutoff_timer")
    assert isinstance(timer, NumberDesc) and timer.write_fn is not None
    assert (timer.native_min, timer.native_max, timer.step) == (0, 99, 1)

    assert timer.write_fn(35.0, {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["ShutOffTimerSet_35"]},
    )
    assert timer.write_fn(0, {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["ShutOffTimerSet_0"]},
    )
    assert timer.write_fn(100, {}) is None
    assert timer.write_fn(-1, {}) is None
    assert timer.write_fn(2.5, {}) is None


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


def _timer(power, options):
    bound, _materialized, _state = _discover()
    timer_bound = next(b for b in bound if b.desc.key == "paired_hood_shutoff_timer")
    coordinator = _FakeCoordinator(power, options)
    return LocalThingsPairedHoodTimer(
        cast(LocalThingsCoordinator, coordinator), timer_bound
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

    assert coordinator.commands == [("speed", "4"), ("power", False)]


async def test_turning_on_sends_power_only_from_off():
    fan, coordinator = _fan("Off", _SHUTOFF_OPTIONS)
    await fan.async_turn_on()
    assert coordinator.commands == [("power", True)]

    fan, coordinator = _fan("On", _BOOST_OPTIONS)
    await fan.async_turn_on()
    assert coordinator.commands == []


# A timer the hood would ignore is refused rather than turning the fan on,
# which a number entity shouldn't do as a side effect (PR #677 review).
async def test_a_timer_while_the_fan_is_off_is_refused():
    timer, coordinator = _timer("Off", _SHUTOFF_OPTIONS)

    with pytest.raises(ServiceValidationError) as err:
        await timer.async_set_native_value(20)

    assert err.value.translation_key == "paired_hood_fan_off"
    assert coordinator.commands == []


async def test_a_running_fan_sets_and_cancels_the_timer_alone():
    timer, coordinator = _timer("On", _RUNNING_OPTIONS)

    await timer.async_set_native_value(99)
    await timer.async_set_native_value(0)

    assert coordinator.commands == [99, 0]


async def test_cancelling_never_turns_the_fan_on():
    timer, coordinator = _timer("Off", _SHUTOFF_OPTIONS)

    await timer.async_set_native_value(0)

    assert coordinator.commands == [0]


# The write only carries Set, so the entity, which reads Current, showed the
# old value until the settle window ended, about 45 s (#677).
def test_a_timer_write_shows_as_running_right_away():
    bound, _materialized, _state = _discover()
    desc = next(b.desc for b in bound if b.desc.key == "paired_hood_shutoff_timer")
    assert isinstance(desc, NumberDesc)
    assert desc.write_fn is not None and desc.optimistic_fn is not None

    _path, body = desc.write_fn(5, {})
    assert body == {"x.com.samsung.da.options": ["ShutOffTimerSet_5"]}
    assert desc.optimistic_fn(body)["x.com.samsung.da.options"] == [
        "ShutOffTimerSet_5",
        "ShutOffTimerCurrent_5",
        "ShutOffTimerState_Run",
    ]

    _path, body = desc.write_fn(0, {})
    assert desc.optimistic_fn(body)["x.com.samsung.da.options"] == [
        "ShutOffTimerSet_0",
        "ShutOffTimerCurrent_0",
        "ShutOffTimerState_Ready",
    ]
    assert desc.optimistic_fn({"x.com.samsung.da.options": ["Lamp_On"]}) == {
        "x.com.samsung.da.options": ["Lamp_On"]
    }


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
    _swept_as(transport, "Cooktop", index=1)

    transport.write(["power", "vs", "1"], {"x.com.samsung.da.power": "On"}, timeout=10.0)
    transport.write(["mode", "vs", "1"], {"x.com.samsung.da.options": ["FanSpeed_2"]}, timeout=10.0)
    transport.write(["mode", "vs", "1"], {"x.com.samsung.da.options": ["Lamp_On"]}, timeout=10.0)
    transport.write(
        ["mode", "vs", "1"], {"x.com.samsung.da.options": ["ShutOffTimerSet_20"]}, timeout=10.0
    )

    assert sent == [
        ("PUT", "/devices/1", {"Device": {"Operation": {"power": "On"}}}),
        ("PUT", "/devices/1", {"Device": {"Mode": {"options": ["FanSpeed_2"]}}}),
        ("PUT", "/devices/1", {"Device": {"Mode": {"options": ["Lamp_On"]}}}),
        ("PUT", "/devices/1", {"Device": {"Mode": {"options": ["ShutOffTimerSet_20"]}}}),
    ]


# The slot's modelNum is the cooktop's board, so it used to be named
# "<cooktop> Tp6X Ct 16K". The hood's record names it, and its DeviceType
# option gives the model, through its capability (PR #677 review).
async def test_the_hood_device_is_named_by_its_own_record(bare_coordinator, monkeypatch):  # noqa: F811
    view = _hood_view()
    view["/information/vs/0"] = {
        **view["/information/vs/0"],
        "x.com.samsung.da.deviceName": "Hood",
    }
    monkeypatch.setattr(bare_coordinator, "canonical_resources", lambda subdevice: view)
    bound, _materialized, _state = _discover()
    bare_coordinator.bound = bound

    info = bare_coordinator.device_info_for(HOOD)

    assert info["name"] == f"{bare_coordinator.device_info['name']} Hood"
    assert info["model"] == "NK9300KM"


# GET /devices and /devices/1 on the reporter's cooktop both carry the record
# name, "CookTop" for 0 and "Hood" for 1 (PR #677).
def test_a_sibling_record_names_its_information():
    bodies = _with_record_name(FIXTURE["device_bodies"]["1"], "Hood")

    information = to_resources(bodies, table_for("Cooktop"))["/information/vs/0"]
    assert information["x.com.samsung.da.deviceName"] == "Hood"
    assert _with_record_name(FIXTURE["device_bodies"]["1"], None) == FIXTURE["device_bodies"]["1"]


def test_an_unpaired_slot_has_no_hood_model():
    view = _hood_view()
    assert cooktop.paired_hood_model(view) == "NK9300KM"

    options = view["/mode/vs/0"]["x.com.samsung.da.options"]
    view["/mode/vs/0"] = {
        "x.com.samsung.da.options": [
            "DeviceType_NULL" if o.startswith("DeviceType_") else o for o in options
        ]
    }
    assert cooktop.paired_hood_model(view) is None


# The registry wraps its other capabilities to drop the empty slot; the hood's
# are left whole so the platforms can pick their classes by them.
def test_the_platforms_find_the_hood_by_its_capabilities():
    bound, _materialized, _state = _discover()
    fan = next(b for b in bound if b.desc.key == "paired_hood_fan")
    timer = next(b for b in bound if b.desc.key == "paired_hood_shutoff_timer")

    assert fan.capability is LEGACY_HOOD_POWER
    assert timer.capability is LEGACY_HOOD_MODE


# On 2026-10-27 the reporter's cooktop took 4 to 14 s a request, so the hood's
# 10 s budget went on /devices and both linked reads were skipped. With no
# Information the slot bound as a second cooktop. Its record still says what
# it is.
def test_a_hood_whose_linked_reads_time_out_still_binds_as_a_hood(monkeypatch):
    monkeypatch.setattr("custom_components.localthings.legacy_http_transport._STATE", {})
    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.http.client.HTTPSConnection",
        _FakeConnection,
    )
    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.client_context",
        lambda cert_pem, key_pem: object(),
    )
    body = {
        key: value
        for key, value in FIXTURE["device_bodies"]["1"].items()
        if key not in ("Configuration", "Information")
    }
    record = {**body, "id": "1", "description": "TP6X_CT_16K_HOOD", "name": "Hood"}
    _FakeConnection.log = []
    _FakeConnection.routes = {
        "/devices": (200, {"Devices": [record]}),
        "/devices/1/configuration": TimeoutError("timed out"),
        "/devices/1/information": TimeoutError("timed out"),
    }
    transport = LegacyHttpTransport(
        "10.0.0.7", 8888, cert_pem="CERT", key_pem="KEY", token="tok123456", family="TP6X_CT"
    )
    transport.connect()

    code, seed = transport.read(["device", "1"], timeout=10.0)

    assert code == 0x45
    view = {HOOD.to_canonical(e["href"]): e["rep"] for e in seed}
    assert view["/information/vs/0"] == {
        "x.com.samsung.da.description": "TP6X_CT_16K_HOOD",
        "x.com.samsung.da.deviceName": "Hood",
    }
    assert cooktop.is_legacy_hood_slot(view)
    assert LEGACY_HOOD_POWER.match_fn is not None and LEGACY_HOOD_POWER.match_fn({}, view)
    assert cooktop.paired_hood_model(view) == "NK9300KM"


# A record's description can carry the serial, so only a plain board token
# stands in for a missing Information.
@pytest.mark.parametrize("description", ["TP6X_CT_16K_HOOD(0A1B2C3D)", "Hood", "", None])
def test_only_a_board_token_stands_in_for_information(description):
    assert _with_record_description({"Mode": {}}, description) == {"Mode": {}}
