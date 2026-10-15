"""Sibling devices over 8888: the NV51K777OS Flex Duo's lower cavity (#572).

The bridge serves the lower cavity as `/devices/1` and lists it in `/devices`
only while the divider is in. The coordinator sees the same indexed sibling
an OCF dual-cavity oven presents (`/device/1`, hrefs ending `/1`), so the
existing subdevice machinery does the rest. Nothing here is per family.
"""

from __future__ import annotations

import json
from itertools import permutations
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import _drop_power_switches_superseded_by_sensors
from custom_components.localthings.binary_sensor import LocalThingsBinarySensor
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.entity import _is_included
from custom_components.localthings.legacy_http_transport import (
    LegacyHttpTransport,
    _with_record_description,
)
from custom_components.localthings.number import LocalThingsNumber
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.by_type import oven, resolve
from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.capabilities.operational import (
    OPERATIONAL_STATE,
    STOP_BUTTON,
)
from custom_components.localthings.registry.entities import BinarySensorDesc, SwitchDesc
from custom_components.localthings.registry.identity import DeviceIdentity
from custom_components.localthings.registry.subdevices import MAIN
from custom_components.localthings.select import LocalThingsSelect
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
    monkeypatch.setattr("custom_components.localthings.legacy_http_transport.time.sleep", Mock())


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
            "LowerHealthycook1",
            "LowerHealthycook2",
            "LowerHealthycook3",
            "LowerHealthycook4",
        ]
        assert _startable(resources, 1) == [
            "LowerBake",
            "LowerConvectionBake",
            "LowerConvectionRoast",
            "LowerHealthycook1",
            "LowerHealthycook2",
            "LowerHealthycook3",
            "LowerHealthycook4",
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

    def test_a_failed_listing_forgets_the_divider(self):
        """Unknown is not "in": the upper list goes back to as reported."""
        transport = _transport()
        _seed(transport, 0)
        _FakeConnection.routes["/devices"] = (500, None)

        modes = _supported(_seed(transport, 0), 0)

        assert "ConvectionBake" in modes

    def test_a_listing_that_times_out_still_reads_the_upper_cavity(self, monkeypatch):
        transport = _transport()
        _seed(transport, 0)

        def _timeout(timeout):
            raise TimeoutError("timed out")

        monkeypatch.setattr(transport, "_list_devices", _timeout)

        modes = _supported(_seed(transport, 0), 0)

        assert "UpperConvectionBake" in modes and "ConvectionBake" in modes

    def test_divider_out_the_lower_cavity_offers_nothing_to_start(self):
        _FakeConnection.routes = _routes(divided=False)

        resources = _seed(_transport(), 1)

        assert _supported(resources, 1) == ["NoOperation"]
        assert _startable(resources, 1) == []

    def test_a_list_entry_that_is_no_string_is_dropped(self):
        upper = {**UPPER, "Mode": {**UPPER["Mode"], "supportedModes": ["Bake,UpperBake", 5]}}
        _FakeConnection.routes = _routes(divided=False, upper=upper)

        assert _supported(_seed(_transport(), 0), 0) == ["Bake", "NoOperation"]

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
        assert _FakeConnection.log[0][:3] == ("GET", "/devices/1/configuration", None)
        method, path, body, _ = _FakeConnection.log[1]
        assert (method, path) == ("PUT", "/devices/1")
        assert body is not None
        assert body["Device"]["Mode"] == {"modes": ["LowerBake"]}
        assert "Operation" not in body["Device"]
        assert _FakeConnection.log[2][:3] == ("GET", "/devices/1/configuration", None)
        assert _FakeConnection.log[3][:3] == (
            "PUT",
            "/devices/1",
            {"Device": {"Operation": {"state": "Run"}}},
        )
        assert len(_FakeConnection.log) == 4

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

    @pytest.mark.parametrize("index,mode", [(0, "UpperConvectionBake"), (1, "LowerBake")])
    @pytest.mark.parametrize("failed_step", [0, 1])
    def test_a_refusal_stops_the_sequence_and_keeps_the_reason(
        self, index, mode, failed_step, monkeypatch
    ):
        transport = _transport()
        permission = (200, {"Configuration": {"remoteControlEnabled": True}})
        responses = [permission, (204, None)] * failed_step + [
            permission,
            (400, "Control fail, <Operation.state=Run>"),
        ]
        request = Mock(side_effect=responses)
        monkeypatch.setattr(transport, "_request", request)
        batch = cook.CookPlan(mode, 350, "Fahrenheit", 300).batch(
            lambda href: href[:-1] + str(index)
        )

        code, response = transport.write(["device", str(index)], batch, timeout=8.0)

        assert code == 0x80
        assert response == "Control fail, <Operation.state=Run>"
        assert request.call_count == (failed_step + 1) * 2
        assert "state" not in request.call_args_list[1].kwargs["body"]["Device"]["Operation"]

    @pytest.mark.parametrize("index", [0, 1])
    def test_settings_alone_do_not_start_a_cook(self, index):
        batch = [
            {"href": f"/mode/vs/{index}", "rep": {PREFIX + "modes": ["LowerBake"]}},
            {
                "href": f"/operational/state/vs/{index}",
                "rep": {PREFIX + "operationTime": "00:05:00"},
            },
        ]

        code, _ = _transport().write(["device", str(index)], batch, timeout=8.0)

        assert code == 0x44
        assert [entry[:3] for entry in _FakeConnection.log] == [
            (
                "PUT",
                f"/devices/{index}",
                {
                    "Device": {
                        "Mode": {"modes": ["LowerBake"]},
                        "Operation": {"operationTime": "00:05:00"},
                    }
                },
            )
        ]

    def test_a_bare_run_is_still_one_write(self):
        code, _ = _transport().write(
            ["operational", "state", "vs", "1"], {PREFIX + "state": "Run"}, timeout=8.0
        )

        assert code == 0x44
        assert [entry[:3] for entry in _FakeConnection.log] == [
            ("PUT", "/devices/1", {"Device": {"Operation": {"state": "Run"}}})
        ]


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


@pytest.mark.parametrize("divided", [False, True])
async def test_reservoir_is_shared_and_tracks_position_independently_of_door(
    hass: HomeAssistant, divided
):
    coordinator = await _discovered(hass, _routes(divided=divided))
    reservoirs = [
        b
        for b in coordinator.bound
        if b.desc.key == "reservoir_open" and _is_included(b, coordinator)
    ]
    assert {b.subdevice for b in reservoirs} == {MAIN, _lower(coordinator)}
    entities = [LocalThingsBinarySensor(coordinator, b) for b in reservoirs]
    assert len({e.unique_id for e in entities}) == 2
    assert all(e.device_class == "opening" for e in entities)
    assert not any(b.desc.key == "lamp" and _is_included(b, coordinator) for b in coordinator.bound)
    assert flatten(coordinator.bound, coordinator.entity_resources())["reservoir_open"] is False

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "oven_lcd_ov_wall_16k_reservoir.json").read_text()
    )
    _FakeConnection.log.clear()
    for sample in fixture["samples"]:
        rep = {**coordinator.resource("/mode/vs/0"), PREFIX + "options": sample["options"]}
        coordinator._observe.apply("/mode/vs/0", rep, source="poll")
        coordinator._observe.apply(
            "/doors/vs/0",
            {PREFIX + "items": [{PREFIX + "id": "0", PREFIX + "openState": sample["door"]}]},
            source="poll",
        )
        coordinator.async_set_updated_data(
            flatten(coordinator.bound, coordinator.entity_resources())
        )
        assert all(e.is_on is (sample["position"] == "open") for e in entities)
        assert coordinator.data["door_open"] is True
        assert coordinator.data["subdevice1_reservoir_open"] is coordinator.data["reservoir_open"]
        assert PREFIX + "options" not in coordinator.resource("/mode/vs/1")
        assert (
            PREFIX + "options"
            not in coordinator.device_resources(_lower(coordinator))["/mode/vs/0"]
        )

    for options in (
        [],
        None,
        ["waterTankInSwitch_On"],
        ["waterTankInSwitch_On", "waterTankOutSwitch_On"],
    ):
        coordinator._observe.apply("/mode/vs/0", {PREFIX + "options": options}, source="poll")
        coordinator.async_set_updated_data(
            flatten(coordinator.bound, coordinator.entity_resources())
        )
        assert all(e.is_on is None for e in entities)
    assert not _requests()


