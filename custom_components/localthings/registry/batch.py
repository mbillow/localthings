"""OCF /device/0 batch response parser."""

from __future__ import annotations


def is_stub_rep(rep: dict) -> bool:
    """True for the device's "resource exists, no data fetched yet" marker --
    an echoed {"href": "..."} with no other fields.

    Distinct from a genuinely empty {} rep, which is the device's confirmed
    (if empty) answer -- e.g. an unsupported resource on this model that will
    never populate. Conflating the two used to make every field-gated entity
    on a permanently-empty resource look like a not-yet-fetched stub forever,
    creating phantom always-"unknown" entities (issue #127)."""
    return isinstance(rep, dict) and set(rep.keys()) == {"href"}


def parse_device0_batch(device0: list) -> dict[str, dict]:
    """Extract {href: rep} from a /device/0 CBOR list response.

    Most devices put a collection representation without an ``href`` at
    index 0, while some firmware starts directly with resource entries.
    Iterate the whole list and let the existing href check ignore collection
    metadata so the first real resource is preserved in either shape. The
    MIM-H04EN Wi-Fi kit (#682) lists the collection itself with an href; it
    is skipped by its `oic.wk.col` type, since it is no resource of its own.

    A stub rep is passed through unchanged rather than collapsed to {} --
    downstream code (entity._is_included, capability exists_fns) uses
    is_stub_rep to tell "not fetched yet" apart from a confirmed-empty {}.
    """
    out = {}
    for entry in device0:
        if not isinstance(entry, dict):
            continue
        href = entry.get("href")
        rep = entry.get("rep")
        if not href:
            continue
        if isinstance(rep, dict) and "oic.wk.col" not in (rep.get("rt") or ()):
            out[href] = rep
    return out
