"""OCF /device/0 batch response parser."""
from __future__ import annotations


def parse_device0_batch(device0: list) -> dict[str, dict]:
    """Extract {href: rep} from a /device/0 CBOR list response."""
    out = {}
    for entry in device0[1:]:   # skip [0] (device-level rep)
        if not isinstance(entry, dict):
            continue
        href = entry.get('href')
        rep  = entry.get('rep')
        if not href:
            continue
        # rep == {"href": "..."} is a stub (resource present, no current data).
        # Include it as {} so capabilities still bind and the entity exists.
        if isinstance(rep, dict):
            out[href] = {} if set(rep.keys()) == {'href'} else rep
    return out
