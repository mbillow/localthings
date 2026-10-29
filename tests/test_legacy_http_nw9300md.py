"""The NW9300MD microwave/oven combo over 8888 (#681): microwave on
`/devices/0`, an empty slot on 1, the oven on 2 and its lower cavity on 3,
which `/devices` lists only while the divider is in."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.localthings.legacy_http_transport import LegacyHttpTransport
from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.by_type import microwave, oven, resolve
from custom_components.localthings.registry.subdevices import enumerate_subdevices
from tests.conftest import FakeCoapSession
from tests.test_legacy_http_transport import _FakeConnection

PREFIX = "x.com.samsung.da."
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "oven_lcd_ov_wall_16k_nw9300md_8888.json").read_text(
        encoding="utf-8"
    )
)


def _routes(*, divided: bool) -> dict:
    listed = (0, 2, 3) if divided else (0, 2)
    routes: dict = {"/devices": (200, {"Devices": [FIXTURE[f"device{i}"] for i in listed]})}
    for i in range(4):
        routes[f"/devices/{i}"] = (200, {"Device": FIXTURE[f"device{i}"]})
        routes[f"/devices/{i}/information"] = (200, FIXTURE[f"device{i}_information"])
        routes[f"/devices/{i}/configuration"] = (200, FIXTURE[f"device{i}_configuration"])
    return routes


@pytest.fixture(autouse=True)
def _fake_https(monkeypatch):
    monkeypatch.setattr("custom_components.localthings.legacy_http_transport._STATE", {})
    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.http.client.HTTPSConnection",
        _FakeConnection,
    )
    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.client_context",
        lambda cert_pem, key_pem: object(),
    )
    _FakeConnection.log = []
    _FakeConnection.routes = _routes(divided=False)


def _transport() -> LegacyHttpTransport:
    device = LegacyHttpTransport(
        "10.0.0.9", 8888, cert_pem="CERT", key_pem="KEY", token="tok", family="LCD_OV_WALL_16K"
    )
    device.connect()
    return device


def _seed(transport, index: int) -> dict[str, dict]:
    code, body = transport.read(["device", str(index)], timeout=10.0)
    assert code == 0x45
    return parse_device0_batch(body)


def _canonical(resources: dict[str, dict], index: int) -> dict[str, dict]:
    if not index:
        return resources
    return {href[: -len(str(index))] + "0": rep for href, rep in resources.items()}


def _modes(transport, index: int) -> list[str]:
    rep = _canonical(_seed(transport, index), index)["/mode/vs/0"]
    return rep[PREFIX + "supportedModes"]


def _sweep(*, divided: bool) -> LegacyHttpTransport:
    _FakeConnection.routes = _routes(divided=divided)
    transport = _transport()
    _seed(transport, 0)
    return transport


def test_the_microwave_keeps_its_modes():
    transport = _transport()

    assert "MicroWave" in _modes(transport, 0)
    assert resolve(_seed(transport, 0)) is microwave.REGISTRY


def test_divider_out_the_oven_offers_its_whole_oven_modes():
    modes = _modes(_sweep(divided=False), 2)

    assert "ConvectionBake" in modes and "SteamBake" in modes
    assert not any(m.startswith(("Upper", "Lower")) for m in modes)


def test_divider_in_the_oven_offers_its_upper_modes():
    modes = _modes(_sweep(divided=True), 2)

    assert "UpperConvectionBake" in modes
    assert all(m.startswith("Upper") for m in modes if m != "NoOperation")


def test_divider_in_the_lower_cavity_offers_its_own_lower_modes():
    assert _modes(_sweep(divided=True), 3) == [
        "NoOperation",
        "LowerHealthycook4",
        "LowerHealthycook3",
        "LowerHealthycook2",
        "LowerHealthycook1",
        "LowerBake",
        "LowerConvectionBake",
    ]


def test_divider_out_the_lower_cavity_offers_nothing_but_is_an_oven():
    resources = _canonical(_seed(_sweep(divided=False), 3), 3)

    assert resources["/mode/vs/0"][PREFIX + "supportedModes"] == ["NoOperation"]
    assert resources["/connected/vs/0"] == {PREFIX + "connected": "Off"}
    assert resolve(resources) is oven.REGISTRY


@pytest.mark.parametrize("divided", [True, False])
def test_both_cavities_route_to_the_oven(divided):
    transport = _sweep(divided=divided)

    for index in (2, 3):
        assert resolve(_canonical(_seed(transport, index), index)) is oven.REGISTRY


def test_the_lower_cavity_is_probed_once_device_2_answers():
    transport = _sweep(divided=False)
    seeds = {
        f"/device/{i}": [{"href": h, "rep": r} for h, r in _seed(transport, i).items()]
        for i in (1, 2, 3)
    }
    probed: list[str] = []

    subdevices, _ = enumerate_subdevices(
        FakeCoapSession(seeds), {}, [], lambda href, found: probed.append(href)
    )

    assert [s.key for s in subdevices] == ["1", "2", "3"]
    assert probed[:3] == ["/device/1", "/device/2", "/device/3"]


def test_no_third_sibling_is_probed_without_a_second():
    probed: list[str] = []

    enumerate_subdevices(
        FakeCoapSession({"/device/1": [{"href": "/power/vs/1", "rep": {}}]}),
        {},
        [],
        lambda href, found: probed.append(href),
    )

    assert "/device/3" not in probed