@pytest.mark.parametrize("divided", [False, True])
async def test_divider_is_shared_without_changing_raw_resources(hass: HomeAssistant, divided):
    coordinator = await _discovered(hass, _routes(divided=divided))
    assert resolve(coordinator.device_resources(_lower(coordinator))) is oven.REGISTRY
    dividers = [
        b for b in coordinator.bound if b.desc.key == "divider" and _is_included(b, coordinator)
    ]
    assert {b.subdevice for b in dividers} == {MAIN, _lower(coordinator)}
    entities = [LocalThingsBinarySensor(coordinator, b) for b in dividers]
    assert len({e.unique_id for e in entities}) == 2
    raw_upper = dict(coordinator.resource("/information/vs/0"))
    _FakeConnection.log.clear()
    for value, expected in [("On", True), ("Off", False), ("Unknown", None), (None, None)]:
        coordinator._observe.apply("/connected/vs/1", {PREFIX + "connected": value}, source="poll")
        coordinator.async_set_updated_data(
            flatten(coordinator.bound, coordinator.entity_resources())
        )
        assert all(e.is_on is expected for e in entities)
        assert coordinator.resource("/information/vs/0") == raw_upper
        assert coordinator.device_resources(MAIN)["/information/vs/0"] == raw_upper
    assert not _requests()


async def test_shared_sensors_are_included_on_offline_reload(hass: HomeAssistant):
    coordinator = await _discovered(hass)
    snapshot = coordinator.last_resources
    coordinator._rehydrate_resources = snapshot
    for key, cavity in [("divider", MAIN), ("reservoir_open", _lower(coordinator))]:
        bound = next(
            b
            for b in coordinator.bound
            if b.desc.key == key
            and b.subdevice == cavity
            and b.href == cavity.to_actual("/information/vs/0")
        )
        assert _is_included(bound, coordinator)
    assert coordinator.discovery_resources == snapshot


@pytest.mark.parametrize(
    "href,rep",
    [
        ("/information/vs/0", {PREFIX + "modelNum": "LCD_OV_WALL_20K|1|2"}),
        ("/information/vs/1", {PREFIX + "description": "OTHER"}),
    ],
)
async def test_shared_divider_and_reservoir_require_the_measured_pair(
    hass: HomeAssistant, href, rep
):
    coordinator = await _discovered(hass)
    coordinator._observe.apply(href, rep, source="poll")
    state = flatten(coordinator.bound, coordinator.entity_resources())
    assert "divider" not in state
    assert "subdevice1_reservoir_open" not in state


