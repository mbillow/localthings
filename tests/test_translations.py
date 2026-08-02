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


INTEGRATION = (
    Path(__file__).parents[1] / "custom_components" / "localthings"
)
TRANSLATIONS = INTEGRATION / "translations"


def _load(language: str) -> dict:
    return json.loads(
        (TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8")
    )


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
        field_name
        for _, field_name, _, _ in Formatter().parse(value)
        if field_name is not None
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
            assert _placeholders(value) == _placeholders(
                translated_strings[path]
            ), (language, path)


def test_no_catalog_carries_unresolved_core_references():
    """``[%key:...%]`` never resolves for a custom integration.

    Core's build tooling expands these; nothing does for us, so a reference
    left in a catalog would reach the UI verbatim.
    """
    for language in _languages():
        unresolved = [
            (path, value)
            for path, value in _walk_strings(_load(language))
            if "[%key:" in value
        ]
        assert unresolved == [], language


def test_confirmed_korean_table_02_washer_course_names():
    states = _load('en')['entity']['select']['washer_cycle_table_02']['state']
    assert {code: states[code] for code in (
        '69', '6a', '6b', '6c', '6d', '6e', '6f', '70', '71',
        '72', '73', '74', '75', '76', '77', '78', '79', '88',
    )} == {
        '69': 'AI Wash',
        '6a': 'Wool',
        '6b': 'Denim',
        '6c': 'Blouses',
        '6d': 'Delicates',
        '6e': 'Active Wear',
        '6f': 'Bedding',
        '70': 'Towels',
        '71': 'Quick Wash',
        '72': 'Shirts',
        '73': 'Sanitize',
        '74': 'Drum Clean',
        '75': 'Outdoor',
        '76': 'Baby Care',
        '77': 'Cottons',
        '78': 'Rinse + Spin',
        '79': 'Spin Only',
        '88': 'Pet Care',
    }


def test_confirmed_dishwasher_course_names():
    states = _load('en')['entity']['select']['dishwasher_cycle']['state']
    assert {code: states[code] for code in (
        '82', '8a', 'a7', 'a8', '8c', '88',
    )} == {
        '82': 'Auto',
        '8a': 'Normal',
        'a7': 'Heavy',
        'a8': 'Express',
        '8c': 'Extra Silence',
        '88': 'Self Clean',
    }


# The hood fan is its device's primary feature: fan.py sets _attr_name = None
# so it presents as the device itself, and never reads a catalog name. Same
# for the ARTIK051 air-purifier's airflow_fan (issue #56) -- ordered speed
# levels, no presets, same _attr_name = None treatment.
UNNAMED_DESCRIPTORS = {("fan", "fan"), ("fan", "airflow_fan")}


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
        PRESET_ACTIVITY, PRESET_AWAY, PRESET_BOOST, PRESET_COMFORT,
        PRESET_ECO, PRESET_HOME, PRESET_SLEEP,
    )
    standard = {PRESET_ACTIVITY, PRESET_AWAY, PRESET_BOOST, PRESET_COMFORT,
                PRESET_ECO, PRESET_HOME, PRESET_SLEEP}
    preset_labels = set(
        _load("en")["entity"]["climate"]["airconditioner"]["state_attributes"]
        ["preset_mode"]["state"]
    )
    fixtures_dir = Path(__file__).parent / "fixtures"
    missing = []
    for path in sorted(fixtures_dir.glob("airconditioner*_device.json")):
        dump = json.loads(path.read_text())
        conv = next(
            (item for item in dump.get("device0", [])
             if item.get("href") == "/mode/convenient/vs/0"), None,
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
    state_labels = set(
        _load("en")["entity"]["select"]["kimchi_zone_mode"]["state"]
    )
    fixtures_dir = Path(__file__).parent / "fixtures"
    missing = []
    for path in sorted(fixtures_dir.glob("*_device.json")):
        dump = json.loads(path.read_text())
        for item in dump.get("device0", []):
            href = item.get("href", "")
            if not (href.startswith("/status/kimchi/") and href.endswith("/vs/0")):
                continue
            for code in item["rep"].get("x.com.samsung.da.supportMode", []):
                if code.lower() not in state_labels:
                    missing.append((path.name, href, code))
    assert missing == []
