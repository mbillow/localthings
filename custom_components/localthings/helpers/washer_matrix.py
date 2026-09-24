"""Helper module to parse Samsung OCF washer cycle capability matrices.

Handles capability decoding for:
    - Table_02: Dynamic tokenized bitmask matrices on /course/vs/0 supportedOptions.
    - Table_00: Active-course positional hex-pair availability strings.
    - Hybrid Table_02: Dynamic fall-through from matrix lookups to positional hex sets
        when binary option tokens ('35', '36', '37') are omitted from supportedOptions.
    - Legacy / Unresolved: Fail-open standard option inspection fallbacks."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Standard master option lookup lists used as fallback when global list resources are unavailable
STANDARD_SOIL_LEVELS: list[str] = ["none", "extraLight", "light", "normal", "heavy", "extraHeavy"]
STANDARD_RINSE_CYCLES: list[str] = ["0", "1", "2", "3", "4", "5"]
STANDARD_SPIN_LEVELS: list[str] = ["rinseHold", "noSpin", "400", "800", "1000", "1200", "1400"]
STANDARD_WATER_TEMPS: list[str] = ["cold", "20", "30", "40", "60", "90"]
STANDARD_DRY_LEVELS: list[str] = ["none", "cupboard", "30", "60", "90", "120", "180", "240"]

# Specific lookup lists for older Table_00 / Table_01 washer generations
TABLE00_SPIN_LEVELS: list[str] = ["rinseHold", "noSpin", "low", "medium", "high", "extraHigh"]
TABLE00_WATER_TEMPS: list[str] = ["none", "tapCold", "cold", "warm", "hot", "extraHot"]

# OCF token prefixes identifying valid course payload blocks in Table_02/03 matrices.
# NOTE: '0' is strictly excluded from this tuple to prevent false-positive alignment slips
# on internal token values (e.g., '8000', '8102').
STRICT_TOKEN_PREFIXES: tuple[str, ...] = (
    "35", "36", "37", "5", "6", "7", "8", "9", "A", "B", "C", "D", "E", "F"
)

# Mapping internal switch keys to matrix dictionary flag identifiers
MATRIX_KEY_MAP: dict[str, str] = {
    "bubble_soak": "bubble_soak_supported",
    "pre_wash": "pre_wash_supported",
    "intensive": "intensive_wash_supported",
}


def _find_key_in_resources(resources: dict[str, Any], target_keys: tuple[str, ...]) -> Any:
    """Scan resource payloads regardless of leading/trailing slash variations in hrefs."""
    if not isinstance(resources, dict):
        return None

    for res in resources.values():
        if not isinstance(res, dict):
            continue
        for key in target_keys:
            val = res.get(key)
            if val:
                return val
    return None


def _extract_option_value(options: list[Any], key_prefix: str) -> str | None:
    """Extract string value for a given option key prefix from an options array.

    Example:
        `_extract_option_value(["BubbleSoakSet_F0F0..."], "BubbleSoakSet")`
        --> `"F0F0..."`
    """
    if not isinstance(options, list):
        return None
    target = f"{key_prefix}_"
    for item in options:
        if isinstance(item, str) and item.startswith(target):
            return item[len(target):]
    return None


def _extract_hex_pairs(hex_str: str) -> list[str]:
    """Split a continuous hex string into 2-character byte pairs.

    Example:
        `"F000F0"` --> `["F0", "00", "F0"]`
    """
    clean = hex_str.strip().upper()
    return [clean[i:i + 2] for i in range(0, len(clean), 2)]


def _extract_courses_from_supported_options(raw_supported: Any) -> list[str]:
    """Extract factory course sequence dynamically using discovered block strides.

    Extracts course hex codes (e.g., ['1C', '1B', '25', ...]) in exact firmware sequence
    to ensure positional sets (like BubbleSoakSet) align 1:1 with course indices.
    """
    while isinstance(raw_supported, list) and len(raw_supported) > 0:
        raw_supported = raw_supported[0]

    if not isinstance(raw_supported, str) or not raw_supported:
        return []

    raw_str = raw_supported.strip().upper()
    if len(raw_str) < 11:
        return []

    # Skip 1-char header metadata byte (e.g., '4', '3', '5')
    raw_payload = raw_str[1:]

    # Dynamically discover exact stride for this device payload
    block_size = _discover_course_block_stride(raw_payload) or 18
    courses: list[str] = []

    for i in range(0, len(raw_payload), block_size):
        segment = raw_payload[i : i + block_size]
        if len(segment) == block_size:
            course_code = segment[:2]
            if course_code not in courses:
                courses.append(course_code)

    return courses


def _extract_courses_list(resources: dict[str, Any], opts: list[Any]) -> list[str]:
    """Extract positional course order, prioritizing supportedOptions factory sequence."""
    resources = resources or {}
    opts = opts or []

    # 1. Primary: Extract course sequence directly from supportedOptions
    raw_supported = _find_key_in_resources(
        resources,
        ("x.com.samsung.da.supportedOptions", "supportedOptions"),
    )
    if raw_supported:
        courses_from_supported = _extract_courses_from_supported_options(raw_supported)
        if courses_from_supported:
            return courses_from_supported

    # 2. Secondary: editCourseList fallback
    raw_edit = _find_key_in_resources(
        resources,
        ("x.com.samsung.da.editCourseList", "editCourseList"),
    )
    if not raw_edit and isinstance(resources, dict):
        for res in resources.values():
            if isinstance(res, dict):
                found = _find_key_in_resources(
                    res,
                    ("x.com.samsung.da.editCourseList", "editCourseList"),
                )
                if found:
                    raw_edit = found
                    break

    if raw_edit:
        if isinstance(raw_edit, str):
            clean = raw_edit.replace("EditCourseList_", "").replace("Course_", "").strip().upper()
            if "," in clean:
                tokens = [t.strip() for t in clean.split(",") if t.strip()]
            else:
                tokens = [clean[i : i + 2] for i in range(0, len(clean), 2)]
            if tokens:
                return tokens
        elif isinstance(raw_edit, list):
            tokens = [
                str(t.get("value") if isinstance(t, dict) else t)
                .replace("EditCourseList_", "")
                .replace("Course_", "")
                .strip()
                .upper()
                for t in raw_edit
            ]
            tokens = [t for t in tokens if t]
            if tokens:
                return tokens

    # 3. Tertiary: courseTable dict fallback
    course_table = _find_key_in_resources(
        resources,
        ("x.com.samsung.da.st.courseTable", "courseTable"),
    )
    if isinstance(course_table, dict):
        tokens = [str(k).replace("Course_", "").strip().upper() for k in course_table]
        if tokens:
            return tokens

    # 4. Fallback: inspect local options array for Course_ entries
    return [
        item.replace("Course_", "").strip().upper()
        for item in opts
        if isinstance(item, str) and item.startswith("Course_")
    ]


def _discover_course_block_stride(raw_payload: str) -> int | None:
    """Discover course block stride dynamically starting from K=2.

    Evaluates both mathematical payload divisibility (L % block_size == 0) and
    100% token anchor validation across all blocks using STRICT_TOKEN_PREFIXES.

    Returns the exact stride integer if verified, or None if no valid washer
    tokenization pattern could be mathematically proven (e.g., dishwashers).
    """
    length = len(raw_payload)
    if length < 6:
        return None

    # Test candidate token counts K from 2 up to 8
    # Valid strides: 10 (K=2), 12 (K=2, 12-char), 14 (K=3), 18 (K=4), 20 (US top-loader), 22 (K=5),
    # etc.
    for k in range(2, 9):
        # Check standard 2+4K stride and special 20-char US top-loader stride
        for block_size in (2 + (k * 4), 20 if k == 4 else None):
            if not block_size or length % block_size != 0:
                continue

            valid_blocks = 0
            total_blocks = length // block_size

            for offset in range(0, length, block_size):
                chunk = raw_payload[offset : offset + block_size]
                # Slice into 4-char tokens after the 2-char course hex
                payload_tokens = [
                    chunk[2 + (t * 4) : 2 + (t * 4) + 4]
                    for t in range((block_size - 2) // 4)
                ]

                # Count how many tokens in this block start with recognized washer prefixes
                matching_tokens = sum(
                    1 for tok in payload_tokens if tok.startswith(STRICT_TOKEN_PREFIXES)
                )

                # Require at least 1 matching token for K=2 blocks, or 2 for K>=3 blocks
                required_matches = 1 if block_size <= 12 else 2
                if matching_tokens >= required_matches:
                    valid_blocks += 1

            # If 100% of blocks satisfy prefix validation, the true stride is discovered!
            if valid_blocks == total_blocks:
                _LOGGER.debug(
                    "Dynamically discovered washer stride: %d chars/course",
                    block_size,
                )
                return block_size

    # Could not resolve a valid washer tokenization stride
    return None


def _evaluate_positional_flag(
    course_code: str,
    field_names: tuple[str, ...],
    courses: list[str],
    resources: dict[str, Any],
    opts: list[Any],
) -> bool:
    """Directly evaluate positional hex set for a course code based on local device alignment.

    Used when binary toggle tokens ('35', '36', '37') are omitted from local OCF matrices.
    Deep-searches option arrays and resources for positional sets (e.g., 'BubbleSoakSet')
    and checks if the 2-char byte pair at the course's index equals 'F0' (True) or '00' (False).
    """
    if not course_code or not courses or course_code not in courses:
        return False

    raw = None

    # 1. Search in local options list
    for fname in field_names:
        val = _extract_option_value(opts, fname)
        if val:
            raw = val
            break

    # 2. Search across resources, including deep nested x.com.samsung.da.options
    if not raw and resources:
        for fname in field_names:
            val = _find_key_in_resources(resources, (fname, f"x.com.samsung.da.{fname}"))
            if val:
                raw = val
                break

            for res in resources.values():
                if isinstance(res, dict) and "x.com.samsung.da.options" in res:
                    found_opt = _extract_option_value(res["x.com.samsung.da.options"], fname)
                    if found_opt:
                        raw = found_opt
                        break
            if raw:
                break

    if not raw:
        return False

    pairs = _extract_hex_pairs(raw)
    idx = courses.index(course_code)

    # Evaluate positional index against local course order
    if idx < len(pairs):
        return pairs[idx] == "F0"

    return False


def parse_supported_options_matrix(
    raw_supported: Any,
    global_lists: dict[str, list[str]] | None = None,
    course_table: str | None = None,
    edit_course_list: str | None = None,
    resources: dict[str, Any] | None = None,
    opts: list[Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Parse Table_02 matrices dynamically using mathematical pattern discovery."""
    if global_lists is None:
        global_lists = {}
    resources = resources or {}
    opts = opts or []

    while isinstance(raw_supported, list) and len(raw_supported) > 0:
        raw_supported = raw_supported[0]

    if not isinstance(raw_supported, str) or not raw_supported:
        return {}

    raw_str = raw_supported.strip().upper()
    if len(raw_str) < 11:
        return {}

    # Skip 1-char header metadata byte (OCF Protocol Version header, e.g., '4')
    raw_payload = raw_str[1:]

    # Extract full factory course list for positional indexing
    master_courses = _extract_courses_from_supported_options(raw_str)

    # Dynamically find the exact block stride for this machine's firmware
    block_size = _discover_course_block_stride(raw_payload)
    if not block_size:
        _LOGGER.warning(
            "Could not dynamically resolve course block stride for "
            "supportedOptions string."
        )
        return {}

    supp_temp = global_lists.get("supportedWaterTemperature", [])
    supp_spin = global_lists.get("supportedSpinLevel", [])
    is_table00 = ("tapCold" in supp_temp) or ("extraHigh" in supp_spin)

    temp_list = supp_temp or (TABLE00_WATER_TEMPS if is_table00 else STANDARD_WATER_TEMPS)
    rinse_list = global_lists.get("supportedRinseCycles") or STANDARD_RINSE_CYCLES
    spin_list = supp_spin or (TABLE00_SPIN_LEVELS if is_table00 else STANDARD_SPIN_LEVELS)
    dry_list = global_lists.get("supportedDryLevel") or STANDARD_DRY_LEVELS
    soil_list = global_lists.get("supportedSoilLevel") or STANDARD_SOIL_LEVELS

    table_matrix: dict[str, dict[str, Any]] = {}

    # Slice strictly using the dynamically discovered block_size
    for i in range(0, len(raw_payload), block_size):
        segment = raw_payload[i : i + block_size]
        course_code = segment[:2]
        payload = segment[2:]

        # Extract all 4-char tokens from the segment
        tokens = [payload[k : k + 4] for k in range(0, len(payload), 4)]

        course_opts = _parse_token_list(
            tokens, temp_list, rinse_list, spin_list, dry_list, soil_list
        )

        # Direct positional evaluation when 35/36/37 tokens are missing from supportedOptions
        if course_opts["bubble_soak_supported"] is None:
            course_opts["bubble_soak_supported"] = _evaluate_positional_flag(
                course_code, ("BubbleSoakSet", "BubbleSoak"), master_courses, resources, opts
            )

        if course_opts["pre_wash_supported"] is None:
            course_opts["pre_wash_supported"] = _evaluate_positional_flag(
                course_code,
                ("PreWashAvailableSet", "PreWashSet", "PreWash"),
                master_courses,
                resources,
                opts,
            )

        if course_opts["intensive_wash_supported"] is None:
            course_opts["intensive_wash_supported"] = _evaluate_positional_flag(
                course_code,
                ("IntensiveAvailableSet", "IntensiveSet", "Intensive"),
                master_courses,
                resources,
                opts,
            )

        table_matrix[course_code] = course_opts

    return table_matrix