@pytest.mark.parametrize(
    "options,expected",
    [
        (["waterTankInSwitch_Off", "waterTankOutSwitch_On"], True),
        (["waterTankInSwitch_Off"], None),
        (["waterTankInSwitch_On", "waterTankOutSwitch_On"], None),
    ],
)
async def test_a_lower_reservoir_reading_is_not_replaced(hass: HomeAssistant, options, expected):
    coordinator = await _discovered(hass)
    coordinator._observe.apply("/mode/vs/1", {PREFIX + "options": options}, source="poll")
    state = flatten(coordinator.bound, coordinator.entity_resources())
    assert state["reservoir_open"] is False
    assert state.get("subdevice1_reservoir_open") is expected
    assert coordinator.entity_resources()["/mode/vs/1"] == coordinator.resource("/mode/vs/1")


@pytest.mark.parametrize("divided", [False, True])
async def test_lower_door_tracks_shared_door_without_changing_raw_resources(
    hass: HomeAssistant, divided
):
    coordinator = await _discovered(hass, _routes(divided=divided))
    lower = _lower(coordinator)
    for connected in ("Off", "On", "Off"):
        coordinator._observe.apply(
            "/connected/vs/1", {PREFIX + "connected": connected}, source="poll"
        )
        for open_state, expected in [("Close", False), ("Open", True), ("Close", False)]:
            rep = {PREFIX + "items": [{PREFIX + "id": "0", PREFIX + "openState": open_state}]}
            coordinator._observe.apply("/doors/vs/0", rep, source="poll")
            state = flatten(coordinator.bound, coordinator.entity_resources())
            assert state["door_open"] is expected
            assert state["subdevice1_door_open"] is expected
            assert coordinator.canonical_resources(lower)["/doors/vs/0"] == rep
            assert coordinator.device_resources(lower)["/doors/vs/0"] == {PREFIX + "items": []}
            assert coordinator.resource("/doors/vs/1") == {PREFIX + "items": []}

    coordinator._observe.apply("/doors/vs/0", {PREFIX + "items": []}, source="poll")
    state = flatten(coordinator.bound, coordinator.entity_resources())
    assert state["subdevice1_door_open"] is None


@pytest.mark.parametrize(
    "href,rep",
    [
        ("/information/vs/0", {PREFIX + "modelNum": "LCD_OV_WALL_OTHER|1|2"}),
        ("/information/vs/1", {PREFIX + "description": "LCD_OV_WALL_16K"}),
        ("/doors/vs/0", {PREFIX + "items": [{PREFIX + "id": "0"}]}),
        (
            "/doors/vs/0",
            {PREFIX + "items": [{PREFIX + "id": "0", PREFIX + "openState": "Unknown"}]},
        ),
    ],
)
async def test_shared_door_requires_the_measured_model_and_a_known_state(
    hass: HomeAssistant, href, rep
):
    coordinator = await _discovered(hass)
    coordinator._observe.apply(href, rep, source="poll")
    assert coordinator.entity_resources()["/doors/vs/1"] == {PREFIX + "items": []}


@pytest.mark.parametrize("divided", [False, True])
async def test_shared_door_preserves_a_lower_cavitys_own_reading(hass: HomeAssistant, divided):
    coordinator = await _discovered(hass, _routes(divided=divided))
    own_door = {PREFIX + "items": [{PREFIX + "id": "0", PREFIX + "openState": "Open"}]}
    coordinator._observe.apply("/doors/vs/1", own_door, source="poll")
    state = flatten(coordinator.bound, coordinator.entity_resources())
    assert state["door_open"] is False
    assert state["subdevice1_door_open"] is True
    assert coordinator.entity_resources()["/doors/vs/1"] == own_door


async def test_legacy_oven_power_is_status_only_on_both_cavities(hass: HomeAssistant):
    coordinator = await _discovered(hass)
    for cavity in (MAIN, _lower(coordinator)):
        power = [
            b
            for b in coordinator.bound
            if b.subdevice == cavity
            and b.desc.key == "power_switch"
            and _is_included(b, coordinator)
        ]
        assert len(power) == 1
        assert isinstance(power[0].desc, BinarySensorDesc)
        assert not isinstance(power[0].desc, SwitchDesc)


async def test_power_cleanup_removes_both_old_controls_and_preserves_other_entities(
    hass: HomeAssistant,
):
    coordinator = await _discovered(hass)
    registry = er.async_get(hass)
    old_controls = []
    keep = []
    for bound in coordinator.bound:
        if (
            isinstance(bound.desc, BinarySensorDesc)
            and bound.desc.key == "power_switch"
            and _is_included(bound, coordinator)
        ):
            unique_id = LocalThingsBinarySensor(coordinator, bound).unique_id
            assert unique_id is not None
            old = registry.async_get_or_create(
                "switch", DOMAIN, unique_id, config_entry=coordinator._entry
            )
            old_controls.append(
                registry.async_update_entity(
                    old.entity_id, new_entity_id=f"switch.renamed_power_{len(old_controls)}"
                )
            )
            keep.append(
                registry.async_get_or_create(
                    "binary_sensor", DOMAIN, unique_id, config_entry=coordinator._entry
                )
            )
    assert len(old_controls) == 2
    keep.append(
        registry.async_get_or_create(
            "switch", DOMAIN, "TEST_UNRELATED", config_entry=coordinator._entry
        )
    )
    other_entry = MockConfigEntry(domain=DOMAIN)
    other_entry.add_to_hass(hass)
    keep.append(
        registry.async_get_or_create(
            "switch", DOMAIN, "TEST_OTHER_power_switch", config_entry=other_entry
        )
    )

    for _ in range(2):
        _drop_power_switches_superseded_by_sensors(hass, coordinator._entry, coordinator)
        assert all(registry.async_get(row.entity_id) is None for row in old_controls)
        assert all(registry.async_get(row.entity_id) is not None for row in keep)


