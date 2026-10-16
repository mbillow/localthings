"""Untimed legacy starts confirm Run and timer removal without replaying heating commands."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.subdevices import MAIN
from custom_components.localthings.transport import UntimedStartUnconfirmed
from tests.test_legacy_http_lower_cavity import _discovered, _lower, _transport
from tests.test_legacy_http_lower_cavity import _fake_https as _fake_https

PERMITTED = (200, {"Configuration": {"remoteControlEnabled": True}})
ACCEPTED = (204, None)


def _operation(state="Run", duration="00:01:00", remaining=None):
    return 200, {
        "Operation": {
            "state": state,
            "operationTime": duration,
            "remainingTime": duration if remaining is None else remaining,
        }
    }


def _responses():
    return [
        PERMITTED,
        ACCEPTED,
        PERMITTED,
        ACCEPTED,
        _operation(),
        PERMITTED,
        ACCEPTED,
        _operation(duration="00:00:00"),
    ]


def _batch(index=1, duration=0):
    return cook.CookPlan(
        "LowerBake" if index else "UpperConvectionBake", 400, "Fahrenheit", duration
    ).batch(lambda href: href[:-1] + str(index))


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("failed_request", range(1, 9))
async def test_uncertain_untimed_start_is_never_replayed(hass, monkeypatch, index, failed_request):
    coordinator = await _discovered(hass)
    cavity = _lower(coordinator) if index else MAIN
    responses = _responses()
    request = Mock(side_effect=[*responses[: failed_request - 1], TimeoutError("response lost")])
    monkeypatch.setattr(coordinator._session, "_request", request)
    reconnect = Mock()
    monkeypatch.setattr(coordinator, "_connect_session", reconnect)

    with pytest.raises(HomeAssistantError) as error:
        await coordinator.async_start_cooking(
            cavity,
            mode="LowerBake" if index else "UpperConvectionBake",
            temperature=400,
            duration=0,
        )

    assert error.value.translation_key == (
        "cook_untimed_unconfirmed" if failed_request >= 4 else "cook_start_unconfirmed"
    )
    assert request.call_count == failed_request
    reconnect.assert_not_called()
    assert not coordinator._session_lock.locked()


@pytest.mark.parametrize(
    "permission",
    [
        (200, {"Configuration": {"remoteControlEnabled": False}}),
        (200, {"Configuration": {"remoteControlEnabled": "false"}}),
        (200, {"Configuration": {"remoteControlEnabled": 1}}),
        (200, {"Configuration": {}}),
        (403, None),
        (500, None),
    ],
)
def test_timer_clear_requires_fresh_permission(monkeypatch, permission):
    transport = _transport()
    request = Mock(side_effect=[*_responses()[:5], permission])
    monkeypatch.setattr(transport, "_request", request)

    with pytest.raises(UntimedStartUnconfirmed, match="before timer clear"):
        transport.write(["device", "1"], _batch(), timeout=30)

    assert request.call_count == 6
    assert len([call for call in request.call_args_list if call.args[0] == "PUT"]) == 2


def test_refused_run_never_clears_a_timer(monkeypatch):
    transport = _transport()
    refused = (400, "Control fail, <Operation.state=Run>")
    request = Mock(side_effect=[*_responses()[:3], refused])
    monkeypatch.setattr(transport, "_request", request)

    assert transport.write(["device", "1"], _batch(), timeout=30) == (0x80, refused[1])
    assert request.call_count == 4


@pytest.mark.parametrize(
    "operation",
    [
        _operation("Ready"),
        _operation("Pause"),
        (200, {"Operation": {}}),
        (200, "invalid"),
        (204, None),
        (500, None),
    ],
)
def test_unconfirmed_run_never_clears_a_timer(monkeypatch, operation):
    transport = _transport()
    request = Mock(side_effect=[*_responses()[:4], *([operation] * 5)])
    monkeypatch.setattr(transport, "_request", request)

    with pytest.raises(UntimedStartUnconfirmed, match="after Run"):
        transport.write(["device", "1"], _batch(), timeout=30)

    puts = [call for call in request.call_args_list if call.args[0] == "PUT"]
    assert len(puts) == 2
    assert puts[-1].kwargs["body"] == {"Device": {"Operation": {"state": "Run"}}}


@pytest.mark.parametrize(
    "operation",
    [
        _operation(),
        _operation(duration="00:00:00", remaining="00:01:00"),
        _operation("Ready", "00:00:00"),
        (200, {"Operation": {"state": "Run", "operationTime": "00:00:00"}}),
        (200, None),
        (500, None),
    ],
)
def test_timer_clear_must_be_verified_while_still_running(monkeypatch, operation):
    transport = _transport()
    request = Mock(side_effect=[*_responses()[:7], *([operation] * 5)])
    monkeypatch.setattr(transport, "_request", request)

    with pytest.raises(UntimedStartUnconfirmed, match="after timer clear"):
        transport.write(["device", "1"], _batch(), timeout=30)

    puts = [call for call in request.call_args_list if call.args[0] == "PUT"]
    assert len(puts) == 3
    assert puts[-1].kwargs["body"] == {"Device": {"Operation": {"operationTime": "00:00:00"}}}


def test_refused_timer_clear_is_reported_without_another_write(monkeypatch):
    transport = _transport()
    request = Mock(side_effect=[*_responses()[:6], (400, "Control fail")])
    monkeypatch.setattr(transport, "_request", request)

    with pytest.raises(UntimedStartUnconfirmed, match="Timer clear refused"):
        transport.write(["device", "1"], _batch(), timeout=30)

    assert request.call_count == 7


@pytest.mark.parametrize("duration", [None, 0])
@pytest.mark.parametrize("stale_reads", [1, 4])
def test_delayed_readback_can_confirm_without_repeating_writes(monkeypatch, duration, stale_reads):
    transport = _transport()
    responses = _responses()
    responses[4:4] = [_operation("Ready")] * stale_reads
    responses[7 + stale_reads : 7 + stale_reads] = [_operation()] * stale_reads
    request = Mock(side_effect=responses)
    monkeypatch.setattr(transport, "_request", request)
    sleep = Mock()
    monkeypatch.setattr("custom_components.localthings.legacy_http_transport.time.sleep", sleep)
    batch = _batch(duration=duration)
    original = deepcopy(batch)

    assert transport.write(["device", "1"], batch, timeout=30) == (0x44, None)

    assert batch == original
    puts = [
        call.kwargs["body"]["Device"] for call in request.call_args_list if call.args[0] == "PUT"
    ]
    assert len(puts) == 3
    assert puts[0]["Operation"] == {"operationTime": "00:01:00"}
    assert puts[1] == {"Operation": {"state": "Run"}}
    assert puts[2] == {"Operation": {"operationTime": "00:00:00"}}
    retry_delays = [0.25, 0.25, 0.5, 1.0][:stale_reads]
    assert [call.args[0] for call in sleep.call_args_list] == [0.5, *retry_delays, *retry_delays]


@pytest.mark.parametrize("duration", [None, 0, 60])
def test_other_legacy_oven_family_keeps_original_start(monkeypatch, duration):
    transport = _transport("LCD_OV_WALL_20K")
    request = Mock(side_effect=_responses()[:4])
    monkeypatch.setattr(transport, "_request", request)
    sleep = Mock()
    monkeypatch.setattr("custom_components.localthings.legacy_http_transport.time.sleep", sleep)

    assert transport.write(["device", "1"], _batch(duration=duration), timeout=8) == (0x44, None)

    assert request.call_count == 4
    sleep.assert_called_once_with(3.0)
    settings = request.call_args_list[1].kwargs["body"]["Device"]
    if duration is None:
        assert "Operation" not in settings
    else:
        assert settings["Operation"] == {"operationTime": cook.format_hms(duration)}


@pytest.mark.parametrize("budget", [7.0, 8.0, 10.0, 30.0])
def test_confirmation_and_clear_share_the_whole_start_deadline(monkeypatch, budget):
    now = 0.0
    timeouts = []
    responses = iter(_responses())

    def sleep(seconds):
        nonlocal now
        now += seconds

    def request(_method, _path, **kwargs):
        timeouts.append(kwargs["timeout"])
        if kwargs["timeout"] < 1:
            sleep(kwargs["timeout"])
            raise TimeoutError("the request budget is spent")
        sleep(1)
        return next(responses)

    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.time",
        SimpleNamespace(monotonic=lambda: now, sleep=sleep),
    )
    transport = _transport()
    monkeypatch.setattr(transport, "_request", request)

    if budget < 8.5:
        with pytest.raises(UntimedStartUnconfirmed, match="budget is spent"):
            transport.write(["device", "1"], _batch(), timeout=budget)
        assert now == budget
    else:
        assert transport.write(["device", "1"], _batch(), timeout=budget) == (0x44, None)
        assert timeouts == [budget - elapsed for elapsed in (0, 1, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5)]
    assert all(timeout > 0 for timeout in timeouts)
