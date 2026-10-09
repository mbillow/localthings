"""Sibling devices over 8888: the NV51K777OS Flex Duo's lower cavity (#572).

The bridge serves the lower cavity as `/devices/1` and lists it in `/devices`
only while the divider is in. The coordinator sees the same indexed sibling
an OCF dual-cavity oven presents (`/device/1`, hrefs ending `/1`), so the
existing subdevice machinery does the rest. Nothing here is per family.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.localthings.legacy_http_transport import (
    LegacyHttpTransport,
    _with_record_description,
)
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.by_type import oven, resolve
from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.capabilities.operational import (
    OPERATIONAL_STATE,
    STOP_BUTTON,
)
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
assert FIXTURE["devices_no_divider"]["Devices"] == [UPPER]


def _running(device: dict) -> dict:
    return {**device, "Operation": {**device["Operation"], "state": "Run"}}


def _routes(*, divided: bool = True, upper: dict = UPPER, lower: dict = LOWER) -> dict:
    return {
        "/devices": (200, {"Devices": [upper, lower] if divided else [upper]}),
        "/devices/0": (200, {"Device": upper}),
        "/devices/0/configuration": (200, FIXTURE["device0_configuration"]),
        "/devices/0/information": (200, FIXTURE["device0_information"]),
        "/devices/0/mode": (200, {"Mode": upper["Mode"]}),
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
    _FakeConnection.routes = _routes()


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
    def test_a_listed_sibling_reads_from_devices(self):
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
        assert _requests() == [
            ("GET", "/devices"),
            ("GET", "/devices/1/configuration"),
            ("GET", "/devices/1/information"),
        ]

    def test_an_unlisted_sibling_reads_its_own_record(self):
        """The divider is out: /devices drops the cavity, /devices/1 still
        answers."""
        _FakeConnection.routes = _routes(divided=False)

        resources = _seed(_transport(), 1)

        assert resources["/connected/vs/1"] == {PREFIX + "connected": "Off"}
        assert ("GET", "/devices/1") in _requests()

    def test_a_listed_sibling_reads_as_listed(self):
        assert _seed(_transport(), 1)["/connected/vs/1"] == {PREFIX + "connected": "On"}

    def test_it_names_itself_by_its_own_record(self):
        """/devices/1/information repeats the main's description; the record
        says _DIV."""
        resources = _seed(_transport(), 1)

        assert resources["/information/vs/1"][PREFIX + "description"] == "LCD_OV_WALL_16K_DIV"
        assert resources["/information/vs/1"][PREFIX + "modelNum"].startswith("LCD_OV_WALL_16K|")

    def test_listing_reads_on_its_own_too(self):
        code, rep = _transport().read(["connected", "vs", "1"], timeout=10.0)

        assert (code, rep) == (0x45, {PREFIX + "connected": "On"})

    def test_a_refused_listing_keeps_the_appliances_status(self):
        _FakeConnection.routes["/devices"] = (403, {"errorCode": "SHE-001"})

        code, _ = _transport().read(["connected", "vs", "1"], timeout=10.0)

        assert code == 0x83

    def test_a_listing_that_is_no_list_is_no_success(self):
        _FakeConnection.routes["/devices"] = (200, "<html>")

        code, _ = _transport().read(["connected", "vs", "1"], timeout=10.0)

        assert code == 0xA2

    def test_a_failed_listing_falls_back_to_the_record(self):
        _FakeConnection.routes["/devices"] = (500, None)

        resources = _seed(_transport(), 1)

        assert "/operational/state/vs/1" in resources
        assert "/connected/vs/1" not in resources
        assert ("GET", "/devices/1") in _requests()

    def test_a_sibling_shares_one_budget(self):
        assert _transport().read(["device", "1"], timeout=0.0) == (0xA4, None)
        assert _FakeConnection.log == []

    def test_a_single_sibling_href_reads_its_own_endpoint(self):
        code, rep = _transport().read(["operational", "state", "vs", "1"], timeout=10.0)

        assert code == 0x45
        assert rep[PREFIX + "state"] == "Ready"
        assert _requests() == [("GET", "/devices/1/operation")]

    def test_the_main_is_unchanged(self):
        resources = _seed(_transport(), 0)

        assert all(href.endswith("/0") for href in resources)
        assert "/connected/vs/0" not in resources

    def test_the_main_reads_the_listing_only_for_a_family_with_cavities(self):
        _seed(_transport(), 0)
        assert ("GET", "/devices") in _requests()

        _FakeConnection.log.clear()
        _FakeConnection.routes["/devices/0"] = (200, {"Device": {"Information": {}}})
        _seed(_transport("TP6X_WASHER"), 0)
        assert ("GET", "/devices") not in _requests()

    def test_a_sibling_the_bridge_does_not_serve_is_a_404(self):
        code, _ = _transport("TP6X_WASHER").read(["device", "2"], timeout=10.0)

        assert code == 0x84

    def test_a_record_naming_another_device_is_no_sibling(self):
        """A bridge echoing device 0 at any index must not produce a phantom."""
        _FakeConnection.routes["/devices/2"] = (200, {"Device": UPPER})

        code, _ = _transport().read(["device", "2"], timeout=10.0)

        assert code == 0x84


def _supported(resources: dict[str, dict], index: int) -> list[str]:
    return resources[f"/mode/vs/{index}"][PREFIX + "supportedModes"]


def _startable(resources: dict[str, dict], index: int) -> list[str]:
    canonical = {href[: -len(str(index))] + "0": rep for href, rep in resources.items()}
    return cook.startable_modes(canonical)


class TestCavityModes:
    """The bridge reports one static supportedModes on both cavities, divider
    in or out; what each cavity can run comes from the divider (#572)."""

    def test_divider_in_the_upper_cavity_offers_its_upper_modes(self):
        transport = _transport()

        modes = _supported(_seed(transport, 0), 0)

        assert "NoOperation" in modes
        assert all(m.startswith("Upper") for m in modes if m != "NoOperation")
        assert "UpperConvectionBake" in modes
        assert "ConvectionBake" not in modes

    def test_divider_out_the_whole_oven_offers_the_unprefixed_modes(self):
        _FakeConnection.routes = _routes(divided=False)

        modes = _supported(_seed(_transport(), 0), 0)

        assert "ConvectionBake" in modes
        assert not any(m.startswith(("Upper", "Lower")) for m in modes)

    def test_the_lower_cavity_offers_the_lower_modes(self):
        resources = _seed(_transport(), 1)

        assert _supported(resources, 1) == [
            "NoOperation",
            "LowerBake",
            "LowerConvectionBake",
            "LowerConvectionRoast",
        ]
        assert _startable(resources, 1) == [
            "LowerBake",
            "LowerConvectionBake",
            "LowerConvectionRoast",
        ]

    def test_only_upper_modes_start_with_the_divider_in(self):
        startable = _startable(_seed(_transport(), 0), 0)

        assert "UpperConvectionBake" in startable
        assert all(m.startswith("Upper") for m in startable)

    @pytest.mark.parametrize("divided", [True, False])
    def test_the_oven_routes_whatever_its_modes(self, divided):
        """Bake is in the list only with the divider out, so routing can't
        rest on it."""
        _FakeConnection.routes = _routes(divided=divided)

        assert resolve(_seed(_transport(), 0)) is oven.REGISTRY

    def test_an_unknown_divider_leaves_the_list_as_reported(self):
        _FakeConnection.routes["/devices"] = (500, None)

        modes = _supported(_seed(_transport(), 0), 0)

        assert "UpperConvectionBake" in modes and "ConvectionBake" in modes

    @pytest.mark.parametrize(
        ("index", "key", "mode"),
        [
            (0, "device0_running_divided", "UpperConvectionBake"),
            (1, "device1_running_divided", "LowerConvectionBake"),
        ],
    )
    def test_a_running_cavity_reports_one_of_its_own_modes(self, index, key, mode):
        running = FIXTURE[key]["Device"]
        if index:
            _FakeConnection.routes = _routes(lower=running)
        else:
            _FakeConnection.routes = _routes(upper=running)

        resources = _seed(_transport(), index)

        assert resources[f"/mode/vs/{index}"][PREFIX + "modes"] == [mode]
        assert mode in _supported(resources, index)

    def test_a_reported_mode_outside_the_list_is_kept(self):
        lower = {**LOWER, "Mode": {**LOWER["Mode"], "modes": ["LowerKeepWarm"]}}
        _FakeConnection.routes = _routes(lower=lower)

        assert "LowerKeepWarm" in _supported(_seed(_transport(), 1), 1)

    def test_a_single_mode_read_follows_the_last_listing(self):
        transport = _transport()
        _seed(transport, 0)

        code, rep = transport.read(["mode", "vs", "0"], timeout=10.0)

        assert code == 0x45
        assert "ConvectionBake" not in rep[PREFIX + "supportedModes"]


_INFO = {"Information": {"description": "LCD_OV_WALL_16K", "modelID": "LCD_OV_WALL_16K|1|2"}}


class TestRecordDescription:
    def test_a_suffix_is_carried(self):
        bodies = _with_record_description(_INFO, "LCD_OV_WALL_16K_DIV")

        assert bodies["Information"]["description"] == "LCD_OV_WALL_16K_DIV"

    @pytest.mark.parametrize(
        "description", ["LCD_OV_WALL_16K(0A1B2C3D)", "OTHER_DIV", "LCD_OV_WALL_16K_DIV2", None]
    )
    def test_anything_else_is_not(self, description):
        assert _with_record_description(_INFO, description) == _INFO


class TestWrites:
    def test_stop_on_the_lower_cavity_goes_to_devices_1(self):
        _FakeConnection.routes = _routes(lower=_running(LOWER))

        code, _ = _transport().write(
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
        plan = cook.plan_start(resources, "LowerBake", 350, None)
        _FakeConnection.log.clear()

        code, _ = transport.write(
            ["device", "1"], plan.batch(lambda href: href[:-1] + "1"), timeout=8.0
        )

        assert code == 0x44
        method, path, body, _ = _FakeConnection.log[0]
        assert (method, path) == ("PUT", "/devices/1")
        assert body is not None
        assert body["Device"]["Mode"] == {"modes": ["LowerBake"]}
        assert body["Device"]["Operation"]["state"] == "Run"

    def test_a_batch_naming_the_other_cavity_is_refused(self):
        batch = [{"href": "/operational/state/vs/0", "rep": {PREFIX + "state": "Run"}}]

        code, _ = _transport().write(["device", "1"], batch, timeout=8.0)

        assert code == 0x84
        assert _FakeConnection.log == []

    def test_an_unlisted_sibling_takes_no_command(self):
        _FakeConnection.routes = _routes(divided=False, lower=_running(LOWER))
        transport = _transport()
        _seed(transport, 1)
        _FakeConnection.log.clear()

        code, reason = transport.write(
            ["operational", "state", "vs", "1"], {PREFIX + "state": "Ready"}, timeout=8.0
        )

        assert code == 0x83
        assert "does not list device 1" in reason
        assert _FakeConnection.log == []

    def test_a_failed_listing_forgets_the_last_one(self):
        """Unknown is not unlisted: commands go through again."""
        _FakeConnection.routes = _routes(divided=False, lower=_running(LOWER))
        transport = _transport()
        _seed(transport, 1)
        _FakeConnection.routes["/devices"] = (500, None)
        _seed(transport, 1)

        code, _ = transport.write(
            ["operational", "state", "vs", "1"], {PREFIX + "state": "Ready"}, timeout=8.0
        )

        assert code == 0x44
        assert ("PUT", "/devices/1") in _requests()

    def test_each_cavity_holds_its_own_state(self):
        _FakeConnection.routes = _routes(upper=_running(UPPER))
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


def test_only_stop_is_declared_free_of_remote_control():
    assert not STOP_BUTTON.needs_remote_control
    others = [e for e in OPERATIONAL_STATE.entities if e is not STOP_BUTTON]
    assert others
    assert all(e.needs_remote_control for e in others)


def test_diagnostics_carry_the_lower_cavity_bodies():
    transport = _transport()
    _seed(transport, 0)
    _seed(transport, 1)

    diagnostics = transport.diagnostics()

    assert diagnostics["device_bodies"]["1"]["Operation"] == LOWER["Operation"]
    assert "Diagnosis" in diagnostics["bodies"]


def test_diagnostics_list_only_devices_that_were_read():
    transport = _transport()
    _seed(transport, 0)
    transport.write(["operational", "state", "vs", "3"], {PREFIX + "state": "Ready"}, timeout=8.0)

    assert "device_bodies" not in transport.diagnostics()


# ---------------------------------------------------------------------------
# Through the coordinator
# ---------------------------------------------------------------------------


async def _discovered(hass: HomeAssistant, routes: dict | None = None):
    if routes is not None:
        _FakeConnection.routes = routes
    coordinator = _coordinator(hass)
    coordinator.async_request_refresh = AsyncMock()
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


class _StopsOnStop(dict):
    """Routes where the lower cavity reads Run until a PUT reaches it."""

    def get(self, key, default=None):
        if key == "/devices/1/operation" and ("PUT", "/devices/1") in _requests():
            return (200, {"Operation": LOWER["Operation"]})
        return super().get(key, default)


def _remote_control_off(routes: dict) -> dict:
    off = {"Configuration": {"remoteControlEnabled": False}}
    routes["/devices/0/configuration"] = (200, off)
    routes["/devices/1/configuration"] = (200, off)
    return routes


@pytest.fixture
def _no_confirm_wait(monkeypatch):
    monkeypatch.setattr(
        "custom_components.localthings.coordinator.LocalThingsCoordinator._CONFIRM_DELAY_S", 0.0
    )


@pytest.mark.usefixtures("_no_confirm_wait")
async def test_stop_reaches_the_lower_cavity_with_remote_control_off(hass: HomeAssistant):
    """The reporter's Stop with the bypass on stopped the upper cavity but
    not the lower (every write went to /devices/0)."""
    routes = _StopsOnStop(_remote_control_off(_routes(lower=_running(LOWER))))
    coordinator = await _discovered(hass, routes)
    lower = _lower(coordinator)
    stop = next(b for b in coordinator.bound if b.subdevice == lower and b.desc is STOP_BUTTON)
    _FakeConnection.log.clear()

    await coordinator.async_send_command(stop, STOP_BUTTON.payload)

    assert ("PUT", "/devices/1") in _requests()


@pytest.mark.usefixtures("_no_confirm_wait")
async def test_a_stop_not_shown_with_remote_control_off_says_so(hass: HomeAssistant):
    """An appliance that answers 2.04 and still reads Run says so, without
    claiming why: it may have dropped it, or still be winding down."""
    routes = _remote_control_off(_routes(lower=_running(LOWER)))
    coordinator = await _discovered(hass, routes)
    lower = _lower(coordinator)
    stop = next(b for b in coordinator.bound if b.subdevice == lower and b.desc is STOP_BUTTON)

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_send_command(stop, STOP_BUTTON.payload)

    assert err.value.translation_key == "command_not_confirmed"
    assert ("PUT", "/devices/1") in _requests()
    coordinator.async_request_refresh.assert_awaited()


async def test_other_writes_still_need_remote_control(hass: HomeAssistant):
    off = {"Configuration": {"remoteControlEnabled": False}}
    routes = _routes()
    routes["/devices/0/configuration"] = (200, off)
    coordinator = await _discovered(hass, routes)
    power = next(
        b for b in coordinator.bound if b.subdevice.key == "" and b.desc.key == "power_switch"
    )

    with pytest.raises(ServiceValidationError):
        await coordinator.async_send_command(power, False)


async def test_a_refused_stop_reaches_the_user(hass: HomeAssistant):
    """Stop no longer waits on Remote Control, so an appliance that refuses
    it has to say so."""
    off = {"Configuration": {"remoteControlEnabled": False}}
    routes = _routes(lower=_running(LOWER))
    routes["/devices/1/configuration"] = (200, off)
    routes[("PUT", "/devices/1")] = (403, {"errorCode": "SHE-001"})
    coordinator = await _discovered(hass, routes)
    lower = _lower(coordinator)
    stop = next(b for b in coordinator.bound if b.subdevice == lower and b.desc is STOP_BUTTON)

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_send_command(stop, STOP_BUTTON.payload)

    assert err.value.translation_key == "command_refused"
    placeholders = err.value.translation_placeholders or {}
    assert "4.03 Remote Control is off" in placeholders["error"]


async def test_a_command_to_an_unlisted_sibling_is_refused_aloud(hass: HomeAssistant):
    coordinator = await _discovered(hass, _routes(divided=False))
    lower = _lower(coordinator)
    stop = next(b for b in coordinator.bound if b.subdevice == lower and b.desc is STOP_BUTTON)
    _FakeConnection.log.clear()

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_send_command(stop, STOP_BUTTON.payload)

    assert err.value.translation_key == "command_refused"
    assert not any(method == "PUT" for method, _ in _requests())