@pytest.mark.parametrize("readonly", [False, True])
async def test_power_cleanup_requires_status_support_and_the_owning_entry(
    hass: HomeAssistant, readonly
):
    coordinator = await _discovered(hass)
    if not readonly:
        for index in (0, 1):
            coordinator._observe.apply(
                f"/information/vs/{index}",
                {PREFIX + "modelNum": "OTHER_OVEN"},
                source="poll",
            )
    power = next(
        b
        for b in coordinator.bound
        if b.subdevice == MAIN
        and isinstance(b.desc, BinarySensorDesc)
        and b.desc.key == "power_switch"
    )
    unique_id = LocalThingsBinarySensor(coordinator, power).unique_id
    assert unique_id is not None
    other_entry = MockConfigEntry(domain=DOMAIN)
    other_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "switch",
        DOMAIN,
        unique_id,
        config_entry=other_entry if readonly else coordinator._entry,
    )

    _drop_power_switches_superseded_by_sensors(hass, coordinator._entry, coordinator)

    assert registry.async_get(old.entity_id) is not None


async def test_both_discovered_setpoints_expose_numeric_box_attributes(hass: HomeAssistant):
    hass.config.units = US_CUSTOMARY_SYSTEM
    coordinator = await _discovered(hass)
    for cavity in (MAIN, _lower(coordinator)):
        bound = next(
            b for b in coordinator.bound if b.subdevice == cavity and b.desc.key == "oven_setpoint"
        )
        entity = LocalThingsNumber(coordinator, bound)
        entity.hass = hass
        assert entity.capability_attributes == {
            "mode": "box",
            "min": 175.0,
            "max": 480.0,
            "step": 5.0,
        }
        assert entity.native_unit_of_measurement == "°F"


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("minutes", [0, 5, 59])
async def test_cook_time_before_mode_is_held_and_survives_the_first_mode_selection(
    hass: HomeAssistant, index, minutes
):
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator) if index else MAIN
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
    timer = LocalThingsNumber(coordinator, bound["cook_time_minutes"])
    select = LocalThingsSelect(coordinator, bound["oven_mode"])
    resources = coordinator._cavity_resources(cavity)
    _FakeConnection.log.clear()

    await timer.async_set_native_value(minutes)

    assert timer.native_value == minutes
    assert select.current_option == "no_operation"
    held = coordinator._held_cooks[cavity]
    assert held.values == {cook.PARAM_DURATION: minutes * 60}
    with pytest.raises(cook.CookStartError, match="cook_mode_required"):
        held.plan(resources)
    for href, rep in resources.items():
        coordinator._observe.apply(cavity.to_actual(href), rep, source="poll")
    assert coordinator.entity_resources()[cavity.to_actual(cook.OPERATION_HREF)][
        PREFIX + "operationTime"
    ] == cook.format_hms(minutes * 60)

    mode = "UpperConvectionBake" if index == 0 else "LowerBake"
    await coordinator.async_send_command(bound["oven_mode"], mode)
    await coordinator.async_send_command(bound["oven_setpoint"], 350)

    assert timer.native_value == minutes
    assert held.plan(resources) == cook.CookPlan(mode, 350, "Fahrenheit", minutes * 60)
    assert not _requests()
    assert coordinator._held_cooks.get(MAIN if index else _lower(coordinator)) is None


@pytest.mark.parametrize(
    "order", list(permutations(("oven_mode", "oven_setpoint", "cook_time_minutes")))
)
@pytest.mark.parametrize(
    "index,divided,mode",
    [(0, True, "UpperConvectionBake"), (1, True, "LowerBake"), (0, False, "Bake")],
)
async def test_idle_settings_can_be_entered_in_any_order(hass, order, index, divided, mode):
    coordinator = await _discovered(hass, _routes(divided=divided))
    cavity = _lower(coordinator) if index else MAIN
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    timer = LocalThingsNumber(coordinator, bound["cook_time_minutes"])
    select = LocalThingsSelect(coordinator, bound["oven_mode"])
    resources = coordinator._cavity_resources(cavity)
    _FakeConnection.log.clear()
    values = {"oven_mode": mode, "oven_setpoint": 350, "cook_time_minutes": 5}
    entered = set()

    for key in order:
        await coordinator.async_send_command(bound[key], values[key])
        entered.add(key)
        if "oven_setpoint" in entered:
            assert target.native_value == 350
        if "cook_time_minutes" in entered:
            assert timer.native_value == 5
        if "oven_mode" not in entered:
            assert select.current_option == "no_operation"
            with pytest.raises(cook.CookStartError, match="cook_mode_required"):
                coordinator._held_cooks[cavity].plan(resources)

    assert coordinator._held_cooks[cavity].plan(resources) == cook.CookPlan(
        mode, 350, "Fahrenheit", 300
    )
    for href, rep in resources.items():
        coordinator._observe.apply(cavity.to_actual(href), rep, source="poll")
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))
    assert target.native_value == 350
    assert timer.native_value == 5
    assert coordinator.resource(cavity.to_actual(cook.MODE_HREF))[PREFIX + "modes"] == [
        "NoOperation"
    ]
    assert not _requests()


