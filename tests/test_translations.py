"""Translation catalog architecture tests.

Home Assistant loads a custom integration's ``translations/<lang>.json``
directly -- there is no ``strings.json`` step and no ``[%key:...%]``
resolution, both of which belong to Core's build tooling. So
``translations/en.json`` is the source of truth here, and these tests hold
the two invariants that follow from that: every translation key the Python
side names has to exist in it, and every other language has to mirror its
shape.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from string import Formatter

from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.entities import PLATFORM_OF

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "localthings"
TRANSLATIONS = INTEGRATION / "translations"


def _load(language: str) -> dict:
    return json.loads((TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8"))


def _languages() -> list[str]:
    return sorted(path.stem for path in TRANSLATIONS.glob("*.json"))


def _topology(value):
    if isinstance(value, dict):
        return {key: _topology(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_topology(child) for child in value]
    return None


def _placeholders(value: str) -> set[str]:
    return {
        field_name for _, field_name, _, _ in Formatter().parse(value) if field_name is not None
    }


def _walk_strings(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, (*path, str(index)))
    elif isinstance(value, str):
        yield path, value


def _all_descriptions():
    capabilities_dir = INTEGRATION / "registry" / "capabilities"
    seen: set[int] = set()
    for module_path in capabilities_dir.glob("*.py"):
        if module_path.stem == "__init__":
            continue
        module = importlib.import_module(
            f"custom_components.localthings.registry.capabilities.{module_path.stem}"
        )

        def visit(value):
            if isinstance(value, Capability):
                if id(value) in seen:
                    return
                seen.add(id(value))
                yield from value.entities
            elif isinstance(value, (tuple, list, set)):
                for child in value:
                    yield from visit(child)

        for value in vars(module).values():
            yield from visit(value)


def test_every_language_mirrors_the_english_catalog():
    """English is the complete catalog; the rest must match it key for key.

    A missing key silently falls back to English at runtime, so checking the
    shape is the only way to notice a half-finished translation.
    """
    english = _load("en")
    english_strings = dict(_walk_strings(english))
    for language in _languages():
        if language == "en":
            continue
        translated = _load(language)
        assert _topology(english) == _topology(translated), language

        translated_strings = dict(_walk_strings(translated))
        for path, value in english_strings.items():
            # Placeholders are substituted by name, so a translation that
            # drops or invents one renders a literal '{...}' in the UI.
            assert _placeholders(value) == _placeholders(translated_strings[path]), (language, path)


def test_no_catalog_carries_unresolved_core_references():
    """``[%key:...%]`` never resolves for a custom integration.

    Core's build tooling expands these; nothing does for us, so a reference
    left in a catalog would reach the UI verbatim.
    """
    for language in _languages():
        unresolved = [
            (path, value) for path, value in _walk_strings(_load(language)) if "[%key:" in value
        ]
        assert unresolved == [], language


def test_confirmed_korean_table_02_washer_course_names():
    states = _load("en")["entity"]["select"]["washer_cycle_table_02"]["state"]
    assert {
        code: states[code]
        for code in (
            "69",
            "6a",
            "6b",
            "6c",
            "6d",
            "6e",
            "6f",
            "70",
            "71",
            "72",
            "73",
            "74",
            "75",
            "76",
            "77",
            "78",
            "79",
            "88",
        )
    } == {
        "69": "AI Wash",
        "6a": "Wool",
        "6b": "Denim",
        "6c": "Blouses",
        "6d": "Delicates",
        "6e": "Active Wear",
        "6f": "Bedding",
        "70": "Towels",
        "71": "Quick Wash",
        "72": "Shirts",
        "73": "Sanitize",
        "74": "Drum Clean",
        "75": "Outdoor",
        "76": "Baby Care",
        "77": "Cottons",
        "78": "Rinse + Spin",
        "79": "Spin Only",
        "88": "Pet Care",
    }


def test_confirmed_washer_table_02_towels_bedding_are_not_swapped():
    """Issue #343: DA_WM_TP1_21_COMMON's Table_02 had 24/33 transposed --
    selecting 'Towels' in HA ran the washer's Bedding cycle and vice versa.
    24 must agree with the 69/6A-79/88 family's own Bedding/Towels pair
    (6f/70), not with each other.

    Checked in every locale catalog, not just English: the underlying bug
    is a device-code mapping error, not a wording issue, and
    test_every_language_mirrors_the_english_catalog only checks key
    topology -- a locale-specific 24/33 swap (the exact shape of #343)
    would still pass that test."""
    for language in _languages():
        states = _load(language)["entity"]["select"]["washer_cycle_table_02"]["state"]
        assert states["24"] == states["6f"], language
        assert states["33"] == states["70"], language


def test_confirmed_washer_table_02_missing_course_names():
    """Issue #342: 06/08/a0 had no translation and fell back to the raw
    device code in the UI; 74 was already translated by the time this
    landed and is pinned here only as a "didn't regress" anchor.

    06's wording was corrected by issue #376 (originally 'XXL Laundry';
    that device's own '이불' report -- the same text as the confirmed
    Bedding codes 24/6f -- turned out to be the right one)."""
    states = _load("en")["entity"]["select"]["washer_cycle_table_02"]["state"]
    assert {code: states[code] for code in ("06", "08", "74", "a0")} == {
        "06": "Bedding",
        "08": "Rinse+Spin",
        "74": "Drum Clean",
        "a0": "15' Quick Wash",
    }


def test_confirmed_washer_table_02_ww90dg5g34able_course_names():
    """Issue #363: 0A/B0 rendered as raw hex on a WW90DG5G34ABLE
    (DA_WM_TP1_21_COMMON), whose other Table_02 labels the reporter
    confirmed were already correct.

    0A joins 33/54/70 as a Towels code -- checked in every locale, since a
    locale that translated 0A differently from the Towels codes it shares a
    meaning with would still pass the key-topology test, the same gap
    issue #343 fell through.
    """
    for language in _languages():
        states = _load(language)["entity"]["select"]["washer_cycle_table_02"]["state"]
        assert states["0a"] == states["33"], language
        assert states["b0"] != states["34"], language
    english = _load("en")["entity"]["select"]["washer_cycle_table_02"]["state"]
    assert {code: english[code] for code in ("0a", "b0")} == {
        "0a": "Towels",
        "b0": "Mixed Load",
    }


def test_confirmed_washer_table_02_wf21t6500kv_course_names():
    """Issue #376: WF21T6500KV (DA_WM_A51_20_COMMON) reported Korean labels
    for 21 previously-untranslated Table_02 codes.

    Several share their Korean text with an already-confirmed code
    (cross-checked against translations/ko.json, not guessed), so those
    reuse the established label rather than a fresh translation -- checked
    in every locale per issue #363's precedent, since a locale that
    translated the shared text differently would still pass the
    key-topology test alone. That includes '06': its Korean text ('이불')
    matches the confirmed Bedding codes 24/6f exactly, which superseded
    the wrong 'XXL Laundry' wording #342 had originally given it (see
    test_confirmed_washer_table_02_missing_course_names).
    """
    english = _load("en")["entity"]["select"]["washer_cycle_table_02"]["state"]
    assert {
        code: english[code]
        for code in (
            "02",
            "03",
            "05",
            "06",
            "07",
            "09",
            "0b",
            "0c",
            "0d",
            "0e",
            "0f",
            "10",
            "11",
            "12",
            "13",
            "14",
            "15",
            "16",
            "18",
            "19",
            "1a",
        )
    } == {
        "02": "Extra Heavy Duty",
        "03": "Super Eco Wash",
        "05": "Wool/Lingerie",
        "06": "Bedding",
        "07": "Outdoor",
        "09": "Drum Clean",
        "0b": "Boil Wash",
        "0c": "Baby Care",
        "0d": "Spin Only",
        "0e": "Cloudy Day",
        "0f": "Pure Wash",
        "10": "Spin Dry",
        "11": "Summer Bedding",
        "12": "Cottons",
        "13": "Black Cottons",
        "14": "Delicate Underwear",
        "15": "Activewear",
        "16": "Blouses",
        "18": "Soft Bubble",
        "19": "AI Wash",
        "1a": "Shirts",
    }

    # Anchors picked among the code's own duplicate-label siblings by
    # whichever this catalog already has translated consistently -- '2b'
    # over its "AI 맞춤세탁" twin '69', which nl.json alone translates
    # differently ("AI wassen" vs "AI Wash"), an existing inconsistency
    # unrelated to this issue and not one this PR resolves.
    reused_pairs = (
        ("06", "24"),
        ("07", "75"),
        ("09", "3a"),
        ("0c", "2e"),
        ("15", "2f"),
        ("16", "6c"),
        ("19", "2b"),
        ("1a", "32"),
    )
    for language in _languages():
        states = _load(language)["entity"]["select"]["washer_cycle_table_02"]["state"]
        for new_code, anchor_code in reused_pairs:
            assert states[new_code] == states[anchor_code], (language, new_code, anchor_code)


def test_confirmed_dryer_table_03_dv19t8745bv_course_names():
    """Issue #376: DV19T8745BV (DA_WM_TP1_21_COMMON) reported Korean labels
    for 18 previously-untranslated Table_03 codes -- none conflict with an
    existing entry. As with the washer table above, codes sharing Korean
    text with an already-confirmed code (including two, '3c'/'3d', whose
    text matches a washer_cycle_table_02 entry rather than one on this
    table) reuse that label instead of a fresh translation, checked in
    every locale.
    """
    english = _load("en")["entity"]["select"]["dryer_cycle_table_03"]["state"]
    assert {
        code: english[code]
        for code in (
            "02",
            "03",
            "05",
            "07",
            "09",
            "0b",
            "0c",
            "0e",
            "0f",
            "11",
            "28",
            "37",
            "38",
            "39",
            "3a",
            "3b",
            "3c",
            "3d",
        )
    } == {
        "02": "AI Dry",
        "03": "Super Speed",
        "05": "Bedding",
        "07": "Delicates",
        "09": "Shirts",
        "0b": "Padding Care",
        "0c": "Outdoor Water-Repellent Care",
        "0e": "Towels",
        "0f": "Wool",
        "11": "Cool air",
        "28": "Interior Hot Air Sanitize",
        "37": "Blouses",
        "38": "Iron dry",
        "39": "Room Dehumidify",
        "3a": "Hygiene Care",
        "3b": "Bedding/Dust Off",
        "3c": "Activewear",
        "3d": "Denim",
    }

    same_table_pairs = (
        ("02", "29"),
        ("03", "17"),
        ("05", "1b"),
        ("07", "19"),
        ("09", "1c"),
        ("0e", "1d"),
        ("0f", "1a"),
        ("11", "24"),
        ("38", "20"),
        ("3a", "21"),
    )
    for language in _languages():
        d_states = _load(language)["entity"]["select"]["dryer_cycle_table_03"]["state"]
        w_states = _load(language)["entity"]["select"]["washer_cycle_table_02"]["state"]
        for new_code, anchor_code in same_table_pairs:
            assert d_states[new_code] == d_states[anchor_code], (language, new_code, anchor_code)
        # Cross-table reuse: same Korean text as a washer_cycle_table_02 code.
        assert d_states["37"] == w_states["6c"], language  # Blouses
        assert d_states["3c"] == w_states["2f"], language  # Activewear
        assert d_states["3d"] == w_states["66"], language  # Denim


def test_confirmed_dryer_dry_level_labels():
    """dryer_dry_level's word vocabulary (None/Damp/Less/Normal/More/Very),
    confirmed across the TP1_21 dryer fixtures. The numeric vocabulary
    (None/1/2/3) that DV6800N reports is deliberately NOT translated -- no
    confirmed meaning for the digits exists, and select._display renders an
    uncatalogued value raw rather than guessing a label for it."""
    states = _load("en")["entity"]["select"]["dryer_dry_level"]["state"]
    assert set(states) == {"none", "damp", "less", "normal", "more", "very"}
    assert states["none"] == "Off"
    for digit in ("1", "2", "3"):
        assert digit not in states


def test_reported_washer_standard_courses_all_have_table_02_labels():
    """Every non-personal code in the reported washer's live course list
    must resolve through the Table_02 catalog instead of appearing as raw
    text. F1/F3 deliberately come from device-provided personal names.
    """
    states = _load("en")["entity"]["select"]["washer_cycle_table_02"]["state"]
    reported = {
        "69",
        "6f",
        "73",
        "75",
        "78",
        "01",
        "71",
        "96",
        "88",
        "70",
        "6d",
        "6a",
        "76",
        "72",
        "6c",
        "6e",
        "6b",
        "77",
        "74",
        "79",
    }

    assert reported <= states.keys()


def test_confirmed_dishwasher_course_names():
    states = _load("en")["entity"]["select"]["dishwasher_cycle"]["state"]
    assert {
        code: states[code]
        for code in (
            "82",
            "8a",
            "a7",
            "a8",
            "8c",
            "88",
        )
    } == {
        "82": "Auto",
        "8a": "Normal",
        "a7": "Heavy",
        "a8": "Express",
        "8c": "Extra Silence",
        "88": "Self Clean",
    }


def test_confirmed_course_names_are_localized():
    washer_codes = (
        "69",
        "6a",
        "6b",
        "6c",
        "6d",
        "6e",
        "6f",
        "70",
        "71",
        "72",
        "73",
        "74",
        "75",
        "76",
        "77",
        "78",
        "79",
        "88",
    )
    dishwasher_codes = ("82", "8a", "a7", "a8", "8c", "88")
    expected = {
        "cs": {
            "washer": (
                "AI praní",
                "Vlna",
                "Džíny",
                "Halenky",
                "Jemné prádlo",
                "Sportovní oblečení",
                "Ložní prádlo",
                "Ručníky",
                "Rychlé praní",
                "Košile",
                "Dezinfekce",
                "Čištění bubnu",
                "Outdoor",
                "Dětské potřeby",
                "Bavlna",
                "Máchání + odstřeďování",
                "Pouze odstřeďování",
                "Péče o domácí mazlíčky",
            ),
            "dishwasher": (
                "Automatický",
                "Normální",
                "Intenzivní",
                "Expresní",
                "Extra tichý",
                "Samočištění",
            ),
        },
        "nl": {
            "washer": (
                "AI wassen",
                "Wol",
                "Spijkergoed",
                "Blouses",
                "Fijne was",
                "Sportkleding",
                "Beddengoed",
                "Handdoeken",
                "Snelle was",
                "Overhemden",
                "Hygiëne",
                "Trommel reinigen",
                "Outdoor",
                "Babyverzorging",
                "Katoen",
                "Spoelen + centrifugeren",
                "Alleen centrifugeren",
                "Huisdierverzorging",
            ),
            "dishwasher": (
                "Auto",
                "Normaal",
                "Intensief",
                "Express",
                "Extra stil",
                "Zelfreiniging",
            ),
        },
    }

    for language, translations in expected.items():
        catalog = _load(language)["entity"]["select"]
        washer = catalog["washer_cycle_table_02"]["state"]
        dishwasher = catalog["dishwasher_cycle"]["state"]
        assert tuple(washer[code] for code in washer_codes) == translations["washer"]
        assert tuple(dishwasher[code] for code in dishwasher_codes) == (translations["dishwasher"])


# The hood fan is its device's primary feature: fan.py sets _attr_name = None
# so it presents as the device itself, and never reads a catalog name. Same
# for the ARTIK051 air-purifier's airflow_fan (issue #56) -- ordered speed
# levels, no presets, same _attr_name = None treatment.
#
# The EHS DHW water_heater is deliberately NOT in here: it is one loop of a
# two-loop device rather than the device itself, so it carries a catalog
# name like everything else (entity.water_heater.dhw, via the descriptor's
# translation_key). That is independent of its *states* -- those are all
# HA's own standard water_heater states (STATE_ECO/HEAT_PUMP/HIGH_DEMAND/
# PERFORMANCE/OFF), which Home Assistant translates itself via the
# entity_component fallback, so no per-state entry is needed either way.
UNNAMED_DESCRIPTORS = {
    ("fan", "fan"),
    ("fan", "airflow_fan"),
}


def test_every_descriptor_has_an_entity_catalog_entry():
    """A descriptor's name comes from the catalog or nowhere.

    ``translation_key`` defaults to ``desc.key`` (entity.py), and there is no
    Python-side name to fall back on, so a descriptor with no catalog entry
    is an entity with no name.
    """
    entity_strings = _load("en")["entity"]
    missing = []
    for desc in _all_descriptions():
        platform = PLATFORM_OF[type(desc)]
        if (platform, desc.key) in UNNAMED_DESCRIPTORS:
            continue
        translation_key = desc.translation_key
        if callable(translation_key):
            # Runtime table resolvers pick their key out of the catalog
            # itself (see laundry.cycle_select), so there's nothing static
            # to check here; the generic 'cycle' fallback is asserted below.
            continue
        if translation_key is None:
            translation_key = desc.key
        if translation_key not in entity_strings.get(platform, {}):
            missing.append((platform, desc.key, translation_key))
    assert missing == []

    # cycle_select falls back to 'cycle' for any course table without its
    # own entry, and resolves to '<family>_cycle_<table>' where there is one.
    select_strings = entity_strings["select"]
    for key in ("cycle", "washer_cycle_table_02", "dryer_cycle_table_03"):
        assert key in select_strings


def test_all_entity_state_translation_keys_are_lowercase():
    entity_strings = _load("en")["entity"]
    for platform in entity_strings.values():
        for translation in platform.values():
            for state_key in translation.get("state", {}):
                assert state_key == state_key.lower()


def test_every_ac_convenient_mode_code_has_a_preset_label():
    """issue #91 review feedback #3: AC preset resolution is fully dynamic
    (climate._preset_to_ha), so every fixture's /mode/convenient/vs/0
    supportedModes code surfaces as a preset -- an unlabelled one falls back
    to its raw device code in the UI. Cheap guard against repeating that gap:
    every non-'Off' code across every AC fixture must either resolve to one
    of HA's own auto-localized standard presets or have an explicit label in
    en.json.
    """
    from homeassistant.components.climate.const import (
        PRESET_ACTIVITY,
        PRESET_AWAY,
        PRESET_BOOST,
        PRESET_COMFORT,
        PRESET_ECO,
        PRESET_HOME,
        PRESET_SLEEP,
    )

    standard = {
        PRESET_ACTIVITY,
        PRESET_AWAY,
        PRESET_BOOST,
        PRESET_COMFORT,
        PRESET_ECO,
        PRESET_HOME,
        PRESET_SLEEP,
    }
    preset_labels = set(
        _load("en")["entity"]["climate"]["airconditioner"]["state_attributes"]["preset_mode"][
            "state"
        ]
    )
    fixtures_dir = Path(__file__).parent / "fixtures"
    missing = []
    for path in sorted(fixtures_dir.glob("airconditioner*_device.json")):
        dump = json.loads(path.read_text())
        conv = next(
            (
                item
                for item in dump.get("device0", [])
                if item.get("href") == "/mode/convenient/vs/0"
            ),
            None,
        )
        if not conv:
            continue
        for code in conv["rep"].get("x.com.samsung.da.supportedModes", []):
            if code == "Off":
                continue
            label = code.lower()
            if label in standard or label in preset_labels:
                continue
            missing.append((path.name, code))
    assert missing == []


def test_every_kimchi_zone_supportmode_code_has_a_state_label():
    """Same guard as the AC preset one above, for KIMCHI_ZONE's
    kimchi_zone_mode select (fridge.py, issue #26): the write path resolved
    from options_field is fully dynamic too, so an unlabelled supportMode
    code across any /status/kimchi/<slot>/vs/0 resource would silently
    render as its raw device token instead of the translated state.
    """
    state_labels = set(_load("en")["entity"]["select"]["kimchi_zone_mode"]["state"])
    fixtures_dir = Path(__file__).parent / "fixtures"
    missing = []
    for path in sorted(fixtures_dir.glob("*_device.json")):
        dump = json.loads(path.read_text())
        for item in dump.get("device0", []):
            href = item.get("href", "")
            if not (href.startswith("/status/kimchi/") and href.endswith("/vs/0")):
                continue
            missing.extend(
                (path.name, href, code)
                for code in item["rep"].get("x.com.samsung.da.supportMode", [])
                if code.lower() not in state_labels
            )
    assert missing == []
