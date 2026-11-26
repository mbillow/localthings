"""confirm_write: a write the appliance acknowledged is read back before it is accepted."""

from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import CONF_HOST, DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator, _payload_present_in
from custom_components.localthings.registry.capabilities import washer
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import NumberDesc, SelectDesc
from custom_components.localthings.transport import Transport
from tests.conftest import _load_device

HREF = "/course/vs/0"
PATH = ["course", "vs", "0"]
OPTIONS = "x.com.samsung.da.options"
REMOTE = "/remotectrl/vs/0"
REMOTE_FIELD = "x.com.samsung.da.remoteControlEnabled"
DOSING_KEYS = (
    "detergent_quantity",
    "detergent_water_hardness",
    "softener_quantity",
    "softener_concentration",
)
# Every captured washer that reports the dosing tokens.
DOSING_FIXTURES = (
    "washer_ww90dg6u25le",
    "washer_wa55a7700av",
    "washer_wf80h",
    "washer_dryer_awm_m64",
    "washer_dryer_awm_m64_25",
)


def _course(level: str) -> dict:
    tokens = ["Course_1C", f"DetergentLevelCtrl_{level}", "SupportedDetergentLevelCtrl_00010203"]
    return {OPTIONS: tokens}


def _dose(key: str) -> SelectDesc:
    desc = next(e for e in washer.WASHER_COURSE.entities if e.key == key)
    assert isinstance(desc, SelectDesc)
    return desc


def _bound(desc: SelectDesc | None = None) -> BoundEntity:
    desc = desc or _dose("detergent_quantity")
    return BoundEntity(href=HREF, capability=washer.WASHER_COURSE, desc=desc)


def _delays(sleep: AsyncMock) -> list[float]:
    return [c.args[0] for c in sleep.await_args_list if c.args and c.args[0]]


@pytest.fixture
def dose_coordinator(hass, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "192.0.2.1"})
    entry.add_to_hass(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    coordinator._cache.apply_rep(REMOTE, {REMOTE_FIELD: "true"}, source="test")
    coordinator._observe.apply(HREF, _course("3"), source="poll")
    session = MagicMock()
    session.write.return_value = (0x44, None)
    coordinator._session = cast(Transport, session)
    coordinator.async_request_refresh = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr("custom_components.localthings.coordinator.asyncio.sleep", sleep)
    return coordinator, session, sleep


def test_only_the_dosing_selects_opt_in() -> None:
    assert {e.key for e in washer.WASHER_COURSE.entities if e.confirm_write} == set(DOSING_KEYS)


@pytest.mark.parametrize("dump", DOSING_FIXTURES)
@pytest.mark.parametrize("key", DOSING_KEYS)
def test_a_dose_the_washer_reports_matches_the_write_that_sets_it(dump, key) -> None:
    # Each washer reports the level un-padded ('2'), as _level_write sends it,
    # so the confirming read can't miss a dose that did land.
    rep = _load_device(dump)[HREF]
    desc = _dose(key)
    assert desc.rep_fn is not None
    assert desc.write_fn is not None
    code = desc.rep_fn(rep)
    assert code in desc.options({HREF: rep})
    result = desc.write_fn(code, rep)
    assert result is not None
    path, body = result
    assert path == PATH
    assert _payload_present_in(body, rep)


async def test_a_dose_the_washer_shows_is_accepted(dose_coordinator) -> None:
    coordinator, session, sleep = dose_coordinator
    session.read.return_value = (0x45, _course("1"))
    assert await coordinator.async_send_command(_bound(), "01") is True
    session.write.assert_called_once_with(
        PATH, {OPTIONS: ["DetergentLevelCtrl_1"]}, timeout=coordinator._POST_TIMEOUT_S
    )
    session.read.assert_called_once_with(PATH, timeout=coordinator._POST_TIMEOUT_S)
    assert _delays(sleep) == [coordinator._CONFIRM_DELAY_S]
    assert coordinator.write_settling(HREF)


async def test_a_dose_the_washer_shows_late_is_accepted(dose_coordinator) -> None:
    coordinator, session, sleep = dose_coordinator
    session.read.side_effect = [(0x45, _course("3")), (0x45, _course("1"))]
    assert await coordinator.async_send_command(_bound(), "01") is True
    assert session.read.call_count == 2
    assert _delays(sleep) == [coordinator._CONFIRM_DELAY_S, coordinator._CONFIRM_RETRY_S]
    assert coordinator.write_settling(HREF)
    assert "DetergentLevelCtrl_1" in coordinator.resource(HREF)[OPTIONS]


async def test_a_dose_the_washer_dropped_is_reported_and_released(dose_coordinator) -> None:
    coordinator, session, sleep = dose_coordinator
    session.read.return_value = (0x45, _course("3"))
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_send_command(_bound(), "01")
    assert err.value.translation_key == "command_dropped"
    assert session.read.call_count == 2
    assert _delays(sleep) == [coordinator._CONFIRM_DELAY_S, coordinator._CONFIRM_RETRY_S]
    # The next poll may replace the optimistic dose, and one is requested now.
    assert not coordinator.write_settling(HREF)
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.parametrize(
    "reads",
    [
        [TimeoutError()],
        [(0x84, None)],
        [(0x45, None)],
        [(0x45, _course("3")), TimeoutError()],
    ],
)
async def test_a_failed_confirming_read_does_not_fail_the_dose(dose_coordinator, reads) -> None:
    # Non-strict, as for a write sent with Remote Control off: a read that
    # fails proves nothing, so the acknowledged write stands.
    coordinator, session, _sleep = dose_coordinator
    session.read.side_effect = reads
    assert await coordinator.async_send_command(_bound(), "01") is True
    assert session.read.call_count == len(reads)
    assert coordinator.write_settling(HREF)


async def test_a_write_without_the_flag_is_not_read_back(dose_coordinator) -> None:
    coordinator, session, sleep = dose_coordinator
    desc = replace(_dose("detergent_quantity"), confirm_write=False)
    assert await coordinator.async_send_command(_bound(desc), "01") is True
    session.write.assert_called_once()
    session.read.assert_not_called()
    sleep.assert_not_awaited()


async def test_a_waived_remote_control_write_keeps_its_single_read(dose_coordinator) -> None:
    coordinator, session, sleep = dose_coordinator
    coordinator._cache.apply_rep(REMOTE, {REMOTE_FIELD: "false"}, source="test")
    href = "/example/vs/0"
    desc = NumberDesc(
        key="example",
        field="value",
        needs_remote_control=False,
        write_fn=lambda payload, rep, href=None, resources=None: (
            ["example", "vs", "0"],
            {"value": payload},
        ),
    )
    session.read.return_value = (0x45, {"value": 1})
    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_send_command(
            BoundEntity(href=href, capability=Capability(href=href), desc=desc), 7
        )
    assert err.value.translation_key == "command_not_confirmed"
    session.read.assert_called_once()
    assert _delays(sleep) == [coordinator._CONFIRM_DELAY_S]