@pytest.mark.parametrize(
    "index,divided,mode",
    [(0, True, "UpperConvectionBake"), (1, True, "LowerBake"), (0, False, "Bake")],
)
async def test_idle_cook_time_starts_blank_and_keeps_explicit_values(hass, index, divided, mode):
    coordinator = await _discovered(hass, _routes(divided=divided))
    cavity = _lower(coordinator) if index else MAIN
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
    timer = LocalThingsNumber(coordinator, bound["cook_time_minutes"])
    resources = coordinator._cavity_resources(cavity)
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))
    _FakeConnection.log.clear()

    assert coordinator.data[timer._state_key] == 0
    assert timer.native_value is None
    assert timer.native_min_value == 0
    assert timer.native_max_value == 59
    assert coordinator._held_cooks.get(cavity) is None

    await coordinator.async_send_command(bound["oven_mode"], mode)
    assert timer.native_value is None
    await coordinator.async_send_command(bound["oven_setpoint"], 350)
    assert timer.native_value is None
    assert coordinator._held_cooks[cavity].plan(resources).duration is None

    await timer.async_set_native_value(5)
    assert timer.native_value == 5
    await timer.async_set_native_value(0)
    assert timer.native_value == 0
    assert coordinator._held_cooks[cavity].plan(resources).duration == 0
    assert (
        coordinator.resource(cavity.to_actual(cook.OPERATION_HREF))[PREFIX + "operationTime"]
        == "00:00:00"
    )
    assert not _requests()


@pytest.mark.parametrize("index", [0, 1])
async def test_same_mode_reselection_preserves_settings_after_a_refused_start(hass, index):
    path = f"/devices/{index}"

    class RefuseFirstRun(dict):
        def get(self, key, default=None):
            if key == ("PUT", path) and _requests().count(key) == 2:
                return (400, {"errorDescription": "Control fail, <Operation.state=Run>"})
            return super().get(key, default)

    coordinator = await _discovered(hass, RefuseFirstRun(_routes()))
    cavity = _lower(coordinator) if index else MAIN
    mode = "LowerBake" if index else "UpperConvectionBake"
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    timer = LocalThingsNumber(coordinator, bound["cook_time_minutes"])
    _FakeConnection.log.clear()

    await coordinator.async_send_command(bound["oven_mode"], mode)
    await target.async_set_native_value(350)
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_send_command(bound["start_cooking"], "")
    assert err.value.translation_key == "command_refused"
    assert [entry[:3] for entry in _FakeConnection.log] == [
        ("GET", path + "/configuration", None),
        (
            "PUT",
            path,
            {
                "Device": {
                    "Mode": {"modes": [mode]},
                    "Temperatures": [{"id": "0", "desired": 350, "unit": "Fahrenheit"}],
                }
            },
        ),
        ("GET", path + "/configuration", None),
        ("PUT", path, {"Device": {"Operation": {"state": "Run"}}}),
    ]

    await timer.async_set_native_value(5)
    await coordinator.async_send_command(bound["oven_mode"], mode)

    assert target.native_value == 350
    assert timer.native_value == 5
    assert _requests() == [("GET", path + "/configuration"), ("PUT", path)] * 2
    await coordinator.async_send_command(bound["start_cooking"], "")
    assert [entry[:3] for entry in _FakeConnection.log[4:]] == [
        ("GET", path + "/configuration", None),
        (
            "PUT",
            path,
            {
                "Device": {
                    "Mode": {"modes": [mode]},
                    "Temperatures": [{"id": "0", "desired": 350, "unit": "Fahrenheit"}],
                    "Operation": {"operationTime": "00:05:00"},
                }
            },
        ),
        ("GET", path + "/configuration", None),
        ("PUT", path, {"Device": {"Operation": {"state": "Run"}}}),
    ]


@pytest.mark.parametrize("index", [0, 1])
async def test_pending_mode_does_not_display_unused_values_from_the_previous_cook(hass, index):
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator) if index else MAIN
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    timer = LocalThingsNumber(coordinator, bound["cook_time_minutes"])
    temp_href = cavity.to_actual(cook.TEMPERATURES_HREF)
    time_href = cavity.to_actual(cook.OPERATION_HREF)
    temperatures = coordinator.resource(temp_href)
    coordinator._observe.apply(
        temp_href,
        {
            **temperatures,
            PREFIX + "items": [{**temperatures[PREFIX + "items"][0], PREFIX + "desired": 350}],
        },
        source="poll",
    )
    coordinator._observe.apply(
        time_href,
        {**coordinator.resource(time_href), PREFIX + "operationTime": "00:05:00"},
        source="poll",
    )
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))
    _FakeConnection.log.clear()
    assert target.native_value == 350
    assert timer.native_value == 5

    recipe = "LowerHealthycook1" if index else "UpperHealthycook1"
    await coordinator.async_send_command(bound["oven_mode"], recipe)
    assert target.native_value == 375
    assert timer.native_value is None

    mode = "LowerBake" if index else "UpperConvectionBake"
    await coordinator.async_send_command(bound["oven_mode"], mode)
    assert target.native_value is None
    assert timer.native_value is None
    assert coordinator.resource(temp_href)[PREFIX + "items"][0][PREFIX + "desired"] == 350
    assert coordinator.resource(time_href)[PREFIX + "operationTime"] == "00:05:00"
    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_send_command(bound["start_cooking"], "")
    assert err.value.translation_key == "cook_temperature_required"
    assert not _requests()


