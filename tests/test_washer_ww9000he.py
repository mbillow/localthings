"""WW9000HE washer on the 8888 bridge (#635): its cycles are labelled from
Table_00, which the bridge doesn't report, so the family declares it."""

from custom_components.localthings.catalog import translated_states
from custom_components.localthings.legacy_http import course_table
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import laundry, washer
from custom_components.localthings.registry.discovery import discover

# From the owner's sanitized diagnostic (#635); the description's per-unit
# suffix is redacted.
RESOURCES = {
    "/information/vs/0": {
        "x.com.samsung.da.modelNum": "WW9000HE",
        "x.com.samsung.da.deviceSubType": "Washer",
        "x.com.samsung.da.description": "WW9000HE(REDACTED)",
    },
    "/course/vs/0": {
        "x.com.samsung.da.options": [
            "Course_31",
            "DeviceType_0157",
            "QuickWash_Off",
            "EasyIron_Disable",
        ],
        "x.com.samsung.da.supportedOptions": [
            "33E84109308A53F2F847E933FA67F2C843E923FA41F30833E933FA30F31841E943FA41F47843E923FA"
            "53F34811E923FA41F35831E930FA41F37867E943FA53F11841E930FA30F48831E920FA2073A841E930"
            "FA30F3B80009001A57E198000913FA57F3385209204A2041B8410943FA41F418410933FA53F438520"
            "943FA53F428410933FA41F1E8410943FA41F1F8410933FA41F3F843E923FA67F"
        ],
    },
    **course_table("WW9000HE(REDACTED)"),
}

# The panel order the owner read the 22 programs off in.
CODES = [
    "3E", "2F", "2C", "30", "31", "47", "34", "35", "37", "11", "48",
    "3A", "3B", "19", "33", "1B", "41", "43", "42", "1E", "1F", "3F",
]  # fmt: skip


def _cycle():
    return next(e for e in washer.WASHER_COURSE.entities if e.key == "cycle")


def test_supported_options_parse_to_the_owners_22_programs():
    assert laundry.cycle_options(RESOURCES) == CODES


def test_every_program_is_labelled_from_table_00():
    key = _cycle().translation_key
    assert callable(key)
    assert key(RESOURCES) == "washer_cycle_table_00"
    known = translated_states("select", key(RESOURCES))
    assert [code for code in CODES if code.lower() not in known] == []


def test_the_current_cycle_reads_through_the_washer_registry():
    reg = resolve(RESOURCES)
    assert reg is not None and reg.name == "washer"
    bound = discover(RESOURCES, reg.capabilities, reg.pattern_capabilities)
    assert flatten(bound, RESOURCES)["cycle"] == "31"
