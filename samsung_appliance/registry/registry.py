"""Global CAPABILITIES registry: href -> Capability.

Built from the full list of Capability objects in the capabilities package.
Consumed by discover() at connection time to bind device resources to entities.

Raises ValueError at import if any href appears in multiple capabilities.
"""
from .capabilities import ALL
from .capability import Capability


def _build() -> dict[str, Capability]:
    """Build the registry, raising ValueError if any href is duplicated."""
    out: dict[str, Capability] = {}
    for cap in ALL:
        if cap.href in out:
            raise ValueError(f"duplicate capability href: {cap.href}")
        out[cap.href] = cap
    return out


CAPABILITIES: dict[str, Capability] = _build()