async def test_running_zero_cook_time_stays_visible_and_returns_to_blank_when_idle(hass):
    running = {
        **LOWER,
        "Mode": {**LOWER["Mode"], "modes": ["LowerBake"]},
        "Operation": {**LOWER["Operation"], "state": "Run", "operationTime": "00:00:00"},
    }
    coordinator = await _discovered(hass, _routes(lower=running))
    cavity = _lower(coordinator)
    bound = next(
        b for b in coordinator.bound if b.subdevice == cavity and b.desc.key == "cook_time_minutes"
    )
    timer = LocalThingsNumber(coordinator, bound)
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))

    assert timer.native_value == 0

    coordinator._observe.apply(
        "/mode/vs/1",
        {**coordinator.resource("/mode/vs/1"), PREFIX + "modes": ["NoOperation"]},
        source="poll",
    )
    coordinator._observe.apply(
        "/operational/state/vs/1",
        {**coordinator.resource("/operational/state/vs/1"), PREFIX + "state": "Ready"},
        source="poll",
    )
    coordinator._observe.apply("/oven/vs/1", {PREFIX + "state": "Ready"}, source="poll")
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))
    assert timer.native_value is None


async def test_other_oven_families_keep_their_idle_zero_cook_time(hass):
    coordinator = await _discovered(hass)
    coordinator._observe.apply(
        "/information/vs/0",
        {**coordinator.resource("/information/vs/0"), PREFIX + "modelNum": "OTHER_OVEN"},
        source="poll",
    )
    bound = next(b for b in coordinator.bound if b.subdevice == MAIN and b.desc.key == "cook_time")
    timer = LocalThingsNumber(coordinator, bound)
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))

    assert timer.native_value == 0


@pytest.mark.parametrize(
    "language,label", [("en", "Healthy Cook Baked Potato"), ("de", "Gesundes Garen Ofenkartoffel")]
)
@pytest.mark.parametrize("action", ["held_setpoint", "start"])
async def test_cook_errors_use_the_cavitys_translated_recipe_label(
    hass, enable_custom_integrations, language, label, action
):
    hass.config.language = language
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator)
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
    coordinator._observe.apply(
        "/remotectrl/vs/1", {PREFIX + "remoteControlEnabled": True}, source="poll"
    )
    _FakeConnection.log.clear()

    if action == "held_setpoint":
        await coordinator.async_send_command(bound["oven_mode"], "LowerHealthycook1")
    with pytest.raises(ServiceValidationError) as err:
        if action == "start":
            await coordinator.async_start_cooking(cavity, mode="LowerHealthycook1", temperature=485)
        else:
            await coordinator.async_send_command(bound["oven_setpoint"], 485)

    assert err.value.translation_key == "cook_temperature_out_of_range"
    assert err.value.translation_placeholders == {"mode": label, "min": "175°F", "max": "480°F"}
    assert not _requests()


async def test_missing_mode_error_lists_friendly_names_without_starting(hass):
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator)
    coordinator._observe.apply(
        "/remotectrl/vs/1", {PREFIX + "remoteControlEnabled": True}, source="poll"
    )
    _FakeConnection.log.clear()

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_start_cooking(cavity)

    assert err.value.translation_key == "cook_mode_required"
    assert err.value.translation_placeholders == {
        "startable": "Bake, Convection Bake, Convection Roast, Healthy Cook Baked Potato, "
        "Healthy Cook Grilled Chicken, Healthy Cook Salmon Steak, Healthy Cook White Fish Fillet"
    }
    assert not _requests()


@pytest.mark.parametrize("state", ["Ready", "Run"])
async def test_whole_oven_excluded_modes_cannot_be_held_or_written(hass, state):
    mode = "NoOperation" if state == "Ready" else "Bake"
    upper = {
        **UPPER,
        "Mode": {**UPPER["Mode"], "modes": [mode]},
        "Operation": {**UPPER["Operation"], "state": state},
    }
    coordinator = await _discovered(hass, _routes(divided=False, upper=upper))
    bound = next(b for b in coordinator.bound if b.subdevice == MAIN and b.desc.key == "oven_mode")
    select = LocalThingsSelect(coordinator, bound)
    _FakeConnection.log.clear()

    for excluded in ("Drain", "Autocook", "SelfClean", "PyroFree", "Descale"):
        assert excluded in coordinator.resource(cook.MODE_HREF)[PREFIX + "supportedModes"]
        assert excluded not in select._raw_options()
        if state == "Ready":
            with pytest.raises(ServiceValidationError) as err:
                await coordinator.async_send_command(bound, excluded)
            assert err.value.translation_key == "cook_mode_not_startable"
        else:
            await coordinator.async_send_command(bound, excluded)

    assert "Bake" in select._raw_options()
    assert coordinator.resource(cook.MODE_HREF)[PREFIX + "modes"] == [mode]
    assert not _requests()


@pytest.mark.parametrize("index,prefix", [(0, "Upper"), (1, "Lower"), (0, "")])
@pytest.mark.parametrize("recipe,temperature", [(1, 375), (2, 375), (3, 375), (4, 400)])
async def test_recipe_selection_holds_its_raw_code_and_preset(
    hass: HomeAssistant, index, prefix, recipe, temperature
):
    coordinator = await _discovered(hass, _routes(divided=bool(prefix)))
    cavity = _lower(coordinator) if index else MAIN
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
    select = LocalThingsSelect(coordinator, bound["oven_mode"])
    assert select.translation_key == (
        "oven_mode_lcd_wall_16k" if index else "oven_cavity_oven_mode_lcd_wall_16k"
    )
    _FakeConnection.log.clear()

    option_prefix = f"{prefix.lower()}_" if prefix else ""
    await select.async_select_option(f"{option_prefix}healthycook{recipe}")

    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    assert target.native_value == temperature
    maximum = 480 if prefix else 550
    assert (target.native_min_value, target.native_max_value) == (175, maximum)
    await coordinator.async_send_command(bound["cook_time_hours"], 9)
    await coordinator.async_send_command(bound["cook_time_minutes"], 59)
    plan = coordinator._held_cooks[cavity].plan(coordinator._cavity_resources(cavity))
    assert plan.mode == f"{prefix}Healthycook{recipe}"
    assert plan.temperature == temperature
    assert plan.duration == 35940
    assert not _requests()
    assert coordinator._held_cooks.get(MAIN if index else _lower(coordinator)) is None

    with pytest.raises(ServiceValidationError):
        await coordinator.async_send_command(bound["oven_setpoint"], maximum + 5)
    with pytest.raises(ServiceValidationError):
        await coordinator.async_send_command(bound["cook_time_minutes"], 60)


