"""The coordinator's side of starting a cook (issue #473): idle writes from
the mode/setpoint/cook-time entities are held rather than sent, the start
button and the start_cooking action send one Collection write, and a
rejected start sends nothing."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_PORT,
    DOMAIN,
    SERVICE_START_COOKING,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.subdevices import MAIN
from custom_components.localthings.services import async_setup_services
from custom_components.localthings.transport import Transport
from tests.conftest import _discover_full, _load_device_full
from tests.test_cook_start import _idle

ENTRY_DATA = {
    CONF_HOST: "10.0.0.199",
    CONF_PORT: 49154,
    CONF_LEAF_CERT_PEM: "-----BEGIN CERTIFICATE-----\nTEST-LEAF\n-----END CERTIFICATE-----",
    CONF_LEAF_KEY_PEM: "-----BEGIN PRIVATE KEY-----\nTEST-LEAF-KEY\n-----END PRIVATE KEY-----",
}


class _FakeSession:
    def __init__(self):
        self.post_calls: list[tuple[list[str], dict | list]] = []

    def write(self, path_segs, body, timeout=None):
        self.post_calls.append((list(path_segs), body))
        return 0x44, None

    def pace(self):
        pass


def _load(coord: LocalThingsCoordinator, resources: dict) -> None:
    for href, rep in resources.items():
        coord._observe.apply(href, rep, source="poll")
    reg = resolve(resources)
    assert reg is not None
    coord.bound = discover(resources, reg.capabilities, reg.pattern_capabilities)


@pytest.fixture
def bare_coordinator(hass: HomeAssistant) -> LocalThingsCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="localthings_COOK-TEST")
    entry.add_to_hass(hass)
    coord = LocalThingsCoordinator(hass, entry)
    coord.async_request_refresh = AsyncMock()
    coord._session = cast(Transport, _FakeSession())
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord
    async_setup_services(hass)
    return coord


@pytest.fixture
def coordinator(bare_coordinator: LocalThingsCoordinator) -> LocalThingsCoordinator:
    _load(bare_coordinator, _idle("range_ne63a6111ss"))
    return bare_coordinator


def _posts(coord):
    return cast(_FakeSession, coord._session).post_calls


def _entity(coord, key):
    return next(b for b in coord.bound if b.desc.key == key)


async def test_idle_choices_are_held_not_sent(coordinator) -> None:
    await coordinator.async_send_command(_entity(coordinator, "oven_mode"), "Bake")
    await coordinator.async_send_command(_entity(coordinator, "oven_setpoint"), 375)
    await coordinator.async_send_command(_entity(coordinator, "cook_time"), 20)

    assert _posts(coordinator) == []
    assert coordinator.data["oven_mode"] == "Bake"
    assert coordinator.data["oven_setpoint"] == 375
    assert coordinator.data["cook_time"] == 20


async def test_start_sends_the_held_choices_in_one_write(coordinator) -> None:
    await coordinator.async_send_command(_entity(coordinator, "oven_setpoint"), 375)
    await coordinator.async_send_command(_entity(coordinator, "cook_time"), 20)

    await coordinator.async_send_command(_entity(coordinator, "start_cooking"), "")

    assert len(_posts(coordinator)) == 1
    path, body = _posts(coordinator)[0]
    assert path == ["device", "0"]
    assert body[1:] == [
        {"href": "/mode/vs/0", "rep": {"x.com.samsung.da.modes": ["Bake"]}},
        {
            "href": "/temperatures/vs/0",
            "rep": {
                "x.com.samsung.da.items": [
                    {
                        "x.com.samsung.da.id": "0",
                        "x.com.samsung.da.desired": "375",
                        "x.com.samsung.da.unit": "Fahrenheit",
                    }
                ]
            },
        },
        {
            "href": "/operational/state/vs/0",
            "rep": {"x.com.samsung.da.operationTime": "00:20:00", "x.com.samsung.da.state": "Run"},
        },
    ]
    assert coordinator._held_cooks[MAIN].values == {}


async def test_a_mode_the_board_cannot_start_is_refused_while_idle(coordinator) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_send_command(_entity(coordinator, "oven_mode"), "Broil")

    assert err.value.translation_key == "cook_mode_not_startable"
    assert err.value.translation_placeholders == {"mode": "Broil", "startable": "Bake"}
    assert _posts(coordinator) == []


async def test_mid_cook_writes_go_straight_through(coordinator) -> None:
    op = coordinator.resource("/operational/state/vs/0")
    coordinator._observe.apply(
        "/operational/state/vs/0", {**op, "x.com.samsung.da.state": "Run"}, source="poll"
    )

    await coordinator.async_send_command(_entity(coordinator, "oven_setpoint"), 375)

    path, _body = _posts(coordinator)[0]
    assert path == ["temperatures", "vs", "0"]


async def test_start_is_refused_with_remote_control_off(coordinator) -> None:
    coordinator._observe.apply(
        "/remotectrl/vs/0", {"x.com.samsung.da.remoteControlEnabled": "false"}, source="poll"
    )

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_send_command(_entity(coordinator, "start_cooking"), "")

    assert err.value.translation_key == "remote_control_disabled"
    assert _posts(coordinator) == []


async def test_ocf_start_does_not_retry_a_lost_response(coordinator, monkeypatch) -> None:
    session = coordinator._session
    write = Mock(side_effect=TimeoutError("response lost"))
    reconnect = Mock()
    monkeypatch.setattr(session, "write", write)
    monkeypatch.setattr(coordinator, "_connect_session", reconnect)

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_start_cooking(mode="Bake", temperature=375, duration=300)

    assert error.value.translation_key == "cook_start_unconfirmed"
    write.assert_called_once()
    reconnect.assert_not_called()
    assert not coordinator._session_lock.locked()


@pytest.fixture
def device_id(hass: HomeAssistant, coordinator: LocalThingsCoordinator) -> str:
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=coordinator._entry.entry_id,
        identifiers=coordinator.device_info["identifiers"],
    )
    return device.id


async def _start(hass, device_id, **data):
    await hass.services.async_call(
        DOMAIN, SERVICE_START_COOKING, data, target={"device_id": device_id}, blocking=True
    )


async def test_action_starts_exactly_what_it_is_given(hass, coordinator, device_id) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM

    await _start(hass, device_id, mode="Bake", temperature=400, duration={"minutes": 45})

    _path, body = _posts(coordinator)[0]
    assert body[2]["rep"]["x.com.samsung.da.items"][0]["x.com.samsung.da.desired"] == "400"
    assert body[3]["rep"]["x.com.samsung.da.operationTime"] == "00:45:00"


async def test_action_takes_home_assistants_unit(hass, coordinator, device_id) -> None:
    """200C is 392F, which the 473 board's 5-degree step rounds to 390."""
    hass.config.units = METRIC_SYSTEM

    await _start(hass, device_id, mode="Bake", temperature=200)

    _path, body = _posts(coordinator)[0]
    assert body[2]["rep"]["x.com.samsung.da.items"][0]["x.com.samsung.da.desired"] == "390"


