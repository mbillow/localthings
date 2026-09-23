"""RS80F66KCFEF French-door fridge, AILITE_REF_25K board (issue #495).

Three /settings/sound/*/vs/0 hrefs were unbound; they share the water
purifier's shapes and now bind through those capabilities. The board also
reports Auto Door Open with a two-mode ado.mode (EASY_OPEN/WIDE_OPEN) and
per-compartment /autodoor/{cooler,freezer}/vs/0 open-option lists.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import fridge
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc
from tests.conftest import _load_device

FIXTURE = "refrigerator_ailite_ref_25k"


def _fridge():
    resources = _load_device(FIXTURE)
    return resolve(resources, device_types=("oic.wk.d", "oic.d.refrigerator")), resources


def _state():
    reg, resources = _fridge()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_resolves_to_refrigerator_registry():
    reg, _ = _fridge()
    assert reg is not None and reg.name == "refrigerator"


def test_resolves_to_refrigerator_registry_by_board_token_alone():
    resources = _load_device(FIXTURE)
    reg = resolve(resources)
    assert reg is not None and reg.name == "refrigerator"


def test_no_unbound_hrefs():
    reg, resources = _fridge()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_sound_settings_read_live_values():
    state = _state()
    assert state["sound_mode"] == "voice"
    assert state["sound_output"] == "speaker"
    assert state["alarm_in_mute"] is True
    assert state["sound_volume"] == 10


def test_fridge_sound_gated_off_without_device_sound_field():
    """This board's /status/lock/vs/0 has no device.sound -- its sound is
    the /settings/sound/* surface above, not a phantom always-off switch."""
    assert "fridge_sound" not in _state()


def test_auto_door_mode_reads_and_writes_status_lock():
    state = _state()
    assert state["auto_door_opener"] is True
    assert state["auto_door_mode"] == "WIDE_OPEN"
    desc = next(
        e
        for e in fridge.STATUS_LOCK.entities
        if e.key == "auto_door_mode" and isinstance(e, SelectDesc)
    )
    assert desc.options_field == "x.com.samsung.da.ado.supportedModes"
    assert desc.write_fn is not None
    assert desc.write_fn("EASY_OPEN", {}) == (
        ["status", "lock", "vs", "0"],
        {"x.com.samsung.da.ado.mode": "EASY_OPEN"},
    )


def test_auto_door_mode_absent_without_supported_modes():
    """Earlier auto-door boards report ado.devicecontrol but no ado.mode."""
    resources = _load_device("refrigerator_tp1x_ref_21k_autodoor")
    reg = resolve(resources)
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    assert "auto_door_mode" not in flatten(bound, resources)


def test_compartment_autodoor_hrefs_are_coverage_only():
    _reg, resources = _fridge()
    assert fridge.AUTO_DOOR_VARIANT.match_fn is not None
    for href in ("/autodoor/cooler/vs/0", "/autodoor/freezer/vs/0"):
        assert fridge.AUTO_DOOR_VARIANT.match_fn(resources[href], resources)
