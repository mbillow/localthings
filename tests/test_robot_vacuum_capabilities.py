"""POWERbot R7040 robot vacuum (issue #659): routing, coverage, and the
vacuum entity's /mode/vs/0 token mapping."""

import copy
from typing import ClassVar, cast

import pytest
from homeassistant.components.vacuum import VacuumActivity, VacuumEntityFeature
from homeassistant.exceptions import ServiceValidationError

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import robot_vacuum as rv
from custom_components.localthings.registry.capabilities.common import merge_options_field
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import (
    SelectDesc,
    SwitchDesc,
    VacuumDesc,
)
from custom_components.localthings.vacuum import LocalThingsVacuum
from tests.conftest import _load_device

FIXTURE = "robot_vacuum_powerbot_r7040"
DEVICE_TYPES = ("oic.wk.d", "oic.d.robotcleaner")
MODES = "x.com.samsung.da.modes"


def _resources():
    return _load_device(FIXTURE)


def _bind(resources):
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None
    unbound = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return reg, bound, unbound


def _with_modes(resources, *tokens):
    """Replace the current token of each group named in `tokens`."""
    resources = copy.deepcopy(resources)
    rep = resources[rv.HREF_MODE]
    rep[MODES] = merge_options_field(rep[MODES], list(tokens))
    return resources


class _FakeCoordinator:
    device_key = "TEST-ROBOT-VACUUM"
    device_info: ClassVar[dict] = {}
    data: ClassVar[dict] = {}

    def __init__(self, resources):
        self.last_resources = resources
        self.commands = []

    def resource(self, href):
        return self.last_resources.get(href, {})

    def canonical_resources(self, subdevice):
        return self.last_resources

    async def async_send_command(self, bound, payload):
        self.commands.append((bound, payload))


def _vacuum(resources):
    _, bound, _ = _bind(resources)
    vacuum_bound = next(b for b in bound if isinstance(b.desc, VacuumDesc))
    coordinator = _FakeCoordinator(resources)
    entity = LocalThingsVacuum(cast(LocalThingsCoordinator, coordinator), vacuum_bound)
    return entity, coordinator


def test_routes_by_oic_type_with_zero_unbound_hrefs():
    reg, bound, unbound = _bind(_resources())
    assert reg.name == "robot_vacuum"
    assert unbound == []
    state = flatten(bound, _resources())
    assert state["vacuum"] == "docked"
    assert state["robot_status"] == "charging"
    assert state["battery"] == 100
    assert state["battery_charging"] is True
    assert state["cleaning_mode"] == "Stop"
    assert state["repeated_cleaning"] is False
    # Access_UnLock: HA's lock class reads On as unlocked.
    assert state["child_lock"] is True


def test_vendor_battery_stands_in_without_the_ocf_resource():
    resources = _resources()
    del resources["/energy/battery/0"]
    resources["/energy/battery/vs/0"]["x.com.samsung.da.batteryCharge"] = "42"
    _, bound, unbound = _bind(resources)
    assert unbound == []
    assert flatten(bound, resources)["battery"] == 42


@pytest.mark.parametrize(
    "tokens, expected",
    [
        (("Control_Charging",), VacuumActivity.DOCKED),
        (("Control_Cleaning", "Cleaning_Auto"), VacuumActivity.CLEANING),
        (("Control_Point", "Cleaning_Part"), VacuumActivity.CLEANING),
        (("Control_Homing",), VacuumActivity.RETURNING),
        (("Control_Pause",), VacuumActivity.PAUSED),
        (("Control_Idle",), VacuumActivity.IDLE),
        (("Control_Alarm",), VacuumActivity.ERROR),
        # A Control_* value with no activity of its own isn't the robot
        # moving, even with a program set (a scheduled run).
        (("Control_Reserve", "Cleaning_Auto"), VacuumActivity.IDLE),
        (("Control_PowerOff", "Cleaning_Stop"), VacuumActivity.IDLE),
    ],
)
def test_activity_mapping(tokens, expected):
    entity, _ = _vacuum(_with_modes(_resources(), *tokens))
    assert entity.activity == expected


def test_program_alone_decides_without_a_control_token():
    rep = {MODES: ["Cleaning_Repeat"]}
    assert rv.activity(rep) == "cleaning"
    assert rv.activity({MODES: ["Cleaning_Stop"]}) is None