def _parse_token_list(
    token_list: list[str],
    temp_list: list[str],
    rinse_list: list[str],
    spin_list: list[str],
    dry_list: list[str],
    soil_list: list[str],
) -> dict[str, Any]:
    """Decode a list of 4-character hex tokens into structured options for one course."""
    opts: dict[str, Any] = {
        "temp": [],
        "rinse": [],
        "spin": [],
        "dry": [],
        "soil": [],
        "bubble_soak_supported": None,
        "pre_wash_supported": None,
        "intensive_wash_supported": None,
    }

    for token in token_list:
        if len(token) < 4:
            continue

        prefix = token[0]

        if token.startswith("35"):
            opts["bubble_soak_supported"] = token[2] == "F"
        elif token.startswith("36"):
            opts["intensive_wash_supported"] = token[2] == "F"
        elif token.startswith("37"):
            opts["pre_wash_supported"] = token[2] == "F"
        elif prefix in ("7", "C"):
            opts["soil"] = _decode_bitmask_options(token, soil_list)
        elif prefix == "8":
            opts["temp"] = _decode_bitmask_options(token, temp_list)
        elif prefix == "9":
            opts["rinse"] = _decode_bitmask_options(token, rinse_list)
        elif prefix == "A":
            opts["spin"] = _decode_bitmask_options(token, spin_list)
        elif prefix in ("B", "D"):
            opts["dry"] = _decode_bitmask_options(token, dry_list)

    for key in ("temp", "rinse", "spin", "dry", "soil"):
        if not opts[key]:
            opts[key] = ["none"]

    return opts


