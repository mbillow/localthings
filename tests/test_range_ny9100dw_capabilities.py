"""NY9100DW/AA (TP1X_DA-KS-RANGE-0102X, issue #767): no Fuel_ token, but lighting
each burner alone produced the bit map verified on the gas NX60T8311SS."""

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

DEVICE_TYPES = ("oic.wk.d", "oic.d.range")

_OPTIONS = [
    "DeviceType_NY9100DW/AA0",
    "SettingPossible_7",
    "TimeAutoSync_On",
    "UpperLamp_Off",
    "TimeSystem_24",
    "Sound_On",
    "AdjustingTemp_0",
    "Sabbath_Off",
    "EnergySaving_On",
    "BurnerOnAlter_On",
]


def _resources(mask, options=None):
    return {
        "/mode/vs/0": {
            "x.com.samsung.da.supportedModes": ["UpperBroil", "UpperConvectionBake"],
            "x.com.samsung.da.options": _OPTIONS if options is None else options,
        },
        "/cooktopmonitoring/vs/0": {
            "x.com.samsung.da.supportedCooktopRunningState": ["Run", "Ready"],
            "x.com.samsung.da.cooktopMonitoring": mask,
            "x.com.samsung.da.cooktopRunningState": "Ready" if mask == "0" else "Run",
        },
    }


def _state(resources):
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.name == "range"
    return flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)


@pytest.mark.parametrize(
    ("mask", "burner"),
    [
        ("1", 1),  # front-left
        ("2", 2),  # back-left
        ("4", 3),  # center
        ("8", 4),  # back-right
        ("16", 5),  # front-right
    ],
)
def test_each_burner_from_the_issue_decodes(mask, burner):
    state = _state(_resources(mask))
    assert "cooktop_monitoring" not in state
    assert state["active_burners"] == 1
    for n in range(1, 6):
        assert state[f"cooktop_burner_{n}"] is (n == burner)


def test_all_burners_off_reads_zero():
    state = _state(_resources("0"))
    assert state["active_burners"] == 0
    assert not any(state[f"cooktop_burner_{n}"] for n in range(1, 6))


def test_ny_board_reporting_fuel_electric_stays_raw():
    """The NY prefix only fills in for a missing token; it never overrides one."""
    state = _state(_resources("1", [*_OPTIONS, "Fuel_Electric"]))
    assert state["cooktop_monitoring"] == 1
    assert "active_burners" not in state
    assert not any(k.startswith("cooktop_burner_") for k in state)


def test_electric_ne8300d_with_burner_on_alter_stays_raw():
    """NE8300D carries BurnerOnAlter_On too, so that option can't mark a board as gas."""
    resources = _load_device("range_ne8300d")
    assert "BurnerOnAlter_On" in resources["/mode/vs/0"]["x.com.samsung.da.options"]
    state = _state(resources)
    assert state["cooktop_monitoring"] == 0
    assert "active_burners" not in state
    assert not any(k.startswith("cooktop_burner_") for k in state)