@pytest.mark.parametrize("recipe,preset,maximum", [(5, 150, 225), (6, 100, 100)])
async def test_whole_oven_special_recipes_use_verified_presets_and_limits(
    hass, recipe, preset, maximum
):
    coordinator = await _discovered(hass, _routes(divided=False))
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == MAIN}
    select = LocalThingsSelect(coordinator, bound["oven_mode"])
    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    _FakeConnection.log.clear()

    await select.async_select_option(f"healthycook{recipe}")

    assert target.native_value == preset
    assert (target.native_min_value, target.native_max_value) == (100, maximum)
    assert target.available
    plan = coordinator._held_cooks[MAIN].plan(coordinator._cavity_resources(MAIN))
    assert (plan.mode, plan.temperature) == (f"Healthycook{recipe}", preset)
    assert not _requests()
    with pytest.raises(ServiceValidationError) as err:
        await target.async_set_native_value(maximum + 5)
    assert err.value.translation_key == "cook_temperature_out_of_range"
    assert target.native_value == preset
    assert not _requests()


@pytest.mark.parametrize("recipe,preset,maximum", [(5, 150, 225), (6, 100, 100)])
async def test_panel_started_special_recipes_keep_fixed_and_adjustable_bounds(
    hass, recipe, preset, maximum
):
    upper = {
        **UPPER,
        "Mode": {**UPPER["Mode"], "modes": [f"Healthycook{recipe}"]},
        "Operation": {**UPPER["Operation"], "state": "Run"},
        "Oven": {**UPPER["Oven"], "state": "Cooking"},
        "Temperatures": [{**UPPER["Temperatures"][0], "desired": preset}],
    }
    coordinator = await _discovered(hass, _routes(divided=False, upper=upper))
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == MAIN}
    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    assert target.native_value == preset
    assert (target.native_min_value, target.native_max_value) == (100, maximum)
    assert target.available
    _FakeConnection.log.clear()

    await coordinator.async_send_command(bound["oven_setpoint"], maximum + 5)

    assert not _requests()
    assert target.native_value == preset


@pytest.mark.parametrize("recipe", [5, 6])
async def test_special_recipes_do_not_guess_celsius_presets(hass, recipe):
    upper = {
        **UPPER,
        "Temperatures": [{**UPPER["Temperatures"][0], "unit": "Celsius"}],
    }
    coordinator = await _discovered(hass, _routes(divided=False, upper=upper))
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == MAIN}
    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    _FakeConnection.log.clear()

    await coordinator.async_send_command(bound["oven_mode"], f"Healthycook{recipe}")

    assert target.native_value is None
    assert target.available is (recipe == 5)
    if recipe == 5:
        assert (target.native_min_value, target.native_max_value) == (40, 105)
        await target.async_set_native_value(40)
        assert target.native_value == 40
    else:
        with pytest.raises(ServiceValidationError) as err:
            await target.async_set_native_value(40)
        assert err.value.translation_key == "cook_temperature_not_supported"
    assert not _requests()
    coordinator.last_update_success = False
    assert not target.available


def test_whole_recipe_labels_are_translated_without_relabeling_other_ovens():
    translations = Path(__file__).parent.parent / "custom_components/localthings/translations"
    for path in translations.glob("*.json"):
        selects = json.loads(path.read_text())["entity"]["select"]
        generic = selects["oven_mode"]["state"]
        measured = selects["oven_mode_lcd_wall_16k"]["state"]
        for recipe in range(1, 5):
            assert (
                measured[f"healthycook{recipe}"]
                == measured[f"upper_healthycook{recipe}"]
                == measured[f"lower_healthycook{recipe}"]
            )
            assert generic[f"healthycook{recipe}"].endswith(str(recipe))
        assert measured["healthycook5"].casefold().endswith(generic["dehydrate"].casefold())
        assert measured["healthycook6"].casefold().endswith(generic["bread_proof"].casefold())
        assert generic["healthycook5"].endswith("5")
        assert generic["healthycook6"].endswith("6")


@pytest.mark.parametrize("language,order", [("en", [1, 6, 5, 2, 3, 4]), ("de", [5, 2, 3, 1, 6, 4])])
async def test_oven_mode_order_uses_translated_labels_and_preserves_commands(
    hass, enable_custom_integrations, language, order
):
    hass.config.language = language
    coordinator = await _discovered(hass, _routes(divided=False))
    bound = next(b for b in coordinator.bound if b.subdevice == MAIN and b.desc.key == "oven_mode")
    strings = await async_get_translations(hass, language, "entity", {DOMAIN})
    select = LocalThingsSelect(coordinator, bound, strings)
    raw_before = select._raw_options()
    assert [option for option in select.options if option.startswith("healthycook")] == [
        f"healthycook{recipe}" for recipe in order
    ]
    assert select._raw_options() == raw_before
    _FakeConnection.log.clear()

    await select.async_select_option("healthycook4")

    plan = coordinator._held_cooks[MAIN].plan(coordinator._cavity_resources(MAIN))
    assert (plan.mode, plan.temperature) == ("Healthycook4", 400)
    assert select.current_option == "healthycook4"
    assert not _requests()


