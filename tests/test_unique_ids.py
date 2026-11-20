"""Corpus-wide invariant: adapter._key must be unique across every bound
entity that would actually be registered as an HA entity, for every fixture
in the corpus -- issue #177's whole Subdevice/key_prefix design exists to
protect this (see DESIGN-177.md section 3/6). Run over the entire fixture
set, not just the two new subdevice fixtures, so a future dump -- subdevice-
capable or not -- exercises it automatically.
"""

from collections import Counter
from typing import cast

import pytest

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included, entity_unique_id
from custom_components.localthings.registry.adapter import _key
from custom_components.localthings.registry.entities import PLATFORM_OF
from tests.conftest import FIXTURES, _discover_full, _load_device_full

_FIXTURE_NAMES = sorted(p.name[: -len("_device.json")] for p in FIXTURES.glob("*_device.json"))


class _FakeCoordinator:
    """Just enough of LocalThingsCoordinator's surface for entity.py's
    _is_included -- the same one-time entity-creation gate every platform's
    async_setup_entry runs (see entity.py's own module docstring)."""

    def __init__(self, resources: dict[str, dict], subdevices):
        self.last_resources = resources
        self._subdevices = list(subdevices)

    def canonical_resources(self, subdevice):
        from custom_components.localthings.registry.subdevices import canonical_view

        return canonical_view(subdevice, self.last_resources, self._subdevices)

    # _is_included judges existence against the discovery view, which is the
    # live cache for everything but an offline load (issue #295).
    @property
    def discovery_resources(self):
        return self.last_resources

    def discovery_canonical(self, subdevice):
        return self.canonical_resources(subdevice)


@pytest.mark.parametrize("name", _FIXTURE_NAMES)
def test_key_is_unique_across_all_bound_entities(name):
    """`_key(b)` only has to be unique among entities `_is_included` would
    actually register -- discover() alone can (deliberately) produce two
    BoundEntity rows sharing a key on the same href when they're gated by
    mutually-exclusive `exists_fn`s (see fridge.py's
    REFRIGERATION_FALLBACK.defrost_active, which only exists when
    DEFROST_BLOCK_STATUS's own `/defrost/block/vs/0` href is absent) --
    only one of the two is ever actually included for a real device, so
    checking the raw `bound` list would flag devices that have always
    worked correctly. It also only has to be unique *within one HA
    platform* -- unique_id collisions are scoped by (integration, platform)
    in HA's entity registry (see entity.py's `_attr_unique_id`, which
    doesn't itself encode the platform), and several devices in this corpus
    deliberately bind the same key to the same href on two different
    platforms discriminated the same way (e.g. a SwitchDesc and a
    BinarySensorDesc both named 'power_switch' on /power/0).
    """
    resources, oic_res, seeds = _load_device_full(name)
    bound, materialized, _skipped, full_resources, _device_type_name = _discover_full(
        resources,
        oic_res,
        seeds,
    )
    coordinator = cast(LocalThingsCoordinator, _FakeCoordinator(full_resources, materialized))
    included = [b for b in bound if _is_included(b, coordinator)]
    for entity in included:
        assert entity_unique_id("test-device", entity) == f"{DOMAIN}_test-device_{_key(entity)}"
    keys = [(PLATFORM_OF[type(b.desc)], _key(b)) for b in included]
    dupes = {k: n for k, n in Counter(keys).items() if n > 1}
    assert not dupes, (
        f"{name}: duplicate (platform, _key) values across {len(included)} "
        f"included entities (materialized subdevices: "
        f"{[su.key for su in materialized]}): {dupes}"
    )


def test_subdevice_capable_fixtures_actually_exercise_a_subdevice():
    """A meta-check on the test above: if both subdevice fixtures somehow
    stopped materializing any subdevice (a regression in enumeration or the
    materialization gate), the corpus-wide uniqueness test above would keep
    passing vacuously -- it never gets to check a single collision. Assert
    the two fixtures this design added actually produce a materialized
    subdevice, so that silent-vacuous-pass failure mode is caught here
    instead."""
    for name in ("airconditioner_artik051_dongle_fac_18k", "airconditioner_fac_bora_2in1"):
        resources, oic_res, seeds = _load_device_full(name)
        _, materialized, _, _, _ = _discover_full(resources, oic_res, seeds)
        assert materialized, f"{name}: expected at least one materialized subdevice"
