"""QuickDrive contracts shared by washers and dryers."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import dryer, washer
from custom_components.localthings.registry.discovery import discover

_CAPTURE = json.loads(
    (Path(__file__).parent / "fixtures/laundry_quick_drive.json").read_text(encoding="utf-8")
)
_OPTIONS = "x.com.samsung.da.options"
_TABLE = "x.com.samsung.da.supportedOptions"
_HREF = "/course/vs/0"


@pytest.fixture(params=["washer", "dryer"])
def appliance(request):
    family = request.param
    cap = washer.WASHER_COURSE if family == "washer" else dryer.DRYER_COURSE
    desc = next(entity for entity in cap.entities if entity.key == "quick_drive")
    return family, desc, {_HREF: deepcopy(_CAPTURE[family])}


def _on_course(resources, code):
    rep = resources[_HREF]
    rep[_OPTIONS] = [
        f"Course_{code}" if token.startswith("Course_") else token for token in rep[_OPTIONS]
    ]
    return rep


def test_discovery_exposes_reported_quick_drive_only(appliance):
    family, _, resources = appliance
    reg = resolve(resources, device_types=(f"oic.d.{family}",))
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    assert flatten(bound, resources)["quick_drive"] is False

    resources[_HREF][_OPTIONS] = [
        token for token in resources[_HREF][_OPTIONS] if not token.startswith("QuickDrive_")
    ]
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    assert "quick_drive" not in flatten(bound, resources)


@pytest.mark.parametrize("value", ["On", "Off"])
def test_partial_write_and_read_contract(appliance, value):
    _, desc, resources = appliance
    rep = resources[_HREF]
    path, body = desc.write_fn(value, rep)
    assert path == ["course", "vs", "0"]
    assert body == {_OPTIONS: [f"QuickDrive_{value}"]}
    assert desc.rep_fn(body) is (value == "On")
    assert desc.needs_remote_control is False


def test_availability_tracks_selected_course_not_edit_list_order(appliance):
    family, desc, resources = appliance
    supported, unsupported = ("1B", "1C") if family == "washer" else ("51", "53")
    resources["/wm/editcourse/vs/0"] = {
        "x.com.samsung.da.editCourseList": f"EditCourseList_{unsupported}{supported}",
    }
    for course, expected in ((supported, True), (unsupported, False)):
        rep = _on_course(resources, course)
        assert desc.extra_state_attributes_fn(rep, resources) == {"course_supported": expected}
        assert desc.validate_fn("On", rep, resources) == (
            None if expected else "quick_drive_unavailable_for_cycle"
        )
        assert desc.validate_fn("Off", rep, resources) is None


@pytest.mark.parametrize("table", [None, [], "", ["not-hex"], ["1"], ["151D3"]])
def test_unresolvable_table_does_not_guess_from_edit_list(appliance, table):
    family, desc, resources = appliance
    course = "1B" if family == "washer" else "51"
    rep = _on_course(resources, course)
    rep[_TABLE] = table
    resources["/wm/editcourse/vs/0"] = {
        "x.com.samsung.da.editCourseList": f"EditCourseList_{course}",
    }
    assert desc.extra_state_attributes_fn(rep, resources) == {"course_supported": None}
    assert desc.validate_fn("On", rep, resources) is None


@pytest.mark.parametrize("bitmap", [None, "F", "XX", "F000", ""])
def test_missing_or_malformed_bitmap_is_unknown(appliance, bitmap):
    _, desc, resources = appliance
    rep = resources[_HREF]
    rep[_OPTIONS] = [token for token in rep[_OPTIONS] if not token.startswith("QuickDriveSet_")]
    if bitmap is not None:
        rep[_OPTIONS].append(f"QuickDriveSet_{bitmap}")
    assert desc.extra_state_attributes_fn(rep, resources) == {"course_supported": None}
    assert desc.validate_fn("On", rep, resources) is None


def test_unadvertised_course_has_unknown_availability(appliance):
    _, desc, resources = appliance
    rep = _on_course(resources, "FE")
    assert desc.extra_state_attributes_fn(rep, resources) == {"course_supported": None}
    assert desc.validate_fn("On", rep, resources) is None
