"""Tests for select-option display casing (custom_components/localthings/select.py)."""

from custom_components.localthings.select import _display

_UNTRANSLATED = None
_TRANSLATED = "ice_type"


def test_display_titlecases_a_fully_lowercase_device_native_token():
    """Samsung's sound-mode field is genuinely lowercase on the wire
    ('voice'/'tone'/'mute') -- these have no other casing signal to key
    off, so title-case them for display."""
    assert _display("voice", _UNTRANSLATED) == "Voice"
    assert _display("mute", _UNTRANSLATED) == "Mute"


def test_display_inserts_a_space_at_a_camelcase_boundary():
    """'ExtraHigh' (from supportedHeatedDry) should read as two words."""
    assert _display("ExtraHigh", _UNTRANSLATED) == "Extra High"


def test_display_passes_through_an_already_human_friendly_value():
    """'AI Wash' etc. (dishwasher cycle names) already read fine and
    have no camelCase boundary or all-lowercase pattern -- must not be
    mangled."""
    assert _display("AI Wash", _UNTRANSLATED) == "AI Wash"
    assert _display("Low", _UNTRANSLATED) == "Low"
    assert _display("Off", _UNTRANSLATED) == "Off"


def test_display_lowercases_for_translation_key_lookup():
    """An entity with a translation_key must match the catalog's
    lowercase keys exactly -- unlike the untranslated cases above, this
    is not a cosmetic transform."""
    assert _display("Whiskey_IceBall_3", _TRANSLATED) == "whiskey_iceball_3"


def test_unknown_translated_vendor_value_keeps_readable_fallback():
    """A firmware-added value must remain readable instead of being mangled."""
    assert _display("FutureVendorMode", _TRANSLATED) == "Future Vendor Mode"


def test_known_camel_case_state_uses_snake_case_translation_key():
    assert _display("ExtraHigh", "heated_dry") == "extra_high"


def test_display_passes_through_non_string_values():
    assert _display(None, _UNTRANSLATED) is None


def test_display_uses_fallback_when_translation_has_no_state_table():
    assert _display("69", "cycle", lambda value: f"Unknown (0x{value})") == ("Unknown (0x69)")


def test_uncatalogued_value_stays_raw_when_the_fallback_declines_it():
    """The "no state table" escape hatch keys off whether anything actually
    named the value, not off whether a fallback was supplied.

    laundry.cycle_select always supplies one now (it labels cloud "Download"
    programs) and returns None for everything else. Keyed on the fallback's
    presence instead, every dryer/dishwasher/air-dresser on an unrecognized
    course table would have its options and state cosmetically reshaped --
    course '0E' rendered '0 E' -- silently breaking automations and recorder
    history."""
    assert _display("0E", "cycle", None) == "0E"
    assert _display("0E", "cycle", lambda value: None) == "0E"
    # A fallback that does name the value still wins.
    assert _display("0E", "cycle", lambda value: f"Course {value}") == "Course 0E"


def test_tp2x_ref_20k_pantry_zone_options_are_named():
    """A TP2X_REF_20K's flex crisper (/status/pantry/one/vs/0, issue #464)
    reports the flex zone's RF9000A options without the CV_ prefix. Unnamed,
    'TTYPE_RF9000A_FRIDGE' fell to the cosmetic split and showed as
    'TTYPE_RF9000 A_FRIDGE'."""
    assert _display("TTYPE_MEAT_FISH", "pantry_zone_mode") == "ttype_meat_fish"
    assert _display("TTYPE_RF9000A_FRIDGE", "pantry_zone_mode") == "ttype_rf9000a_fridge"
