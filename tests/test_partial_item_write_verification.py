"""Partial item writes must verify against complete device representations."""

from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import CONF_HOST, DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator, _payload_present_in
from custom_components.localthings.registry.capabilities.operational import (
    OPERATIONAL_STATE,
    STOP_BUTTON,
)
from custom_components.localthings.registry.discovery import BoundEntity

ITEMS = "x.com.samsung.da.items"
ID = "x.com.samsung.da.id"
DESIRED = "x.com.samsung.da.desired"
UNIT = "x.com.samsung.da.unit"
CURRENT = "x.com.samsung.da.current"
STATE = "x.com.samsung.da.state"
OPTIONS = "x.com.samsung.da.options"
TEMPERATURE_HREF = "/temperatures/vs/0"
STATE_HREF = "/operational/state/vs/0"
REQUESTED = {ID: "0", DESIRED: "350", UNIT: "Fahrenheit"}
RETURNED = {**REQUESTED, DESIRED: 350.0, CURRENT: "80", "rt": ["temperature"]}
SIBLING = {ID: "1", DESIRED: "0", UNIT: "Fahrenheit", CURRENT: "75"}


@pytest.mark.parametrize("batch", [False, True])
def test_partial_items_match_by_id_without_mutating_inputs(batch):
    payload = {ITEMS: [REQUESTED, {ID: "1", DESIRED: "0"}]}
    readback = {ITEMS: [SIBLING, {ID: "2", CURRENT: "70"}, RETURNED]}
    if batch:
        payload = [{"href": TEMPERATURE_HREF, "rep": payload}]
        readback = {TEMPERATURE_HREF: readback}
    original = deepcopy((payload, readback))

    assert _payload_present_in(payload, readback)
    assert (payload, readback) == original


@pytest.mark.parametrize(
    "items",
    [
        [],
        [SIBLING],
        [{**RETURNED, ID: "00"}],
        [{**RETURNED, ID: 0}],
        [{**RETURNED, ID: ""}],
        [{DESIRED: "350", UNIT: "Fahrenheit"}],
        [{ID: "0", UNIT: "Fahrenheit"}],
        [{ID: "0", DESIRED: "350"}],
        [{**RETURNED, UNIT: "Celsius"}],
        [{**RETURNED, UNIT: "fahrenheit"}],
        [{**RETURNED, DESIRED: "325"}],
        [RETURNED, RETURNED],
        [RETURNED, SIBLING, SIBLING],
        [RETURNED, {CURRENT: "75"}],
        [RETURNED, "malformed"],
    ],
)
def test_incomplete_mismatched_or_ambiguous_readback_is_not_confirmed(items):
    assert not _payload_present_in({ITEMS: [REQUESTED]}, {ITEMS: items})


@pytest.mark.parametrize(
    "items",
    [[REQUESTED, REQUESTED], [{DESIRED: "350"}], [{ID: 0}], [{ID: ""}], [None]],
)
def test_ambiguous_request_is_not_confirmed_even_by_an_identical_reply(items):
    assert not _payload_present_in({ITEMS: items}, {ITEMS: deepcopy(items)})


@pytest.mark.parametrize(
    ("written", "current", "expected"),
    [
        ("350", 350, True),
        (350, "350.0", True),
        (350.0, "350", True),
        ("350.00", "350", True),
        ("175.5", 175.5, True),
        ("350", 350.01, False),
        ("9007199254740993", "9007199254740992", False),
        (True, 1, False),
        (1, True, False),
        ("NaN", "NaN", False),
        (float("inf"), "Infinity", False),
        ("-Infinity", float("-inf"), False),
        ("350F", "350F", False),
        (None, None, False),
    ],
)
def test_only_finite_numeric_setpoints_are_equivalent(written, current, expected):
    assert (
        _payload_present_in(
            {ITEMS: [{ID: "0", DESIRED: written}]},
            {ITEMS: [{ID: "0", DESIRED: current}]},
        )
        is expected
    )


def test_empty_item_write_requires_an_empty_readback():
    assert _payload_present_in({ITEMS: []}, {ITEMS: []})
    assert not _payload_present_in({ITEMS: []}, {ITEMS: [RETURNED]})


def test_missing_field_is_not_an_explicit_null():
    assert not _payload_present_in({"field": None}, {})
    assert _payload_present_in({"field": None}, {"field": None})


def test_other_lists_and_nested_fields_still_compare_whole():
    assert not _payload_present_in(
        {ITEMS: [{ID: "0", "metadata": {"a": 1}}]},
        {ITEMS: [{ID: "0", "metadata": {"a": 1, "b": 2}}]},
    )
    assert not _payload_present_in(
        {"x.com.samsung.da.modes": ["Bake"]},
        {"x.com.samsung.da.modes": ["Bake", "Broil"]},
    )


