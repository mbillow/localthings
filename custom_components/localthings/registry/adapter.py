"""Adapter: BoundEntity list → flat state dict and command dispatch."""
from __future__ import annotations

from typing import Any

from .discovery import BoundEntity


def _key(b: BoundEntity) -> str:
    return f"{b.key_override or b.desc.key}{b.instance}"


def flatten(bound: list[BoundEntity], resources: dict) -> dict[str, Any]:
    """Map bound entities to their current scalar values."""
    out: dict[str, Any] = {}
    for b in bound:
        rep = resources.get(b.href) or {}
        if b.desc.exists_fn is not None and not b.desc.exists_fn(rep, resources):
            continue
        if b.desc.rep_fn is not None:
            out[_key(b)] = b.desc.rep_fn(rep)
        elif b.desc.field:
            out[_key(b)] = b.desc.value_fn(rep.get(b.desc.field))
    return out
