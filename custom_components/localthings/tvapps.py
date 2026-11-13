"""Remember TV apps observed in contentinfo and launch them by name or id.

On the UE50TU7172, contentinfo reports an app's name/id as title/subtitle.
Built-in screens require targetAppName; installed apps use targetAppId.
Known apps persist on the entry and merge into the launchapp rep under
FIELD for entity reads, without writing that synthetic field to the TV.
"""

from __future__ import annotations

import threading

from .const import CONF_KNOWN_APPS

LAUNCH_HREF = "/sec/tv/launchapp"
CONTENT_HREF = "/sec/tv/contentinfo"
TARGET_APP_FIELD = "x.com.samsung.tv.targetAppId"
TARGET_NAME_FIELD = "x.com.samsung.tv.targetAppName"
NATIVE_APPS_FIELD = "x.com.samsung.tv.supportedNativeApps"
# Synthetic: {"apps": {app_id: name}, "current": app_id | None}.
FIELD = "x.com.localthings.knownApps"

_INPUT = "x.com.samsung.tv.input"
_TITLE = "x.com.samsung.tv.title"
_SUBTITLE = "x.com.samsung.tv.subtitle"


def running_app(rep: dict) -> tuple[str, str] | None:
    """(app_id, name) of the app in front, from a contentinfo rep."""
    if rep.get(_INPUT) != "webapp":
        return None
    app_id, name = rep.get(_SUBTITLE), rep.get(_TITLE)
    if not isinstance(app_id, str) or not isinstance(name, str):
        return None
    app_id, name = app_id.strip(), name.strip()
    # An id is one token; a title-like subtitle is not an app.
    if not app_id or not name or any(c.isspace() for c in app_id):
        return None
    return app_id, name


def _coerce(stored) -> dict[str, str]:
    """The persisted map, dropping anything this module doesn't write."""
    if not isinstance(stored, dict):
        return {}
    return {
        app_id: name
        for app_id, name in stored.items()
        if isinstance(app_id, str) and app_id and isinstance(name, str) and name
    }


def persist(hass, entry, apps: dict[str, str]) -> None:
    """Write `apps` onto the entry. Runs on the event loop."""
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_KNOWN_APPS: apps})


class KnownApps:
    """app_id -> name for every app the TV has been seen running."""

    def __init__(self, stored=None) -> None:
        self._lock = threading.Lock()
        self._apps = _coerce(stored)

    def observe(self, rep: dict) -> tuple[str, str] | None:
        """Record the running app; returns it when it is new or renamed."""
        seen = running_app(rep)
        if seen is None:
            return None
        app_id, name = seen
        with self._lock:
            if self._apps.get(app_id) == name:
                return None
            self._apps[app_id] = name
        return seen

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._apps)

    def clear(self) -> None:
        with self._lock:
            self._apps.clear()


def view(apps: dict[str, str], content_rep: dict | None) -> dict:
    """The FIELD value: known apps plus which one is in front."""
    seen = running_app(content_rep or {})
    return {"apps": dict(apps), "current": seen[0] if seen else None}


def _native_apps(rep: dict) -> list[str]:
    return [
        name.strip()
        for name in rep.get(NATIVE_APPS_FIELD) or []
        if isinstance(name, str) and name.strip()
    ]


def app_names(rep: dict) -> list[str]:
    """What the Open app select offers, from a merged /sec/tv/launchapp rep:
    the built-in screens in the TV's own order, then known apps sorted."""
    apps = (rep.get(FIELD) or {}).get("apps") or {}
    names: dict[str, str] = {}
    for name in [*_native_apps(rep), *sorted(apps.values(), key=str.casefold)]:
        if name := name.strip():
            names.setdefault(name.casefold(), name)
    return list(names.values())


def current_name(rep: dict) -> str | None:
    """The app in front, spelled as app_names offers it."""
    known = rep.get(FIELD) or {}
    name = (known.get("apps") or {}).get(known.get("current"))
    if name is None:
        return None
    folded = name.strip().casefold()
    return next((n for n in app_names(rep) if n.casefold() == folded), None)


def _looks_like_id(value: str) -> bool:
    """A store number (3201512006963) or a Tizen package id (kIciSQlYEM.plex)."""
    return not any(c.isspace() for c in value) and (value.isdigit() or "." in value)


def launch_body(rep: dict, value: str) -> dict:
    """The launch POST for `value`.

    A known app, by name (case-insensitive) or id, launches by its id; so does
    anything shaped like an id. Everything else -- the built-in screens, or an
    app named but never seen -- launches by name, which the TV resolves itself.
    """
    apps = (rep.get(FIELD) or {}).get("apps") or {}
    wanted = value.strip()
    if wanted in apps:
        return {TARGET_APP_FIELD: wanted}
    folded = wanted.casefold()
    known = next(
        (app_id for app_id, name in apps.items() if name.strip().casefold() == folded), None
    )
    if known is not None:
        return {TARGET_APP_FIELD: known}
    if _looks_like_id(wanted):
        return {TARGET_APP_FIELD: wanted}
    native = next((n for n in _native_apps(rep) if n.casefold() == folded), wanted)
    return {TARGET_NAME_FIELD: native}


def launch_write(value: str, rep: dict) -> tuple[list[str], dict]:
    return ["sec", "tv", "launchapp"], launch_body(rep, value)
