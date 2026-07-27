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


# The hood fan is its device's primary feature: fan.py sets _attr_name = None
# so it presents as the device itself, and never reads a catalog name.
UNNAMED_DESCRIPTORS = {("fan", "fan")}


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
    
