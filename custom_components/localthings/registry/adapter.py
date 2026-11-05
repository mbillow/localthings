"""Adapter: BoundEntity list → flat state dict and command dispatch."""

from __future__ import annotations

from typing import Any

from .discovery import BoundEntity
from .subdevices import Subdevice, canonical_view


def _key(b: BoundEntity) -> str:
    # b.subdevice.key_prefix is '' for MAIN, so a device with no subdevices
    # (every device this integration shipped before issue #177) gets a
    # byte-identical key to before -- a hard regression guard, not a nicety
    # (see test_unique_ids.py and every golden file under tests/fixtures/golden/).
    return f"{b.subdevice.key_prefix}{b.key_override or b.desc.key}{b.instance}"


def flatten(bound: list[BoundEntity], resources: dict) -> dict[str, Any]:
    """Map bound entities to their current scalar values.

    `exists_fn(rep, resources)` receives that entity's own subdevice's
    *canonical* resources view (see subdevices.canonical_view), not the raw
    actual-href snapshot -- an exists_fn that scans the whole resources dict
    for a sibling href (e.g. is_legacy_board) must judge each subdevice on its
    own resources, not see another subdevice's hrefs bleed in under the same
    canonical key. Views are built once per distinct subdevice per call, not
    once per entity -- O(subdevices), not O(bound entities).
    """
    out: dict[str, Any] = {}
    # Subdevice is a frozen dataclass (hashable, equal by value), so it can key
    # `views` directly -- no need to re-derive an identity for it out of
    # (kind, key) first.
    all_subdevices = list(dict.fromkeys(b.subdevice for b in bound))
    views: dict[Subdevice, dict] = {}

    for b in bound:
        rep = resources.get(b.href) or {}
        if b.desc.exists_fn is not None:
            view = views.get(b.subdevice)
            if view is None:
                view = canonical_view(b.subdevice, resources, all_subdevices)
                views[b.subdevice] = view
            if not b.desc.exists_fn(rep, view):
                continue
        if b.desc.rep_fn is not None:
            out[_key(b)] = b.desc.rep_fn(rep)
        elif b.desc.field:
            if b.desc.resources_value_fn is not None:
                out[_key(b)] = b.desc.resources_value_fn(
                    rep.get(b.desc.field), resources
                )
            else:
                out[_key(b)] = b.desc.value_fn(rep.get(b.desc.field))
    return out
