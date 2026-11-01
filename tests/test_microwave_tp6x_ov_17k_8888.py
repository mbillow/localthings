"""NQ70M6650DS microwave/oven combo over 8888 (issue #674, TP6X_OV_17K).

The bridge serves the microwave as /devices/0 and the oven as /devices/2,
the NW9300MD combo's layout over CoAP (#525). /devices/1 answers too, with a
blank record /devices leaves out; it must not become a third cavity.
"""

import copy
import json
from pathlib import Path

import pytest

from custom_components.localthings.legacy_http import PREFIX, table_for, to_resources
from custom_components.localthings.legacy_http_transport import LegacyHttpTransport
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import BinarySensorDesc, SensorDesc
from custom_components.localthings.registry.subdevices import Subdevice
from tests.conftest import _discover_full, _load_device_full
from tests.test_legacy_http_transport import _FakeConnection

NAME = "microwave_tp6x_ov_17k_8888"
OVEN = table_for("Oven")
OVEN_SLOT = Subdevice(kind="indexed", key="2", seed_path=("device", "2"))
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / f"{NAME}_device.json").read_text(encoding="utf-8")
)


def test_the_fixture_is_the_translation_of_its_bodies():
    device0 = {e["href"]: e["rep"] for e in FIXTURE["device0"]}
    assert device0 == to_resources(FIXTURE["bodies"], OVEN)
    for index, bodies in FIXTURE["device_bodies"].items():
        sibling = Subdevice(kind="indexed", key=index, seed_path=("device", index))
        seed = {
            sibling.to_canonical(e["href"]): e["rep"] for e in FIXTURE["seeds"][f"/device/{index}"]
        }
        seed.pop("/connected/vs/0")
        assert seed == to_resources(bodies, OVEN)


def test_the_microwave_is_the_master_with_nothing_unbound():
    resources, _oic_res, _seeds = _load_device_full(NAME)
    reg = resolve(resources)
    assert reg is not None and reg.name == "microwave"
    unbound: list[str] = []

    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)

    assert unbound == []
    keys = {b.desc.key for b in bound}
    assert {"power_level", "current_temp_c", "machine_state", "door_open"} <= keys


def test_the_oven_is_subdevice_2_and_the_blank_slot_is_skipped():
    resources, oic_res, seeds = _load_device_full(NAME)

    bound, materialized, skipped, _full, _name = _discover_full(resources, oic_res, seeds)

    assert [s.key for s in materialized] == ["2"]
    assert [s.subdevice.key for s in skipped] == ["1"]
    oven_keys = {b.desc.key for b in bound if b.subdevice.key == "2"}
    assert {"oven_state", "machine_state", "current_temp_c"} <= oven_keys
    assert not any(b.subdevice.key == "1" for b in bound)


def test_the_oven_offers_its_own_modes():
    _resources, _oic_res, seeds = _load_device_full(NAME)
    mode = next(e["rep"] for e in seeds["/device/2"] if e["href"] == "/mode/vs/2")

    modes = mode[PREFIX + "supportedModes"]

    assert {"Bake", "ConvectionRoast", "SteamBake", "SelfClean"} <= set(modes)
    assert "MicroWave" not in modes


def _controls(bound):
    return {
        (b.subdevice.key, b.desc.key)
        for b in bound
        if not isinstance(b.desc, (SensorDesc, BinarySensorDesc))
    }


def test_no_control_binds_on_either_cavity():
    """Live on an NQ70M6650DS, every write was answered 204, but a remote start
    only loaded the cook for Start at the panel, and a setpoint written while
    idle cleared the mode (#675)."""
    resources, oic_res, seeds = _load_device_full(NAME)

    bound, _materialized, _skipped, _full, _name = _discover_full(resources, oic_res, seeds)

    assert _controls(bound) == set()


def test_the_same_cavities_on_another_board_keep_their_controls():
    """The gate is this board's, not the oven or microwave registry's."""
    resources, oic_res, seeds = _load_device_full(NAME)
    resources, seeds = copy.deepcopy(resources), copy.deepcopy(seeds)
    model = "LCD_OV_WALL_16K|40401841|"
    resources["/information/vs/0"][PREFIX + "modelNum"] = model
    for element in seeds["/device/2"]:
        if element["href"] == "/information/vs/2":
            element["rep"][PREFIX + "modelNum"] = model

    bound, _materialized, _skipped, _full, _name = _discover_full(resources, oic_res, seeds)

    assert {("2", "oven_mode"), ("2", "oven_setpoint"), ("2", "start_cooking")} <= _controls(bound)


def _slot_1_set(seeds, href, field, value):
    seeds = copy.deepcopy(seeds)
    actual = Subdevice(kind="indexed", key="1", seed_path=("device", "1")).to_actual(href)
    for element in seeds["/device/1"]:
        if element["href"] == actual:
            element["rep"][field] = value
    return seeds


@pytest.mark.parametrize(
    ("href", "field", "value"),
    [
        ("/power/vs/0", PREFIX + "power", "On"),
        ("/mode/vs/0", PREFIX + "options", ["DeviceType_NQ70M6650DS/AA1", "Sound_0"]),
        ("/mode/vs/0", PREFIX + "supportedModes", ["Bake", "NoOperation"]),
        ("/information/vs/0", PREFIX + "modelNum", "LCD_OV_WALL_16K|40401841|"),
    ],
)
def test_a_slot_that_differs_from_the_blank_one_is_not_filtered(href, field, value):
    """Synthetic variants bound the exclusion to the captured placeholder."""
    resources, oic_res, seeds = _load_device_full(NAME)

    _bound, materialized, _skipped, _full, _name = _discover_full(
        resources, oic_res, _slot_1_set(seeds, href, field, value)
    )

    assert [s.key for s in materialized] == ["1", "2"]


# The oven's Information names only the board it shares with the microwave, so
# with no record name it was named "<microwave> Tp6X Ov 17K". The reporter's
# /devices lists the oven with no name but type "Oven" (2026-10-29).
def test_an_unnamed_sibling_is_named_by_its_record_type(monkeypatch):
    monkeypatch.setattr("custom_components.localthings.legacy_http_transport._STATE", {})
    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.http.client.HTTPSConnection",
        _FakeConnection,
    )
    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.client_context",
        lambda cert_pem, key_pem: object(),
    )
    body = FIXTURE["device_bodies"]["2"]
    record = {
        key: value for key, value in body.items() if key not in ("Configuration", "Information")
    }
    _FakeConnection.log = []
    _FakeConnection.routes = {
        "/devices": (200, {"Devices": [{**record, "id": "2"}]}),
        "/devices/2/configuration": (200, {"Configuration": body["Configuration"]}),
        "/devices/2/information": (200, {"Information": body["Information"]}),
    }
    transport = LegacyHttpTransport(
        "10.0.0.7", 8888, cert_pem="CERT", key_pem="KEY", token="tok123456", family="TP6X_OV"
    )
    transport.connect()

    code, seed = transport.read(["device", "2"], timeout=10.0)

    assert code == 0x45
    view = {OVEN_SLOT.to_canonical(e["href"]): e["rep"] for e in seed}
    assert view["/information/vs/0"][PREFIX + "deviceName"] == "Oven"
