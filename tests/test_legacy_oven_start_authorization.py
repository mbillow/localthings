"""Fresh permission and at-most-once dispatch for legacy oven starts."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.localthings.const import CONF_BYPASS_REMOTE_CONTROL
from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.subdevices import MAIN
from custom_components.localthings.transport import AuthRejected
from tests.test_legacy_http_lower_cavity import (
    _discovered,
    _lower,
    _requests,
    _routes,
    _seed,
)
from tests.test_legacy_http_lower_cavity import (
    _fake_https as _fake_https,
)
from tests.test_legacy_http_lower_cavity import (
    _transport as _unseeded_transport,
)
from tests.test_legacy_http_transport import _FakeConnection


def _transport():
    transport = _unseeded_transport()
    _seed(transport, 0)
    _seed(transport, 1)
    _FakeConnection.log.clear()
    return transport


def _batch(index):
    mode = "LowerBake" if index else "UpperConvectionBake"
    return cook.CookPlan(mode, 350, "Fahrenheit", 300).batch(lambda href: href[:-1] + str(index))


@pytest.mark.parametrize(
    "index,divided,mode",
    [(0, True, "UpperConvectionBake"), (1, True, "LowerBake"), (0, False, "Bake")],
)
async def test_start_button_uses_the_held_recipe_and_its_own_cavity(hass, index, divided, mode):
    coordinator = await _discovered(hass, _routes(divided=divided))
    cavity = _lower(coordinator) if index else MAIN
    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == cavity}
    for key, value in [("oven_mode", mode), ("oven_setpoint", 350), ("cook_time", 5)]:
        await coordinator.async_send_command(bound[key], value)
    _FakeConnection.log.clear()

    await coordinator.async_send_command(bound["start_cooking"], "")

    path = f"/devices/{index}"
    assert [entry[:3] for entry in _FakeConnection.log] == [
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
    assert coordinator._held_cooks[cavity].values == {}


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("failed_step", [0, 1])
def test_a_refusal_stops_the_sequence_and_keeps_the_reason(index, failed_step, monkeypatch):
    transport = _transport()
    permission = (200, {"Configuration": {"remoteControlEnabled": True}})
    responses = [permission, (204, None)] * failed_step + [
        permission,
        (400, "Control fail, <Operation.state=Run>"),
    ]
    request = Mock(side_effect=responses)
    monkeypatch.setattr(transport, "_request", request)

    code, response = transport.write(["device", str(index)], _batch(index), timeout=8.0)

    assert code == 0x80
    assert response == "Control fail, <Operation.state=Run>"
    assert request.call_count == (failed_step + 1) * 2


def test_other_lcd_wall_boards_keep_one_combined_write():
    transport = _transport()
    transport._state_at(0).last_bodies["Information"]["modelID"] = "LCD_OV_WALL_30K"
    code, _ = transport.write(["device", "0"], _batch(0), timeout=8.0)

    assert code == 0x44
    assert _requests() == [("PUT", "/devices/0")]
    body = _FakeConnection.log[0][2]
    assert body is not None
    assert body["Device"]["Operation"] == {"operationTime": "00:05:00", "state": "Run"}


@pytest.mark.parametrize("index", [0, 1])
def test_settings_only_and_bare_run_keep_separate_single_writes(index):
    transport = _transport()
    settings = _batch(index)
    settings[-1]["rep"].pop("x.com.samsung.da.state")
    code, _ = transport.write(["device", str(index)], settings, timeout=8.0)
    assert code == 0x44
    assert _requests() == [("PUT", f"/devices/{index}")]

    _FakeConnection.log.clear()
    code, _ = transport.write(
        ["operational", "state", "vs", str(index)],
        {"x.com.samsung.da.state": "Run"},
        timeout=8.0,
    )
    assert code == 0x44
    assert [entry[:3] for entry in _FakeConnection.log] == [
        ("PUT", f"/devices/{index}", {"Device": {"Operation": {"state": "Run"}}})
    ]


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("phase", ["before settings", "before Run"])
@pytest.mark.parametrize(
    "response",
    [
        (200, {"Configuration": {"remoteControlEnabled": False}}),
        (200, {"Configuration": {"remoteControlEnabled": "false"}}),
        (200, {"Configuration": {"remoteControlEnabled": 1}}),
        (200, {"Configuration": {}}),
        (200, {"Configuration": None}),
        (200, "not JSON"),
        (204, None),
        (403, {"errorCode": "SHE-001"}),
        (500, None),
    ],
)
def test_unconfirmed_permission_never_sends_the_next_write(index, phase, response, monkeypatch):
    path = f"/devices/{index}"

    def disable(_seconds=None):
        _FakeConnection.routes[path + "/configuration"] = response

    if phase == "before settings":
        disable()
    else:
        monkeypatch.setattr(
            "custom_components.localthings.legacy_http_transport.time.sleep", disable
        )

    code, reason = _transport().write(["device", str(index)], _batch(index), timeout=8.0)

    assert code >> 5 != 2
    assert phase in reason
    puts = [entry for entry in _FakeConnection.log if entry[0] == "PUT"]
    assert len(puts) == (phase == "before Run")
    for _, _, body, _ in puts:
        assert body is not None
        assert "state" not in body["Device"]["Operation"]
    assert all(request_path.startswith(path) for _, request_path in _requests())


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("bypass", [False, True])
async def test_bypass_changes_apply_without_reconnecting_with_stale_true_cache(
    hass, monkeypatch, index, bypass
):
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator) if index else MAIN
    assert coordinator._remote_control_enabled(cavity)
    hass.config_entries.async_update_entry(
        coordinator._entry, options={CONF_BYPASS_REMOTE_CONTROL: bypass}
    )
    _FakeConnection.routes[f"/devices/{index}/configuration"] = (
        200,
        {"Configuration": {"remoteControlEnabled": False}},
    )
    _FakeConnection.log.clear()

    from custom_components.localthings.const import (
        CONF_DEVICE_TOKEN,
        CONF_HOST,
        CONF_LEGACY_FAMILY,
        CONF_PORT,
        CONF_TRANSPORT,
        TRANSPORT_LEGACY_HTTP,
    )

    # Exercise the actual coordinator -> factory -> transport option plumbing.
    hass.config_entries.async_update_entry(
        coordinator._entry,
        data={
            **coordinator._entry.data,
            CONF_HOST: "10.0.0.8",
            CONF_PORT: 8888,
            CONF_TRANSPORT: TRANSPORT_LEGACY_HTTP,
            CONF_LEGACY_FAMILY: "LCD_OV_WALL_16K",
            CONF_DEVICE_TOKEN: "tok",
        },
    )
    monkeypatch.setattr(
        "custom_components.localthings.coordinator.read_identity", lambda *args: None
    )
    coordinator._open_session(8888)
    transport = coordinator._session
    reconnect = Mock()
    monkeypatch.setattr(coordinator, "_connect_session", reconnect)
    for enabled in (bypass, not bypass, bypass):
        hass.config_entries.async_update_entry(
            coordinator._entry, options={CONF_BYPASS_REMOTE_CONTROL: enabled}
        )
        _FakeConnection.log.clear()
        if enabled:
            await coordinator.async_start_cooking(
                cavity,
                mode="LowerBake" if index else "UpperConvectionBake",
                temperature=350,
                duration=300,
            )
            assert _requests() == [("PUT", f"/devices/{index}")] * 2
        else:
            with pytest.raises(HomeAssistantError) as error:
                await coordinator.async_start_cooking(
                    cavity,
                    mode="LowerBake" if index else "UpperConvectionBake",
                    temperature=350,
                    duration=300,
                )
            assert error.value.translation_key == "command_refused"
            assert _requests() == [("GET", f"/devices/{index}/configuration")]
        assert coordinator._session is transport
        reconnect.assert_not_called()


@pytest.mark.parametrize("index", [0, 1])
async def test_start_is_not_replayed_after_a_lost_response(hass, monkeypatch, index):
    failed_request = 4
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator) if index else MAIN
    transport = coordinator._session
    attempts = []

    def request(method, path, **kwargs):
        attempts.append((method, path, kwargs.get("body")))
        if len(attempts) == failed_request:
            raise TimeoutError("response lost")
        return (
            (200, {"Configuration": {"remoteControlEnabled": True}})
            if method == "GET"
            else (204, None)
        )

    monkeypatch.setattr(transport, "_request", request)
    reconnect = Mock()
    monkeypatch.setattr(coordinator, "_connect_session", reconnect)

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_start_cooking(
            cavity,
            mode="LowerBake" if index else "UpperConvectionBake",
            temperature=350,
            duration=300,
        )

    assert error.value.translation_key == "cook_start_unconfirmed"
    assert len(attempts) == failed_request
    reconnect.assert_not_called()
    assert not coordinator._session_lock.locked()


@pytest.mark.parametrize("failed_request", [1, 2, 3])
def test_failures_before_run_remain_connection_errors(monkeypatch, failed_request):
    transport = _transport()
    permission = (200, {"Configuration": {"remoteControlEnabled": True}})
    responses = [permission, (204, None), permission][: failed_request - 1]
    request = Mock(side_effect=[*responses, TimeoutError("connection failed")])
    monkeypatch.setattr(transport, "_request", request)
    with pytest.raises(TimeoutError):
        transport.write(["device", "0"], _batch(0), timeout=8.0)
    assert request.call_count == failed_request
    assert not any(
        call.kwargs.get("body") == {"Device": {"Operation": {"state": "Run"}}}
        for call in request.call_args_list
    )


def test_explicit_auth_refusal_of_run_is_not_an_uncertain_start(monkeypatch):
    transport = _transport()
    permission = (200, {"Configuration": {"remoteControlEnabled": True}})
    request = Mock(side_effect=[permission, (204, None), permission, AuthRejected("token refused")])
    monkeypatch.setattr(transport, "_request", request)
    assert transport.write(["device", "0"], _batch(0), timeout=8.0) == (0x81, "token refused")
    assert request.call_count == 4


@pytest.mark.parametrize("failed_request", [1, 2, 3])
@pytest.mark.parametrize("lost_run_response", [False, True])
async def test_pre_run_failures_reconnect_without_replaying_run(
    hass, monkeypatch, failed_request, lost_run_response
):
    coordinator = await _discovered(hass)
    transport = coordinator._session
    attempts = []
    run = {"Device": {"Operation": {"state": "Run"}}}

    def request(method, path, **kwargs):
        body = kwargs.get("body")
        attempts.append((method, path, body))
        if len(attempts) == failed_request or (lost_run_response and body == run):
            raise TimeoutError("response lost")
        return (
            (200, {"Configuration": {"remoteControlEnabled": True}})
            if method == "GET"
            else (204, None)
        )

    monkeypatch.setattr(transport, "_request", request)
    reconnect = Mock(side_effect=lambda: setattr(coordinator, "_session", transport))
    monkeypatch.setattr(coordinator, "_connect_session", reconnect)
    monkeypatch.setattr(coordinator, "_RECONNECT_PAUSE_S", 0)
    if lost_run_response:
        with pytest.raises(HomeAssistantError) as error:
            await coordinator.async_start_cooking(
                MAIN, mode="UpperConvectionBake", temperature=350, duration=300
            )
        assert error.value.translation_key == "cook_start_unconfirmed"
    else:
        await coordinator.async_start_cooking(
            MAIN, mode="UpperConvectionBake", temperature=350, duration=300
        )
    reconnect.assert_called_once()
    assert sum(body == run for _, _, body in attempts) == 1
    assert len(attempts) == failed_request + 4


def test_same_board_combo_keeps_a_combined_start():
    from tests.test_legacy_http_nw9300md import _seed as combo_seed
    from tests.test_legacy_http_nw9300md import _sweep

    transport = _sweep(divided=True)
    for index in (0, 2, 3):
        combo_seed(transport, index)
        _FakeConnection.log.clear()
        transport.write(["device", str(index)], _batch(index), timeout=8.0)
        assert _requests() == [("PUT", f"/devices/{index}")]
        body = _FakeConnection.log[0][2]
        assert body is not None and body["Device"]["Operation"]["state"] == "Run"


@pytest.mark.parametrize("budget", [0.0, 2.0, 8.0])
def test_all_start_requests_share_one_timeout_budget(monkeypatch, budget):
    now = 0.0
    timeouts = []

    def sleep(seconds):
        nonlocal now
        now += seconds

    def request(method, _path, **kwargs):
        timeouts.append(kwargs["timeout"])
        sleep(1)
        return (
            (200, {"Configuration": {"remoteControlEnabled": True}})
            if method == "GET"
            else (204, None)
        )

    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.time",
        SimpleNamespace(monotonic=lambda: now, sleep=sleep),
    )
    transport = _transport()
    monkeypatch.setattr(transport, "_request", request)

    if budget < 8:
        with pytest.raises(TimeoutError):
            transport.write(["device", "0"], _batch(0), timeout=budget)
        assert timeouts == ([] if budget == 0 else [2.0, 1.0])
    else:
        assert transport.write(["device", "0"], _batch(0), timeout=budget)[0] == 0x44
        assert timeouts == [8.0, 7.0, 5.5, 4.5]
