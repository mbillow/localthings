"""Tests for the TP2X_DA-KS-COOKTOP-000001 NV8000T induction cooktop (issue #314).

The reporter's actual complaint (six advertised burner slots when only
three are physically present) is expected: the registry declares a
generous static superset of slots and gates each on the live options array
per cooktop.py's own comment; there's no per-device signal that
distinguishes an advertised-but-nonexistent slot from a real one, so the
extra entities are disabled by the user, not filtered by the integration.
This file locks in the actual gap from that issue -- /alarms/vs/0 and
/kidslock/vs/0 -- now bound via common.ALARMS/KIDS_LOCK_VS_FALLBACK.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device


def _bound():
    resources = _load_device("cooktop_nv8000t")
    reg = resolve(resources)
    assert reg is not None
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def test_resolves_to_cooktop_registry():
    resources = _load_device("cooktop_nv8000t")
    reg = resolve(resources)
    assert reg is not None and reg.name == "cooktop"


def test_no_unbound_hrefs():
    _, resources = _bound()
    reg = resolve(resources)
    assert reg is not None
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_alarm_code_and_child_lock_bound():
    bound, resources = _bound()
    state = flatten(bound, resources)
    assert state["alarm_code"] == "CT_E"
    # x.com.samsung.da.kidsLock == "Ready" reads as unlocked, same polarity
    # as every other board KIDS_LOCK_VS_FALLBACK covers.
    assert state["child_lock"] is True


# The reporter's original complaint -- six advertised burner slots when
# only three are physically present -- isn't tested here beyond what the
# golden regression test already locks in (burner_0_state..burner_5_state
# all present). It's expected, not a bug: the registry declares a generous
# static superset of slots per cooktop.py's own comment, and this board's
# /mode/vs/0 rep carries no field distinguishing a real slot from an
# advertised-but-nonexistent one for a test to assert against -- the user
# disabling the three extra entities is the correct fix, not something the
# integration can filter automatically.