async def test_action_rejects_before_sending(hass, coordinator, device_id) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM

    with pytest.raises(ServiceValidationError) as err:
        await _start(hass, device_id, mode="Bake", duration={"hours": 12})

    assert err.value.translation_key == "cook_duration_out_of_range"
    assert _posts(coordinator) == []


async def test_a_lower_cavity_starts_from_its_own_collection(bare_coordinator) -> None:
    """The NE9801T's lower cavity: held choices and the start stay on its
    own hrefs, checked against the upper cavity's shared modeSpec."""
    resources, oic_res, seeds = _load_device_full("range_tp1x_da_ks_range_0101x")
    bound, subdevices, _skipped, full, _name = _discover_full(
        resources, oic_res, seeds, ("oic.wk.d", "oic.d.range")
    )
    for href, rep in full.items():
        bare_coordinator._observe.apply(href, rep, source="poll")
    bare_coordinator._observe.apply(
        "/remotectrl/vs/0", {"x.com.samsung.da.remoteControlEnabled": "true"}, source="poll"
    )
    bare_coordinator.subdevices = subdevices
    bare_coordinator.bound = bound
    (lower,) = subdevices
    lower_mode = next(b for b in bound if b.desc.key == "oven_mode" and b.subdevice == lower)
    lower_start = next(b for b in bound if b.desc.key == "start_cooking" and b.subdevice == lower)

    # The button's existence check sees the shared modeSpec.
    lower_view = bare_coordinator.discovery_canonical(lower)
    assert lower_start.desc.exists_fn is not None
    assert lower_start.desc.exists_fn(lower_view.get("/operational/state/vs/0", {}), lower_view)

    await bare_coordinator.async_send_command(lower_mode, "LowerBake")
    upper_mode = bare_coordinator.canonical_resources(MAIN)["/mode/vs/0"]["x.com.samsung.da.modes"]
    await bare_coordinator.async_send_command(lower_start, "")

    assert upper_mode == ["NoOperation"]
    path, body = _posts(bare_coordinator)[-1]
    assert path == ["device", "1"]
    assert [e["href"] for e in body] == [
        "/mode/vs/1",
        "/temperatures/vs/1",
        "/operational/state/vs/1",
    ]
    assert body[0]["rep"] == {"x.com.samsung.da.modes": ["LowerBake"]}