async def test_legacy_limits_hold_during_cooking_and_keep_other_models_unchanged(hass):
    running = {
        **LOWER,
        "Mode": {**LOWER["Mode"], "modes": ["LowerHealthycook4"]},
        "Operation": {**LOWER["Operation"], "state": "Run"},
    }
    coordinator = await _discovered(hass, _routes(lower=running))
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == _lower(coordinator)}
    timer = LocalThingsNumber(coordinator, bound["cook_time_minutes"])
    target = LocalThingsNumber(coordinator, bound["oven_setpoint"])
    assert timer.native_max_value == 59
    assert target.native_max_value == 480

    coordinator._observe.apply(
        "/information/vs/1", {PREFIX + "modelNum": "OTHER_OVEN"}, source="poll"
    )
    total = LocalThingsNumber(coordinator, bound["cook_time"])
    assert total.native_max_value == 1439
    assert target.native_max_value == 550
    assert LocalThingsSelect(coordinator, bound["oven_mode"]).translation_key == "oven_mode"


def test_recipe_fallback_does_not_change_an_unverified_board():
    modes = _supported(_seed(_transport("LCD_OV_WALL_20K"), 1), 1)
    assert not any(m.startswith("LowerHealthycook") for m in modes)


async def test_steam_minimum_and_whole_oven_maximum(hass):
    coordinator = await _discovered(hass)
    resources = coordinator._cavity_resources(MAIN)
    assert cook.temp_bounds(resources, "UpperSteamBake") == (230, 480, 5)
    coordinator = await _discovered(hass, _routes(divided=False))
    resources = coordinator._cavity_resources(MAIN)
    assert cook.temp_bounds(resources, "Bake") == (175, 550, 5)
    assert cook.temp_bounds(resources, "SteamBake") == (230, 550, 5)


async def test_live_mode_spec_takes_precedence_over_legacy_limits(hass):
    coordinator = await _discovered(hass)
    resources = coordinator._cavity_resources(MAIN)
    resources = {
        **resources,
        cook.MODE_HREF: {
            **resources[cook.MODE_HREF],
            cook.MODE_SPEC: [
                {
                    "mode": "UpperConvectionBake",
                    "control": "Start&Setting",
                    "tempMinF": "200",
                    "tempMaxF": "450",
                    "tempIntervalF": "10",
                    "timeMin": "00:01:00",
                    "timeMax": "02:00:00",
                }
            ],
        },
    }
    assert cook.temp_bounds(resources, "UpperConvectionBake") == (200, 450, 10)
    assert cook.legacy_wall_time_max(resources) is None
    assert cook.mode_specs(resources)["UpperConvectionBake"].time_max == 7200


@pytest.mark.parametrize("index,mode", [(0, "UpperConvectionBake"), (1, "LowerBake")])
@pytest.mark.parametrize("use_button", [False, True])
async def test_normal_start_keeps_the_lock_through_both_writes(
    hass: HomeAssistant, monkeypatch, index, mode, use_button
):
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator) if index else MAIN

    def bound(key):
        return next(b for b in coordinator.bound if b.subdevice == cavity and b.desc.key == key)

    if use_button:
        await coordinator.async_send_command(bound("oven_mode"), mode)
        await coordinator.async_send_command(bound("oven_setpoint"), 350)
        await coordinator.async_send_command(bound("cook_time_minutes"), 5)
    _FakeConnection.log.clear()

    def settle(seconds):
        assert seconds == 3.0
        assert coordinator._session_lock.locked()
        assert _requests() == [
            ("GET", f"/devices/{index}/configuration"),
            ("PUT", f"/devices/{index}"),
        ]

    sleep = Mock(side_effect=settle)
    monkeypatch.setattr("custom_components.localthings.legacy_http_transport.time.sleep", sleep)

    if use_button:
        await coordinator.async_send_command(bound("start_cooking"), "")
    else:
        await coordinator.async_start_cooking(cavity, mode=mode, temperature=350, duration=300)

    sleep.assert_called_once_with(3.0)
    assert [entry[:3] for entry in _FakeConnection.log] == [
        ("GET", f"/devices/{index}/configuration", None),
        (
            "PUT",
            f"/devices/{index}",
            {
                "Device": {
                    "Mode": {"modes": [mode]},
                    "Temperatures": [{"id": "0", "desired": 350, "unit": "Fahrenheit"}],
                    "Operation": {"operationTime": "00:05:00"},
                }
            },
        ),
        ("GET", f"/devices/{index}/configuration", None),
        ("PUT", f"/devices/{index}", {"Device": {"Operation": {"state": "Run"}}}),
    ]
    assert not coordinator._session_lock.locked()


@pytest.mark.parametrize("index", [0, 1])
async def test_legacy_start_still_requires_smart_control(hass: HomeAssistant, index):
    coordinator = await _discovered(hass, _remote_control_off(_routes()))
    cavity = _lower(coordinator) if index else MAIN
    _FakeConnection.log.clear()

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_start_cooking(
            cavity,
            mode="LowerBake" if index else "UpperConvectionBake",
            temperature=350,
            duration=300,
        )

    assert err.value.translation_key == "remote_control_disabled"
    assert _FakeConnection.log == []


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
    start = next(
        b for b in coordinator.bound if b.subdevice.key == "" and b.desc.key == "start_cooking"
    )

    with pytest.raises(ServiceValidationError):
        await coordinator.async_send_command(start, "")


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
