"""Type-based legacy oven starts, shared permission checks and no Run replay."""

from copy import deepcopy
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


def _batch(index, mode=None):
    mode = mode or ("LowerBake" if index else "UpperConvectionBake")
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
        ("PUT", path, {"Device": {"Operation": {"state": "Run"}}}),
    ]
    assert coordinator._held_cooks[cavity].values == {}


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("failed_step", [0, 1])
def test_a_refusal_stops_the_sequence_and_keeps_the_reason(index, failed_step, monkeypatch):
    transport = _transport()
    responses = [(204, None)] * failed_step + [(400, "Control fail, <Operation.state=Run>")]
    request = Mock(side_effect=responses)
    monkeypatch.setattr(transport, "_request", request)

    code, response = transport.write(["device", str(index)], _batch(index), timeout=8.0)

    assert code == 0x80
    assert response == "Control fail, <Operation.state=Run>"
    assert request.call_count == failed_step + 1


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize(
    "information",
    [
        {"modelID": "LCD_OV_WALL_30K", "deviceSubType": "WallOven"},
        {"modelID": "LCD_OV_WALL_16K", "deviceSubType": "CombiOven"},
        {},
    ],
)
def test_the_oven_record_selects_the_sequence_without_a_family_or_model(index, information):
    # Synthetic identity variants of the captured Oven record; no prior sweep.
    _FakeConnection.routes = deepcopy(_routes())
    record = _FakeConnection.routes[f"/devices/{index}"][1]["Device"]
    record["Information"] = information
    transport = _unseeded_transport(family=None)

    code, _ = transport.write(["device", str(index)], _batch(index), timeout=8.0)

    assert code == 0x44
    assert _requests() == [
        ("GET", f"/devices/{index}"),
        ("PUT", f"/devices/{index}"),
        ("PUT", f"/devices/{index}"),
    ]
    body = _FakeConnection.log[1][2]
    assert body is not None
    assert body["Device"]["Operation"] == {"operationTime": "00:05:00"}
    assert _FakeConnection.log[2][2] == {"Device": {"Operation": {"state": "Run"}}}


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
@pytest.mark.parametrize("bypass", [False, True])
async def test_the_shared_smart_control_gate_reads_bypass_without_reconnecting(
    hass, monkeypatch, index, bypass
):
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator) if index else MAIN
    coordinator._observe.apply(
        f"/remotectrl/vs/{index}",
        {"x.com.samsung.da.remoteControlEnabled": "false"},
        source="poll",
    )
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
            assert error.value.translation_key == "remote_control_disabled"
            assert _requests() == []
        assert coordinator._session is transport
        reconnect.assert_not_called()


@pytest.mark.parametrize("index", [0, 1])
async def test_start_is_not_replayed_after_a_lost_response(hass, monkeypatch, index):
    failed_request = 2
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator) if index else MAIN
    transport = coordinator._session
    attempts = []

    def request(method, path, **kwargs):
        attempts.append((method, path, kwargs.get("body")))
        if len(attempts) == failed_request:
            raise TimeoutError("response lost")
        return 204, None

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


@pytest.mark.parametrize("seeded", [False, True])
def test_type_read_and_settings_failures_remain_connection_errors(monkeypatch, seeded):
    transport = _transport() if seeded else _unseeded_transport()
    request = Mock(side_effect=TimeoutError("connection failed"))
    monkeypatch.setattr(transport, "_request", request)
    with pytest.raises(TimeoutError):
        transport.write(["device", "0"], _batch(0), timeout=8.0)
    request.assert_called_once()
    assert request.call_args.args[0] == ("PUT" if seeded else "GET")
    assert request.call_args.kwargs.get("body") != {"Device": {"Operation": {"state": "Run"}}}


def test_explicit_auth_refusal_of_run_is_not_an_uncertain_start(monkeypatch):
    transport = _transport()
    request = Mock(side_effect=[(204, None), AuthRejected("token refused")])
    monkeypatch.setattr(transport, "_request", request)
    assert transport.write(["device", "0"], _batch(0), timeout=8.0) == (0x81, "token refused")
    assert request.call_count == 2


@pytest.mark.parametrize("lost_run_response", [False, True])
async def test_settings_failure_reconnects_without_replaying_run(
    hass, monkeypatch, lost_run_response
):
    coordinator = await _discovered(hass)
    transport = coordinator._session
    attempts = []
    run = {"Device": {"Operation": {"state": "Run"}}}

    def request(method, path, **kwargs):
        body = kwargs.get("body")
        attempts.append((method, path, body))
        if len(attempts) == 1 or (lost_run_response and body == run):
            raise TimeoutError("response lost")
        return 204, None

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
    assert len(attempts) == 3


@pytest.mark.parametrize(
    "index,mode",
    [(0, "PowerConvection"), (2, "UpperConvectionBake"), (3, "LowerBake")],
)
def test_combo_records_reporting_oven_get_the_same_sequence(index, mode):
    from tests.test_legacy_http_nw9300md import _seed as combo_seed
    from tests.test_legacy_http_nw9300md import _sweep

    transport = _sweep(divided=True)
    combo_seed(transport, index)
    _FakeConnection.routes[("PUT", f"/devices/{index}")] = (204, None)
    _FakeConnection.log.clear()

    code, _ = transport.write(["device", str(index)], _batch(index, mode), timeout=8.0)

    assert code == 0x44
    assert _requests() == [("PUT", f"/devices/{index}")] * 2
    body = _FakeConnection.log[0][2]
    assert body is not None
    assert body["Device"]["Mode"] == {"modes": [mode]}
    assert "state" not in body["Device"]["Operation"]
    assert _FakeConnection.log[1][2] == {"Device": {"Operation": {"state": "Run"}}}


@pytest.mark.parametrize("budget", [0.0, 0.5, 1.5, 8.0])
def test_settings_pause_and_run_share_one_timeout_budget(monkeypatch, budget):
    now = 0.0
    timeouts = []

    def sleep(seconds):
        nonlocal now
        now += seconds

    def request(method, _path, **kwargs):
        timeouts.append(kwargs["timeout"])
        sleep(1)
        return 204, None

    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.time",
        SimpleNamespace(monotonic=lambda: now, sleep=sleep),
    )
    transport = _transport()
    monkeypatch.setattr(transport, "_request", request)

    if budget < 8:
        with pytest.raises(TimeoutError):
            transport.write(["device", "0"], _batch(0), timeout=budget)
        assert timeouts == ([] if budget == 0 else [budget])
    else:
        assert transport.write(["device", "0"], _batch(0), timeout=budget)[0] == 0x44
        assert timeouts == [8.0, 6.5]