def _decode_bitmask_options(token: str, master_list: list[str]) -> list[str]:
    """Evaluate a 16-bit hex mask against a master option list."""
    try:
        mask = int(token[2:], 16)
    except ValueError:
        return ["none"]

    if mask == 0:
        return ["none"]

    options = [option for i, option in enumerate(master_list) if mask & (1 << i)]
    return options if options else ["none"]


def parse_table00_options_matrix(
    resources: dict[str, Any], opts: list[Any]
) -> dict[str, dict[str, Any]]:
    """Decode selectable options for current active course on Table_00 devices."""
    supp_temp = _find_key_in_resources(
        resources,
        (
            "x.com.samsung.da.supportedWaterTemperature",
            "supportedWaterTemperature",
        ),
    )
    supp_rinse = _find_key_in_resources(
        resources,
        ("x.com.samsung.da.supportedRinseCycles", "supportedRinseCycles"),
    )
    supp_spin = _find_key_in_resources(
        resources,
        ("x.com.samsung.da.supportedSpinLevel", "supportedSpinLevel"),
    )
    supp_dry = _find_key_in_resources(
        resources,
        ("x.com.samsung.da.supportedDryLevel", "supportedDryLevel"),
    )
    supp_soil = _find_key_in_resources(
        resources,
        ("x.com.samsung.da.supportedSoilLevel", "supportedSoilLevel"),
    )

    global_lists = {
        "temp": supp_temp or TABLE00_WATER_TEMPS,
        "rinse": supp_rinse or STANDARD_RINSE_CYCLES,
        "spin": supp_spin or TABLE00_SPIN_LEVELS,
        "dry": supp_dry or STANDARD_DRY_LEVELS,
        "soil": supp_soil or STANDARD_SOIL_LEVELS,
    }

    raw_current = _extract_option_value(opts, "Course")
    active_course_hex = (
        raw_current.replace("Course_", "").strip().upper() if raw_current else "01"
    )

    opts_payload = {"x.com.samsung.da.options": opts}

    return {
        active_course_hex: {
            "temp": _get_table00_course_list(
                "WaterTemperatureAvailableSet", global_lists["temp"], resources, opts
            ),
            "rinse": _get_table00_course_list(
                "RinseCyclesAvailableSet", global_lists["rinse"], resources, opts
            ),
            "spin": _get_table00_course_list(
                "SpinLevelAvailableSet", global_lists["spin"], resources, opts
            ),
            "dry": _get_table00_course_list(
                "DryLevelAvailableSet", global_lists["dry"], resources, opts
            ),
            "soil": _get_table00_course_list(
                "SoilLevelAvailableSet", global_lists["soil"], resources, opts
            ),
            "bubble_soak_supported": is_supported_for_current_course(
                opts_payload, resources, "bubble_soak", "BubbleSoakSet"
            ),
            "pre_wash_supported": is_supported_for_current_course(
                opts_payload, resources, "pre_wash", "PreWashSet"
            ),
            "intensive_wash_supported": is_supported_for_current_course(
                opts_payload, resources, "intensive", "IntensiveSet"
            ),
        }
    }