def test_optimistic_body_assumes_the_control_state_a_program_leads_to():
    desc = next(b.desc for b in _bind(_resources())[1] if isinstance(b.desc, VacuumDesc))
    assert desc.optimistic_fn is not None
    assert desc.optimistic_fn({MODES: ["Cleaning_Auto"]}) == {
        MODES: ["Cleaning_Auto", "Control_Cleaning"]
    }
    assert desc.optimistic_fn({MODES: ["Cleaning_Stop"]}) == {
        MODES: ["Cleaning_Stop", "Control_Idle"]
    }
    assert desc.optimistic_fn({MODES: ["Turbo_On"]}) == {MODES: ["Turbo_On"]}


def test_features_follow_supported_modes():
    entity, _ = _vacuum(_resources())
    features = entity.supported_features
    for feature in (
        VacuumEntityFeature.START,
        VacuumEntityFeature.STOP,
        VacuumEntityFeature.PAUSE,
        VacuumEntityFeature.RETURN_HOME,
        VacuumEntityFeature.CLEAN_SPOT,
        VacuumEntityFeature.FAN_SPEED,
        VacuumEntityFeature.STATE,
        VacuumEntityFeature.SEND_COMMAND,
    ):
        assert feature in features, feature
    assert VacuumEntityFeature.LOCATE not in features
    assert entity.fan_speed == "normal"
    assert entity.fan_speed_list == ["turbo", "normal"]

    resources = _resources()
    supported = resources[rv.HREF_MODE]["x.com.samsung.da.supportedModes"]
    supported.remove("Control_Pause")
    supported[:] = [t for t in supported if not t.startswith("Turbo_")]
    entity, _ = _vacuum(resources)
    assert VacuumEntityFeature.PAUSE not in entity.supported_features
    assert VacuumEntityFeature.FAN_SPEED not in entity.supported_features


async def test_commands_send_one_token():
    entity, coordinator = _vacuum(_resources())
    await entity.async_start()
    await entity.async_stop()
    await entity.async_pause()
    await entity.async_return_to_base()
    await entity.async_clean_spot()
    await entity.async_set_fan_speed("turbo")
    await entity.async_send_command("Cleaning_Repeat")
    payloads = [payload for _, payload in coordinator.commands]
    assert payloads == [
        ("Cleaning", "Auto"),
        ("Cleaning", "Stop"),
        ("Control", "Pause"),
        ("Control", "Homing"),
        ("Cleaning", "Part"),
        ("Turbo", "On"),
        ("Cleaning", "Repeat"),
    ]
    desc = coordinator.commands[0][0].desc
    assert desc.write_fn(("Control", "Homing"), _resources()[rv.HREF_MODE]) == (
        ["mode", "vs", "0"],
        {MODES: ["Control_Homing"]},
    )


async def test_start_from_pause_resumes():
    entity, coordinator = _vacuum(_with_modes(_resources(), "Control_Pause", "Cleaning_Auto"))
    await entity.async_start()
    assert coordinator.commands[-1][1] == ("Control", "Cleaning")


async def test_unsupported_values_are_refused():
    entity, coordinator = _vacuum(_resources())
    with pytest.raises(ServiceValidationError):
        await entity.async_set_fan_speed("quiet")
    with pytest.raises(ServiceValidationError):
        await entity.async_send_command("Control_Selfdestruct")
    with pytest.raises(ServiceValidationError):
        await entity.async_send_command("Cleaning_Repeat", {"times": 2})
    assert coordinator.commands == []
    desc = next(b.desc for b in _bind(_resources())[1] if isinstance(b.desc, VacuumDesc))
    assert desc.write_fn(("Turbo", "Silence"), _resources()[rv.HREF_MODE]) is None


def test_selects_and_switch_write_their_group():
    resources = _resources()
    _, bound, _ = _bind(resources)
    by_key = {b.desc.key: b.desc for b in bound}
    rep = resources[rv.HREF_MODE]

    cleaning = cast(SelectDesc, by_key["cleaning_mode"])
    assert callable(cleaning.options)
    assert cleaning.options(resources) == ["Auto", "Part", "Repeat", "Manual", "Stop"]
    write = cleaning.write_fn
    assert write is not None
    assert write("Repeat", rep) == (["mode", "vs", "0"], {MODES: ["Cleaning_Repeat"]})

    write = cast(SelectDesc, by_key["robot_sound"]).write_fn
    assert write is not None
    assert write("Mute", rep) == (["mode", "vs", "0"], {MODES: ["Sound_Mute"]})
    assert write("Voice", rep) is None

    write = cast(SwitchDesc, by_key["repeated_cleaning"]).write_fn
    assert write is not None
    assert write("On", rep) == (["mode", "vs", "0"], {MODES: ["RepeatedCleaning_On"]})
    assert all(by_key[k].merges_modes for k in ("cleaning_mode", "robot_sound", "vacuum"))
