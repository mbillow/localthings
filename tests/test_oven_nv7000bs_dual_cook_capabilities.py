"""Coverage for the NV7000BS/EUB Dual Cook wall oven (TP1X_DA-KS-OVEN-0107X).

Two cavities over one connection (Pattern A, indexed siblings). The lower
cavity answers `/mode/vs/1` with only `Lower*` supportedModes: no defaultMode,
no modeSpec of its own, and no description to read a `_DIV` suffix from. Its
mode list is the only thing that says which cavity it is.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.subdevices import MAIN, canonical_view
from tests.conftest import _discover_full, _load_device_full

FIXTURE = "oven_nv7000bs_dual_cook"
DEVICE_TYPES = ("oic.wk.d", "oic.d.oven")


def _pipeline():
    resources, oic_res, seeds = _load_device_full(FIXTURE)
    return _discover_full(resources, oic_res, seeds, DEVICE_TYPES), resources


def _views():
    (bound, materialized, _skipped, full, _name), _resources = _pipeline()
    (lower,) = materialized
    return (
        bound,
        lower,
        canonical_view(MAIN, full, materialized),
        canonical_view(lower, full, materialized),
    )


def test_resolves_to_oven_and_finds_the_second_cavity():
    (_bound, materialized, skipped, _full, name), _resources = _pipeline()
    assert name == "oven"
    assert [(s.kind, s.key) for s in materialized] == [("indexed", "1")]
    assert skipped == []


def test_every_href_is_bound():
    (_bound, materialized, _skipped, full, _name), resources = _pipeline()
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None
    gaps: list[str] = []
    for sub in (MAIN, *materialized):
        discover(
            canonical_view(sub, full, materialized),
            reg.capabilities,
            reg.pattern_capabilities,
            log=gaps.append,
            subdevice=sub,
        )
    assert gaps == []


def test_the_lower_cavity_is_named_by_its_mode_list_and_the_upper_is_not():
    _bound, _lower, upper_view, lower_view = _views()
    assert cook.cavity(lower_view) == "Lower"
    assert cook.cavity(upper_view) is None


def test_the_lower_cavity_gets_the_divider_sensor_not_a_connectivity_one():
    (bound, _materialized, _skipped, full, _name), _resources = _pipeline()
    state = flatten(bound, full)
    assert "subdevice1_divider" in state
    assert "subdevice1_cloud_connected" not in state
    assert "cloud_connected" in state
    assert "divider" not in state


def test_the_divider_reads_out_on_the_captured_oven():
    """The capture was taken idle with `/connected/vs/1` = Off: divider removed."""
    (bound, _materialized, _skipped, full, _name), _resources = _pipeline()
    assert flatten(bound, full)["subdevice1_divider"] is False


def test_the_divider_reads_in_and_the_upper_cavity_narrows_when_it_is_inserted():
    """The second capture, idle with `/connected/vs/1` = On: the upper cavity
    then lists only Upper* modes and says so itself, and the lower is unchanged."""
    resources, oic_res, seeds = _load_device_full("oven_nv7000bs_dual_cook_divided")
    bound, materialized, skipped, full, _name = _discover_full(
        resources, oic_res, seeds, DEVICE_TYPES
    )
    assert skipped == []
    assert flatten(bound, full)["subdevice1_divider"] is True
    assert cook.cavity(canonical_view(MAIN, full, materialized)) == "Upper"
    assert cook.cavity(canonical_view(materialized[0], full, materialized)) == "Lower"