def _get_table00_course_list(
    field_name: str, master_list: list[str], resources: dict[str, Any], opts: list[Any]
) -> list[str]:
    """Decode positional bitmask hex pair for current active course."""
    raw = _extract_option_value(opts, field_name) or _find_key_in_resources(
        resources, (field_name, f"x.com.samsung.da.{field_name}")
    )
    if not raw:
        return master_list if master_list else ["none"]

    pairs = _extract_hex_pairs(raw)
    if not pairs:
        return master_list if master_list else ["none"]

    try:
        mask = int(pairs[0], 16)
    except ValueError:
        return master_list if master_list else ["none"]

    if mask == 0:
        return ["none"]

    selected = [opt for i, opt in enumerate(master_list) if mask & (1 << i)]
    return selected if selected else ["none"]


def is_supported_for_current_course(
    rep: dict[str, Any],
    resources: dict[str, Any],
    key: str,
    availability_field: str | None = None,
) -> bool:
    """Evaluate feature support for active course with fail-open safeguards."""
    rep = rep or {}
    resources = resources or {}
    opts = rep.get("x.com.samsung.da.options") or []

    # 1. Resolve active Course token
    raw_current = _extract_option_value(opts, "Course")
    if not raw_current and isinstance(resources, dict):
        for res in resources.values():
            if isinstance(res, dict) and "x.com.samsung.da.options" in res:
                found_course = _extract_option_value(res["x.com.samsung.da.options"], "Course")
                if found_course:
                    raw_current = found_course
                    if (
                        not availability_field
                        or not _extract_option_value(opts, availability_field)
                    ):
                        opts = res["x.com.samsung.da.options"]
                    break

    if not raw_current:
        return True

    current_course_hex = raw_current.replace("Course_", "").strip().upper()

    # 2. Table_02 / Table_03 Tokenized Matrix Evaluation
    raw_supported = _find_key_in_resources(
        resources,
        ("x.com.samsung.da.supportedOptions", "supportedOptions"),
    )
    if raw_supported:
        washer_res = resources.get("/washer/vs/0") or {}
        course_table = _find_key_in_resources(
            resources,
            ("x.com.samsung.da.st.courseTable", "courseTable"),
        )
        edit_course_list = _find_key_in_resources(
            resources,
            ("x.com.samsung.da.editCourseList", "editCourseList"),
        )

        matrix = parse_supported_options_matrix(
            raw_supported,
            global_lists={
                "supportedSoilLevel": washer_res.get(
                    "x.com.samsung.da.supportedSoilLevel", []
                ),
                "supportedWaterTemperature": washer_res.get(
                    "x.com.samsung.da.supportedWaterTemperature", []
                ),
                "supportedSpinLevel": washer_res.get(
                    "x.com.samsung.da.supportedSpinLevel", []
                ),
                "supportedRinseCycles": washer_res.get(
                    "x.com.samsung.da.supportedRinseCycles", []
                ),
                "supportedDryLevel": washer_res.get(
                    "x.com.samsung.da.supportedDryLevel", []
                ),
            },
            course_table=course_table,
            edit_course_list=edit_course_list,
            resources=resources,
            opts=opts,
        )

        if matrix and current_course_hex in matrix:
            flag_key = MATRIX_KEY_MAP.get(key, f"{key.lower()}_supported")
            val = matrix[current_course_hex].get(flag_key)
            if val is not None:
                return bool(val)

    # 3. Positional Hex Evaluation (Fallback when binary token missing from matrix or for
    #    hybrid sets)
    if availability_field:
        raw = _extract_option_value(opts, availability_field) or _find_key_in_resources(
            resources, (availability_field, f"x.com.samsung.da.{availability_field}")
        )
        if raw:
            pairs = _extract_hex_pairs(raw)
            courses = _extract_courses_list(resources, opts)

            # Strict contract: len(pairs) MUST equal len(courses) (25 == 25 on physical washer).
            # Mismatched/truncated test payloads fail open (return True).
            if courses and len(pairs) == len(courses) and current_course_hex in courses:
                idx = courses.index(current_course_hex)
                _LOGGER.error(
                    "[WASHER_TRACE] key=%s | course=%s | idx=%d | pair=%s | "
                    "courses=%s | raw_set=%s",
                    key,
                    current_course_hex,
                    idx,
                    pairs[idx] if idx < len(pairs) else "OOB",
                    courses,
                    raw,
                )
                return pairs[idx] == "F0"

    # 4. Fail-Open Safeguard
    return True
