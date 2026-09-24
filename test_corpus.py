"""
Washer Corpus Test Script
Deep-scans washer fixture files for capability matrices across all payload paths,
supporting Table_00/01 positional sets, Table_02/03 bitmask matrices, and legacy devices.
"""

import json
import pathlib
import pprint
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.resolve()))

from custom_components.localthings.helpers.washer_matrix import (
    parse_supported_options_matrix,
    parse_table00_options_matrix,
)
from custom_components.localthings.registry.batch import parse_device0_batch


def extract_supported_options(resources: dict):
    """Search extracted resources for supportedOptions."""
    for res_data in resources.values():
        if isinstance(res_data, dict):
            for key, value in res_data.items():
                if (
                    key in ("x.com.samsung.da.supportedOptions", "supportedOptions")
                    and isinstance(value, list)
                    and value
                ):
                    return value
    return None


def get_resources_dict(data: dict) -> dict:
    """Normalize fixture data into a flat href-to-resource dictionary."""
    if "device0" in data:
        return parse_device0_batch(data["device0"])
    return data.get("resources", {})


def extract_options_array(resources: dict) -> list:
    """Extract x.com.samsung.da.options from course vs/0 endpoint or alternative paths."""
    course_res = resources.get("/course/vs/0") or {}
    opts = course_res.get("x.com.samsung.da.options")
    if opts:
        return opts
    for res_data in resources.values():
        if isinstance(res_data, dict) and "x.com.samsung.da.options" in res_data:
            return res_data["x.com.samsung.da.options"]
    return []


def determine_device_type(resources: dict) -> str:
    """Determine table classification (Table_00 to Table_03 or LEGACY)."""
    washercourse_res = resources.get("/st/washercourse/vs/0") or {}
    table_type = washercourse_res.get("x.com.samsung.da.st.courseTable")
    normalized_table = str(table_type).strip() if table_type else None

    supported_tables = {"Table_00", "Table_01", "Table_02", "Table_03"}
    if normalized_table in supported_tables:
        return normalized_table

    return "LEGACY"


def run_corpus_tests():
    test_dir = pathlib.Path("tests")
    washer_files = sorted([
        f
        for f in test_dir.rglob("*.json")
        if (
            ("washer" in f.name.lower() or "dishwasher" in f.name.lower())
            and "golden" not in f.parts
        )
    ])

    print(f"\nFound {len(washer_files)} washer diagnostic fixture(s).\n")

    passed_count = 0
    skipped_count = 0
    failed_count = 0

    for file_path in washer_files:
        print("=" * 60)
        print(f"Testing File: {file_path}")

        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        resources = get_resources_dict(data)
        device_type = determine_device_type(resources)
        print(f"Device Architecture: {device_type}")
        print("=" * 60)

        opts = extract_options_array(resources)
        raw_supported = extract_supported_options(resources)

        try:
            matrix = {}
            if device_type in ("Table_00", "Table_01"):
                matrix = parse_table00_options_matrix(resources, opts)
            elif device_type in ("Table_02", "Table_03"):
                if not raw_supported:
                    print(
                        "⚠️  No 'supportedOptions' payload present in this "
                        "Table_02/03 fixture. Skipping."
                    )
                    skipped_count += 1
                    continue

                washer_res = resources.get("/washer/vs/0", {})
                global_lists = {
                    "supportedWaterTemperature": washer_res.get(
                        "x.com.samsung.da.supportedWaterTemperature", []
                    ),
                    "supportedRinseCycles": washer_res.get(
                        "x.com.samsung.da.supportedRinseCycles", []
                    ),
                    "supportedSpinLevel": washer_res.get(
                        "x.com.samsung.da.supportedSpinLevel", []
                    ),
                    "supportedDryLevel": washer_res.get(
                        "x.com.samsung.da.supportedDryLevel", []
                    ),
                    "supportedSoilLevel": washer_res.get(
                        "x.com.samsung.da.supportedSoilLevel", []
                    ),
                }

                course_table = (
                    resources.get("/st/washercourse/vs/0", {})
                    .get("x.com.samsung.da.st.courseTable")
                )
                edit_course_list = (
                    resources.get("/wm/editcourse/vs/0", {})
                    .get("x.com.samsung.da.editCourseList")
                )

                matrix = parse_supported_options_matrix(
                    raw_supported,
                    global_lists=global_lists,
                    course_table=course_table,
                    edit_course_list=edit_course_list,
                    resources=resources,
                    opts=opts,
                )
            else:
                print(
                    "ℹ️  LEGACY architecture. Using standard integration defaults "   # noqa: RUF001
                    "(no bitmask/positional decoding)."
                )
                passed_count += 1
                continue

            print(f"✅ SUCCESS! Decoded {len(matrix)} course blocks.")
            print(f"   Supported Course Hex IDs: {list(matrix.keys())}\n")

            if matrix:
                print("   --- Option Support Summary Across All Courses ---")
                print(
                    "   "
                    f"{'Course':<8} | {'Bubble Soak':<12} | "
                    f"{'Pre-Wash':<10} | {'Intensive':<10}"
                )
                print("   " + "-" * 52)
                for course_hex, block in matrix.items():
                    bs = block.get("bubble_soak_supported")
                    pw = block.get("pre_wash_supported")
                    iw = block.get("intensive_wash_supported")
                    print(f"   {course_hex:<8} | {bs!s:<12} | {pw!s:<10} | {iw!s:<10}")

                print("\n   --- Sample Block (1B / Cotton or First Course) ---")
                sample_hex = "1B" if "1B" in matrix else next(iter(matrix))
                print(f"   Detailed View ({sample_hex}):")
                pprint.pprint(matrix[sample_hex], indent=6)

            passed_count += 1

        except Exception as e:
            print(f"❌ FAILED to decode: {e}")
            failed_count += 1

    print("\n" + "=" * 60)
    print(
        "Summary: "
        f"{passed_count} passed, {failed_count} failed, "
        f"{skipped_count} skipped without bitmask."
    )
    print("=" * 60)


if __name__ == "__main__":
    run_corpus_tests()
