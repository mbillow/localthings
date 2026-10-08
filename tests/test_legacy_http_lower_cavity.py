"""The NV51K777OS Flex Duo's lower cavity over 8888 (issue #572).

The bridge serves the lower cavity as `/devices/1` and lists it in `/devices`
only while the divider is in. The coordinator sees the same indexed sibling
an OCF dual-cavity oven presents (`/device/1`, hrefs ending `/1`), so the
existing subdevice machinery does the rest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.localthings.legacy_http_transport import LegacyHttpTransport
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.capabilities.operational import STOP_BUTTON
from custom_components.localthings.registry.identity import DeviceIdentity
from custom_components.localthings.transport import Transport
from tests.test_legacy_http_transport import _FakeConnection
from tests.test_subdevice_discovery import _coordinator

PREFIX = "x.com.samsung.da."
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "oven_lcd_ov_wall_16k_divided_8888.json").read_text(
        encoding="utf-8"
    )
)
UPPER, LOWER = FIXTURE["devices_divided"]["Devices"]


def _running(device: dict) -> dict:
    return {**device, "Operation": {**device["Operation"], "state": "Run"}}


def _routes(devices: dict, upper: dict = UPPER, lower: dict = LOWER) -> dict:
    return {
        "/devices": (200, devices),
        "/devices/0": (200, {"Device": upper}),
        "/devices/0/configuration": (200, FIXTURE["device0_configuration"]),
        "/devices/0/information": (200, FIXTURE["device0_information"]),
        "/devices/0/operation": (200, {"Operation": upper["Operation"]}),
        "/devices/1": (200, {"Device": lower}),
        "/devices/1/configuration": (200, FIXTURE["device1_configuration"]),
        "/devices/1/information": (200, FIXTURE["device1_information"]),
        "/devices/1/operation": (200, {"Operation": lower["Operation"]}),
        ("PUT", "/devices/0"): (204, None),
        ("PUT", "/devices/1"): (204, None),
    }


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
    _FakeConnection.routes = _routes(FIXTURE["devices_divided"])


def _transport(family: str = "LCD_OV_WALL_16K") -> LegacyHttpTransport:
    device = LegacyHttpTransport(
        "10.0.0.8", 8888, cert_pem="CERT", key_pem="KEY", token="tok", family=family
    )
    device.connect()
    return device


def _seed(transport, index: int) -> dict[str, dict]:
    code, body = transport.read(["device", str(index)], timeout=10.0)
    assert code == 0x45
    return parse_device0_batch(body)


def _requests() -> list[tuple[str, str]]:
    return [(method, path) for method, path, _, _ in _FakeConnection.log]


class TestReads:
    def test_the_lower_cavity_reads_as_an_indexed_sibling(self):
        resources = _seed(_transport(), 1)

        assert {href.rsplit("/", 1)[1] for href in resources} == {"1"}
        assert {
            "/mode/vs/1",
            "/operational/state/vs/1",
            "/temperatures/vs/1",
            "/remotectrl/vs/1",
            "/information/vs/1",
            "/connected/vs/1",
        } <= set(resources)
        assert ("GET", "/devices/1") in _requests()

    def test_it_names_itself_by_its_own_record(self):
        """/devices/1/information repeats the main's description; the record
        at /devices/1 says _DIV."""
        resources = _seed(_transport(), 1)

        assert resources["/information/vs/1"][PREFIX + "description"] == "LCD_OV_WALL_16K_DIV"
        assert resources["/information/vs/1"][PREFIX + "modelNum"].startswith("LCD_OV_WALL_16K|")

    def test_the_divider_is_in_while_devices_lists_the_cavity(self):
        assert _seed(_transport(), 1)["/connected/vs/1"] == {PREFIX + "connected": "On"}

    def test_the_divider_is_out_once_devices_drops_it(self):
        _FakeConnection.routes = _routes(FIXTURE["devices_no_divider"])

        assert _seed(_transport(), 1)["/connected/vs/1"] == {PREFIX + "connected": "Off"}

    def test_the_divider_reads_on_its_own_too(self):
        code, rep = _transport().read(["connected", "vs", "1"], timeout=10.0)

        assert (code, rep) == (0x45, {PREFIX + "connected": "On"})

    def test_a_single_lower_href_reads_its_own_endpoint(self):
        code, rep = _transport().read(["operational", "state", "vs", "1"], timeout=10.0)

        assert code == 0x45
        assert rep[PREFIX + "state"] == "Ready"
        assert _requests() == [("GET", "/devices/1/operation")]

    def test_the_main_is_unchanged(self):
        resources = _seed(_transport(), 0)

        assert all(href.endswith("/0") for href in resources)
        assert "/connected/vs/0" not in resources
        assert ("GET", "/devices") not in _requests()

    def test_a_third_device_is_not_served(self):
        code, _ = _transport().read(["device", "2"], timeout=10.0)

        assert code == 0x84
        assert _FakeConnection.log == []

    def test_a_single_device_family_serves_no_index(self):
        code, _ = _transport("TP6X_WASHER").read(["operational", "state", "vs", "1"], timeout=10.0)

        assert code == 0x84
        assert _FakeConnection.log == []


class TestWrites:
    def test_stop_on_the_lower_cavity_goes_to_devices_1(self):
        _FakeConnection.routes = _routes(FIXTURE["devices_divided"], lower=_running(LOWER))
        transport = _transport()

        code, _ = transport.write(
            ["operational", "state", "vs", "1"], {PREFIX + "state": "Ready"}, timeout=8.0
        )

        assert code == 0x44
        assert _FakeConnection.log[-1][:3] == (
            "PUT",
            "/devices/1",
            {"Device": {"Operation": {"state": "Ready"}}},
        )

    def test_a_lower_start_goes_to_devices_1(self):
        transport = _transport()
        resources = {
            href.replace("/vs/1", "/vs/0"): rep for href, rep in _seed(transport, 1).items()
        }
        plan = cook.plan_start(resources, "Bake", 350, None)
        _FakeConnection.log.clear()

        code, _ = transport.write(
            ["device", "1"], plan.batch(lambda href: href[:-1] + "1"), timeout=8.0
        )

        assert code == 0x44
        method, path, body, _ = _FakeConnection.log[0]
        assert (method, path) == ("PUT", "/devices/1")
        assert body is not None
        assert body["Device"]["Mode"] == {"modes": ["Bake"]}
        assert body["Device"]["Operation"]["state"] == "Run"

    def test_a_batch_naming_the_other_cavity_is_refused(self):
        batch = [{"href": "/operational/state/vs/0", "rep": {PREFIX + "state": "Run"}}]

        code, _ = _transport().write(["device", "1"], batch, timeout=8.0)

        assert code == 0x84
        assert _FakeConnection.log == []

    def test_each_cavity_holds_its_own_state(self):
        _FakeConnection.routes = _routes(FIXTURE["devices_divided"], upper=_running(UPPER))
        transport = _transport()
        _seed(transport, 0)
        _seed(transport, 1)

        # The upper cavity is running; the lower is idle, so its stop is
        # not sent (legacy_http_transport._already_ready).
        code, _ = transport.write(
            ["operational", "state", "vs", "1"], {PREFIX + "state": "Ready"}, timeout=8.0
        )

        assert code == 0x44
        assert ("PUT", "/devices/1") not in _requests()


def test_only_the_wall_oven_stops_without_remote_control():
    assert _transport().stop_without_remote_control
    assert not _transport("TP6X_WASHER").stop_without_remote_control
    assert not _transport("TP6X_RAC_16K").stop_without_remote_control


def test_diagnostics_carry_the_lower_cavity_bodies():
    transport = _transport()
    _seed(transport, 0)
    _seed(transport, 1)

    diagnostics = transport.diagnostics()

    assert diagnostics["device_bodies"]["1"]["Operation"] == LOWER["Operation"]
    assert "Diagnosis" in diagnostics["bodies"]


# ---------------------------------------------------------------------------
# Through the coordinator
# ---------------------------------------------------------------------------


async def _discovered(hass: HomeAssistant, routes: dict | None = None):
    if routes is not None:
        _FakeConnection.routes = routes
    coordinator = _coordinator(hass)
    transport = _transport()
    coordinator._session = cast(Transport, transport)
    coordinator._identity = DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="",
        name="",
        serial=None,
        device_types=(),
        raw={"/oic/p": {}, "/oic/d": {}, "/oic/res": []},
    )
    resources = _seed(transport, 0)
    merged = await hass.async_add_executor_job(
        coordinator._enumerate_subdevices_blocking, resources
    )
    coordinator._run_discovery(merged)
    for href, rep in coordinator._live_subdevice_resources(merged).items():
        coordinator._observe.apply(href, rep, source="poll")
    return coordinator


def _lower(coordinator):
    return next(s for s in coordinator.subdevices if s.key == "1")


async def test_the_lower_cavity_becomes_its_own_device(hass: HomeAssistant):
    coordinator = await _discovered(hass)
    lower = _lower(coordinator)

    assert coordinator.device_info_for(lower)["name"].endswith("Lower oven")
    state = flatten(coordinator.bound, coordinator._cache.snapshot())
    assert state["subdevice1_divider"] is True
    assert {"subdevice1_oven_mode", "subdevice1_machine_state"} <= set(state)
    assert "subdevice1_cloud_connected" not in state
    assert any(b.subdevice == lower and b.desc is STOP_BUTTON for b in coordinator.bound)


async def test_stop_reaches_the_lower_cavity_with_remote_control_off(hass: HomeAssistant):
    """The reporter's Stop with the bypass on stopped the upper cavity but
    not the lower (every write went to /devices/0)."""
    off = {"Configuration": {"remoteControlEnabled": False}}
    routes = _routes(FIXTURE["devices_divided"], lower=_running(LOWER))
    routes["/devices/0/configuration"] = (200, off)
    routes["/devices/1/configuration"] = (200, off)
    coordinator = await _discovered(hass, routes)
    lower = _lower(coordinator)
    stop = next(b for b in coordinator.bound if b.subdevice == lower and b.desc is STOP_BUTTON)
    _FakeConnection.log.clear()

    await coordinator.async_send_command(stop, STOP_BUTTON.payload)

    assert ("PUT", "/devices/1") in _requests()


async def test_other_writes_still_need_remote_control(hass: HomeAssistant):
    off = {"Configuration": {"remoteControlEnabled": False}}
    routes = _routes(FIXTURE["devices_divided"])
    routes["/devices/0/configuration"] = (200, off)
    coordinator = await _discovered(hass, routes)
    power = next(
        b for b in coordinator.bound if b.subdevice.key == "" and b.desc.key == "power_switch"
    )

    with pytest.raises(ServiceValidationError):
        await coordinator.async_send_command(power, False)
