"""The integration's own shipped translation catalog, as data.

Home Assistant loads exactly one file per language for a custom integration:
``translations/<lang>.json``. It does not read ``strings.json`` and it does
not resolve ``[%key:...%]`` references -- both of those belong to Core's
build tooling (``script/translations``), which custom integrations never run
through. So ``translations/en.json`` is not a generated artifact here, it is
the source of truth, and English is the one catalog required to be complete
(hassfest validates it for custom integrations).

That makes the English catalog the authority on *which* translation keys and
states exist, which is something the Python side genuinely needs to know:

  * ``select`` has to decide whether to normalize a raw Samsung option into
    a lowercase state key (so Home Assistant can look it up) or leave the
    vendor's own casing alone as a readable fallback.
  * ``laundry.cycle_select`` has to decide whether a device-reported course
    table has translations at all before keying off it.

Reading it from the catalog instead of restating it in Python means adding a
state or a course table is a one-file change, and the two can't drift.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .const import DOMAIN

# Read at import, not lazily: custom integrations are imported in an executor
# thread, so this stays off the event loop no matter who asks first.
_ENTITY_CATALOG: dict[str, dict[str, dict]] = json.loads(
    (Path(__file__).parent / "translations" / "en.json").read_text(encoding="utf-8")
).get("entity", {})


def has_entity_translation(platform: str, translation_key: str) -> bool:
    """Whether `platform`.`translation_key` has an entry in the catalog."""
    return translation_key in _ENTITY_CATALOG.get(platform, {})


def translated_states(platform: str, translation_key: str) -> frozenset[str]:
    """The state keys translated for `platform`.`translation_key`.

    Empty when the entity has no translation at all, and also when it has a
    translated *name* but deliberately no state table (a course list whose
    codes we can't map, for instance) -- callers treat both the same way:
    leave the device's value untouched.
    """
    entry = _ENTITY_CATALOG.get(platform, {}).get(translation_key)
    return frozenset(entry.get("state", ())) if entry else frozenset()


_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def translation_state(value: str, known: frozenset[str]) -> str | None:
    """Normalize a vendor option only when the catalog contains that state."""
    direct = value.lower().replace(" ", "_")
    if direct in known:
        return direct
    snake = _CAMEL_BOUNDARY_RE.sub("_", value).lower().replace(" ", "_")
    return snake if snake in known else None


def translated_state_label(
    platform: str, translation_key: str, value: str, strings: dict[str, str]
) -> str:
    """Use the same state label in errors as in a select, with an English fallback."""
    state = translation_state(value, translated_states(platform, translation_key))
    if state is None:
        return value
    key = f"component.{DOMAIN}.entity.{platform}.{translation_key}.state.{state}"
    return strings.get(key, _ENTITY_CATALOG[platform][translation_key]["state"][state])