def test_batch_verification_uses_the_named_resource_and_checks_every_field():
    payload = [
        {"href": "/devices/0"},
        {"href": TEMPERATURE_HREF, "rep": {ITEMS: [REQUESTED]}},
        {"href": STATE_HREF, "rep": {STATE: "Ready"}},
    ]
    assert not _payload_present_in(
        payload, {"/temperatures/vs/1": {ITEMS: [RETURNED]}, STATE_HREF: {STATE: "Ready"}}
    )
    assert not _payload_present_in(
        payload, {TEMPERATURE_HREF: {ITEMS: [RETURNED]}, STATE_HREF: {STATE: "Run"}}
    )


@pytest.mark.parametrize("batch", [False, True])
def test_option_token_matching_is_consistent_for_property_and_batch_writes(batch):
    payload = {OPTIONS: ["Sound_On"]}
    readback = {OPTIONS: ["Lamp_Off", "Sound_On"]}
    if batch:
        payload = [{"href": "/mode/vs/0", "rep": payload}]
        readback = {"/mode/vs/0": readback}
    assert _payload_present_in(payload, readback)


@pytest.fixture
def coordinator(hass, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "192.0.2.1"})
    entry.add_to_hass(hass)
    coord = LocalThingsCoordinator(hass, entry)
    coord.async_request_refresh = AsyncMock()
    monkeypatch.setattr("custom_components.localthings.coordinator.asyncio.sleep", AsyncMock())
    return coord


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("later_temperature", [350, 325])
async def test_raw_write_reports_full_item_readback_and_later_reversion(
    coordinator, batch, later_temperature
):
    payload = {ITEMS: [REQUESTED]}
    immediate = {ITEMS: [SIBLING, RETURNED]}
    later = {ITEMS: [{**RETURNED, DESIRED: later_temperature}, SIBLING]}
    href = TEMPERATURE_HREF
    if batch:
        payload = [
            {"href": TEMPERATURE_HREF, "rep": payload},
            {"href": STATE_HREF, "rep": {STATE: "Ready"}},
        ]
        immediate = [
            {"href": TEMPERATURE_HREF, "rep": immediate},
            {"href": STATE_HREF, "rep": {STATE: "Ready"}},
        ]
        later = [
            {"href": TEMPERATURE_HREF, "rep": later},
            {"href": STATE_HREF, "rep": {STATE: "Ready"}},
        ]
        href = "/device/0"
    session = Mock()
    session.write.return_value = (0x44, None)
    session.read.side_effect = [(0x45, immediate), (0x45, later)]
    coordinator._session = session

    result = await coordinator.async_raw_write_sequence(
        [{"href": href, "payload": payload}], verify_after=1
    )

    assert result["results"][0]["accepted"] is True
    assert result["results"][0]["changed"] is True
    verified = result["verified"][href]
    assert verified["held"] is (later_temperature == 350)
    if batch:
        assert verified["elements"] == {
            TEMPERATURE_HREF: later_temperature == 350,
            STATE_HREF: True,
        }
    session.write.assert_called_once_with(
        href.strip("/").split("/"), payload, timeout=coordinator._POST_TIMEOUT_S
    )


@pytest.mark.parametrize("desired", [350, 325])
async def test_command_confirmation_accepts_only_the_requested_item_value(coordinator, desired):
    coordinator._session = Mock()
    coordinator._session.read.return_value = (
        0x45,
        {ITEMS: [SIBLING, {**RETURNED, DESIRED: desired}]},
    )
    assert (await coordinator._took_effect(["temperatures", "vs", "0"], {ITEMS: [REQUESTED]})) is (
        desired == 350
    )


@pytest.mark.parametrize("state", ["Ready", "Run", None])
async def test_stop_still_requires_the_requested_state_with_remote_control_off(coordinator, state):
    coordinator._cache.apply_rep(
        "/remotectrl/vs/0", {"x.com.samsung.da.remoteControlEnabled": "false"}, source="test"
    )
    session = Mock()
    session.write.return_value = (0x44, None)
    session.read.return_value = (
        0x45,
        {STATE: state, "x.com.samsung.da.progress": "None"} if state else {},
    )
    coordinator._session = session
    bound = BoundEntity(href=STATE_HREF, capability=OPERATIONAL_STATE, desc=STOP_BUTTON)

    if state == "Ready":
        await coordinator.async_send_command(bound, STOP_BUTTON.payload)
    else:
        with pytest.raises(HomeAssistantError) as error:
            await coordinator.async_send_command(bound, STOP_BUTTON.payload)
        assert error.value.translation_key == "command_not_confirmed"
    session.write.assert_called_once_with(
        ["operational", "state", "vs", "0"],
        {STATE: "Ready"},
        timeout=coordinator._POST_TIMEOUT_S,
    )
    coordinator.async_request_refresh.assert_awaited_once()
