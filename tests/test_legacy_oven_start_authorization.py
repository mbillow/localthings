"""Fresh permission and at-most-once dispatch for legacy oven starts."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.localthings.const import CONF_BYPASS_REMOTE_CONTROL
from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.subdevices import MAIN
from tests.test_legacy_http_lower_cavity import (
    _discovered,
    _lower,
    _requests,
    _transport,
)
from tests.test_legacy_http_lower_cavity import (
    _fake_https as _fake_https,
)
from tests.test_legacy_http_transport import _FakeConnection


def _batch(index):
    mode = "LowerBake" if index else "UpperConvectionBake"
    return cook.CookPlan(mode, 350, "Fahrenheit", 300).batch(lambda href: href[:-1] + str(index))


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
async def test_live_off_blocks_start_even_with_stale_true_cache(hass, index, bypass):
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

    with pytest.raises(HomeAssistantError):
        await coordinator.async_start_cooking(
            cavity,
            mode="LowerBake" if index else "UpperConvectionBake",
            temperature=350,
            duration=300,
        )

    assert _requests() == [("GET", f"/devices/{index}/configuration")]


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("failed_request", [1, 2, 3, 4])
async def test_start_is_not_replayed_after_a_lost_response(
    hass, monkeypatch, index, failed_request
):
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


@pytest.mark.parametrize("budget", [0.0, 4.0, 8.0])
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
        assert timeouts == ([] if budget == 0 else [4.0, 3.0])
    else:
        assert transport.write(["device", "0"], _batch(0), timeout=budget)[0] == 0x44
        assert timeouts == [8.0, 7.0, 3.0, 2.0]
