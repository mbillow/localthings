"""Shared sensor helpers.

Appliance-specific flattening lives in samsung_appliance/appliances/*.py;
this module only carries utilities that every descriptor uses (currently
the /device/0 link-dict indexer).
"""


def index_links(device0_body):
    """Turn the /device/0 CBOR list-of-{href, rep} into a dict keyed
    by href. The first list entry is the device-level rep itself and
    isn't useful here, so skip it."""
    out = {}
    if not isinstance(device0_body, list):
        return out
    for entry in device0_body[1:]:
        if isinstance(entry, dict) and 'href' in entry:
            out[entry['href']] = entry.get('rep') or {}
    return out
