"""Test suite for washer supported options matrix parsing across corpus fixtures."""

import json
from pathlib import Path

import pytest

from custom_components.localthings.helpers.washer_matrix import (
    parse_supported_options_matrix,
)


def get_washer_fixtures() -> list[Path]:
    """Return all washer fixture file paths, excluding golden files."""
    test_dir = Path("tests/fixtures")
    return sorted(
        [
            f
            for f in test_dir.rglob("*.json")
            if ("washer" in f.name.lower() or "dishwasher" in f.name.lower())
            and "golden" not in f.parts
        ]
    )


def extract_resource_by_href(data: dict | list, target_href: str) -> dict:
    """Safely extract a resource payload by href regardless of list or dict structure."""
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("href") == target_href:
                return item
        return {}

    if isinstance(data, dict):
        # Dict lookup: either raw href key or inside 'resources' / 'device0'
        if target_href in data:
            return data[target_href]

        resources = data.get("resources", {})
        if isinstance(resources, dict) and target_href in resources:
            return resources[target_href]
        if isinstance(resources, list):
            return extract_resource_by_href(resources, target_href)

        if "device0" in data and isinstance(data["device0"], dict):
            payload = data["device0"].get("payload", {})
            return extract_resource_by_href(payload, target_href)

    return {}


@pytest.mark.parametrize("fixture_path", get_washer_fixtures())
def test_washer_supported_options_corpus(fixture_path: Path) -> None:
    """Ensure every washer fixture in the regression corpus parses cleanly."""
    with open(fixture_path, encoding="utf-8") as f:
        data = json.load(f)

    def extract_supported_options(d: dict | list) -> list[str] | str | None:
        if isinstance(d, dict):
            for k, v in d.items():
                if k in ("x.com.samsung.da.supportedOptions", "supportedOptions") and v:
                    return v
                res = extract_supported_options(v)
                if res:
                    return res
        elif isinstance(d, list):
            for item in d:
                res = extract_supported_options(item)
                if res:
                    return res
        return None

    raw_supported = extract_supported_options(data)
    assert raw_supported is not None, f"No supportedOptions in {fixture_path}"

    washercourse = extract_resource_by_href(data, "/st/washercourse/vs/0")
    editcourse = extract_resource_by_href(data, "/wm/editcourse/vs/0")
    washer = extract_resource_by_href(data, "/washer/vs/0")

    course_table = washercourse.get("x.com.samsung.da.st.courseTable")
    edit_course_list = editcourse.get("x.com.samsung.da.editCourseList")

    global_lists = {
        "supportedSoilLevel": washer.get("x.com.samsung.da.supportedSoilLevel", []),
        "supportedWaterTemperature": washer.get("x.com.samsung.da.supportedWaterTemperature", []),
        "supportedSpinLevel": washer.get("x.com.samsung.da.supportedSpinLevel", []),
        "supportedRinseCycles": washer.get("x.com.samsung.da.supportedRinseCycles", []),
        "supportedDryLevel": washer.get("x.com.samsung.da.supportedDryLevel", []),
    }

    matrix = parse_supported_options_matrix(
        raw_supported,
        global_lists=global_lists,
        course_table=course_table or "Table_02",
        edit_course_list=edit_course_list,
    )

    # Matrix must contain decoded course blocks
    assert len(matrix) > 0
