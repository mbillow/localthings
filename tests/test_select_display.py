"""Tests for select-option display casing (custom_components/localthings/select.py)."""
from custom_components.localthings.select import _display

_UNTRANSLATED = None
_TRANSLATED = 'door_alert'


def test_display_titlecases_a_fully_lowercase_device_native_token():
    """Samsung's sound-mode field is genuinely lowercase on the wire
    ('voice'/'tone'/'mute') -- these have no other casing signal to key
    off, so title-case them for display."""
    assert _display('voice', _UNTRANSLATED) == 'Voice'
    assert _display('mute', _UNTRANSLATED) == 'Mute'


def test_display_inserts_a_space_at_a_camelcase_boundary():
    """'ExtraHigh' (from supportedHeatedDry) should read as two words."""
    assert _display('ExtraHigh', _UNTRANSLATED) == 'Extra High'


def test_display_passes_through_an_already_human_friendly_value():
    """'AI Wash' etc. (dishwasher cycle names) already read fine and
    have no camelCase boundary or all-lowercase pattern -- must not be
    mangled."""
    assert _display('AI Wash', _UNTRANSLATED) == 'AI Wash'
    assert _display('Low', _UNTRANSLATED) == 'Low'
    assert _display('Off', _UNTRANSLATED) == 'Off'


def test_display_lowercases_for_translation_key_lookup():
    """An entity with a translation_key must match strings.json's
    lowercase keys exactly -- unlike the untranslated cases above, this
    is not a cosmetic transform."""
    assert _display('Whiskey_IceBall_3', _TRANSLATED) == 'whiskey_iceball_3'


def test_display_passes_through_non_string_values():
    assert _display(None, _UNTRANSLATED) is None
