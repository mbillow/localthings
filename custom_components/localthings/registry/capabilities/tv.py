"""Samsung TV mode settings, verified on a QE50QN90AATXXU (#388)."""

from collections import Counter

from ..capability import Capability
from ..entities import SelectDesc

PREFIX = "x.com.samsung.tv."
CURRENT_MODE = PREFIX + "currentMode"
SUPPORTED_MODES = PREFIX + "supportedModes"
HREF_INPUT = "/sec/tv/inputsource"
HREF_PICTURE = "/sec/tv/mode/picture"
HREF_SOUND = "/sec/tv/mode/sound"


def mode_options(rep: dict) -> dict[str, str]:
    """Keep wire IDs distinct even when two inputs have the same display name."""
    rows = rep.get(SUPPORTED_MODES)
    if not isinstance(rows, list):
        return {}
    options = {
        row[PREFIX + "id"]: row[PREFIX + "name"]
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get(PREFIX + "id"), str)
        and row[PREFIX + "id"]
        and isinstance(row.get(PREFIX + "name"), str)
        and row[PREFIX + "name"]
    }
    counts = Counter(options.values())
    labels = {
        code: f"{name} ({code})" if counts[name] > 1 else name for code, name in options.items()
    }
    # A device label may itself look like a disambiguated label. Fall back
    # to unique wire IDs rather than letting a select address the wrong input.
    return labels if len(set(labels.values())) == len(labels) else {code: code for code in options}


def _writable(rep: dict) -> bool:
    return "oic.if.a" in (rep.get("if") or ())


def _mode_write(value, rep, href=None):
    if (
        not href
        or not isinstance(value, str)
        or not _writable(rep)
        or value not in mode_options(rep)
    ):
        return None
    return href.strip("/").split("/"), {CURRENT_MODE: value}


def _mode_capability(href: str, key: str, icon: str) -> Capability:
    return Capability(
        href=href,
        poll_tier="warm",
        entities=(
            SelectDesc(
                key=key,
                field=CURRENT_MODE,
                icon=icon,
                entity_category="config",
                needs_remote_control=False,
                # This TV refuses a sound mode that is already selected.
                # Only a fresh matching GET may turn that refusal into success.
                confirm_on_refusal=True,
                options=lambda resources: list(mode_options(resources.get(href) or {})),
                display_fn=lambda raw, resources: mode_options(resources.get(href) or {}).get(
                    raw, raw
                ),
                exists_fn=lambda rep, resources: _writable(rep) and bool(mode_options(rep)),
                write_fn=_mode_write,
            ),
        ),
    )


INPUT = _mode_capability(HREF_INPUT, "tv_input", "mdi:video-input-hdmi")
PICTURE_MODE = _mode_capability(HREF_PICTURE, "tv_picture_mode", "mdi:palette")
SOUND_MODE = _mode_capability(HREF_SOUND, "tv_sound_mode", "mdi:surround-sound")
