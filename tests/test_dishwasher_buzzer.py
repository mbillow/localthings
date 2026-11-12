"""Issue #821: a DA_DW_TP1_21_COMMON dishwasher's /buzzersound/vs/0."""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc
from tests.conftest import _load_device

HREF = "/buzzersound/vs/0"
# As the DW80H73WBI0U1 in #821 reports it.
_REP = {
    "rt": ["x.com.samsung.da.buzzersound"],
    "if": ["oic.if.a", "oic.if.baseline"],
    "supportedBuzzerSound": ["Volume_Off", "Volume_Low", "Volume_Med", "Volume_High"],
    "setBuzzerSound": "Volume_Med",
}


def _bound():
    resources = {**_load_device("dishwasher"), HREF: _REP}
    reg = resolve(resources)
    assert reg is not None and reg.name == "dishwasher"
    unbound: list[str] = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return bound, resources, unbound


def test_buzzer_volume_is_a_select_and_no_longer_a_coverage_gap():
    bound, resources, unbound = _bound()
    assert HREF not in unbound
    state = flatten(bound, resources)
    assert state["buzzer_sound"] == "Volume_Med"
    assert "buzzer_sound_enabled" not in state
    assert "finish_sound" not in state


def test_buzzer_volume_writes_the_chosen_level():
    bound, resources, _ = _bound()
    desc = next(b.desc for b in bound if b.desc.key == "buzzer_sound")
    assert isinstance(desc, SelectDesc) and desc.write_fn is not None
    assert desc.write_fn("Volume_Off", resources[HREF]) == (
        ["buzzersound", "vs", "0"],
        {"setBuzzerSound": "Volume_Off"},
    )
