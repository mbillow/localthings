import json
from pathlib import Path
from typing import Any, cast

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _resources_from_dump(dump: dict) -> dict[str, dict]:
    from custom_components.localthings.registry.batch import parse_device0_batch

    return parse_device0_batch(dump["device0"])


def _load_device(name: str) -> dict[str, dict]:
    data = json.loads((FIXTURES / f"{name}_device.json").read_text())
    return _resources_from_dump(data)


def _load_device_full(name: str):
    """Like _load_device, but also returns the optional `oic_res`/`seeds`
    keys a subdevice-capable fixture (issue #177) may carry alongside
    `device0` -- see the two `airconditioner_*` fixtures with a
    `seeds_note` field. `oic_res`/`seeds` default to `[]`/`{}` for every
    other fixture, so this is safe to call on any fixture in the corpus.

    Returns `(resources, oic_res, seeds)` where `seeds` is
    `{seed_href: raw_batch_list}` -- the same [devcol-rep, {href, rep}, ...]
    shape a real /device/<n> or /<id>/device/0 RETRIEVE returns, ready to
    hand to a FakeCoapSession.

    A fixture's optional `probes` map (plain Property-map resources that
    belong to no batch, e.g. the hand-read /multidevice/vs/0 in the
    ARTIK051_DONGLE_FAC_18K fixture) is folded into `seeds` here, since
    FakeCoapSession answers both shapes off the same href key.

    `probes` runs through `from_json_safe`, so an entry may carry a binary
    rep as the marker `read_resource`/diagnostics emit for one (see
    registry/encode.py) and the fake session hands the caller real `bytes`,
    exactly as a device would.
    """
    from custom_components.localthings.registry.encode import from_json_safe

    data = json.loads((FIXTURES / f"{name}_device.json").read_text())
    resources = _resources_from_dump(data)
    seeds = {**data.get("seeds", {}), **from_json_safe(data.get("probes", {}))}
    return resources, data.get("oic_res", []), seeds


def linked_parent(hass, info) -> tuple[str, str] | None:
    """The identifier of the device `info` links under, or None if unlinked.

    device_info_for names the parent by identifier or by registry id
    depending on the core (see custom_components/localthings/devices.py);
    reading it back through the registry keeps a test asserting the link
    itself rather than which spelling the installed HA wanted.
    """
    from homeassistant.helpers import device_registry as dr

    raw = cast("dict[str, Any]", info)
    if (parent := raw.get("via_device")) is not None:
        return parent
    if (device_id := raw.get("via_device_id")) is None:
        return None
    row = dr.async_get(hass).async_get(device_id)
    return next(iter(row.identifiers)) if row is not None else None


class FakeCoapSession:
    """Minimal stand-in for a Transport, backed by a fixture's `seeds` map
    (raw device0-batch-shaped lists keyed by seed href -- plus any `probes`
    entries, which are plain Property maps rather than batch lists; a
    transport hands both back decoded, and the two readers in
    registry.subdevices already type-check what they get back). Enough
    surface for registry.subdevices.enumerate_subdevices and
    LocalThingsCoordinator's blocking subdevice polls to run against
    fixture data without a live device -- same idea as test_identity.py's
    FakeSession, but keyed by href string (post path-join) rather than a
    path tuple, since callers here pass a `seed_path` tuple straight
    through.
    """

    def __init__(self, seeds: dict[str, list] | None = None):
        self.seeds = seeds or {}

    def read(self, path, timeout=None):
        href = "/" + "/".join(path)
        body = self.seeds.get(href)
        if body is None:
            return 0x84, None  # 4.04 not found -- tolerated absence
        return 0x45, body

    def pace(self):
        pass


def _discover_full(resources: dict[str, dict], oic_res, seeds: dict[str, list], device_types=()):
    """Run the *whole* subdevice-aware discovery pipeline against fixture
    data, HA-free -- mirrors exactly what LocalThingsCoordinator does across
    _enumerate_subdevices_blocking + _run_discovery (issue #177), so a test
    exercising this exercises the real code path, not a re-implementation of
    it. See the adding-device-support skill's section 2 for the plain
    (non-subdevice) equivalent this extends.

    `device_types` is the master's own /oic/d `rt` (see
    discover_partitioned's `oic_device_types` param) -- only needed for a
    board with no /information/vs/0 at all to route from (issue #324's
    range, whose modelNum-based fallback has nothing to read), so it
    defaults to () for every fixture that resolves by board token instead.

    Returns `(bound, materialized, skipped, full_resources, device_type_name)`:
    - `bound`: every BoundEntity, main + every materialized subdevice.
    - `materialized`/`skipped`: Subdevice / SkippedSubdevice lists straight from
      discover_partitioned.
    - `full_resources`: `resources` merged with every candidate's seed data
      (actual hrefs) -- what a coordinator's cache would hold.
    - `device_type_name`: the master's resolved registry name.
    """
    from custom_components.localthings.registry.by_type import resolve
    from custom_components.localthings.registry.registry import CAPABILITIES
    from custom_components.localthings.registry.subdevices import (
        discover_partitioned,
        enumerate_subdevices,
    )

    sess = FakeCoapSession(seeds)
    candidates, extra = enumerate_subdevices(sess, resources, oic_res)
    full_resources = {**resources, **extra}
    bound, device_type_name, materialized, skipped = discover_partitioned(
        full_resources,
        candidates,
        resolve,
        CAPABILITIES,
        oic_device_types=device_types,
    )
    return bound, materialized, skipped, full_resources, device_type_name


def _load_resources(ip: str) -> dict[str, dict]:
    """Legacy IP-based loader — maps known IPs to named fixtures."""
    _ip_to_name = {
        "10.0.0.129": "dishwasher",
        "10.0.0.254": "refrigerator",
    }
    name = _ip_to_name.get(ip)
    if name is None:
        raise ValueError(f"No fixture for IP {ip!r} — add a scrubbed fixture to tests/fixtures/")
    return _load_device(name)


@pytest.fixture
def dishwasher_resources() -> dict[str, dict]:
    return _load_device("dishwasher")


@pytest.fixture
def fridge_resources() -> dict[str, dict]:
    return _load_device("refrigerator")


@pytest.fixture
def washer_resources() -> dict[str, dict]:
    return _load_device("washer")


@pytest.fixture
def all_device_fixtures() -> dict[str, dict[str, dict]]:
    """Every scrubbed device dump, keyed by fixture name.

    For invariants that must hold across the whole corpus rather than for one
    device -- so a newly added dump exercises them automatically.
    """
    return {
        path.name[: -len("_device.json")]: _resources_from_dump(json.loads(path.read_text()))
        for path in sorted(FIXTURES.glob("*_device.json"))
    }
