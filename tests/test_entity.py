"""Tests for entity._is_included -- the one-time entity-creation gate run
per platform at async_setup_entry (see sensor.py etc.), separate from
adapter.flatten()'s per-poll state values. issue #127 review: this default
gate has to stay permissive on a genuinely empty {} rep, not just a true
is_stub_rep stub -- a resource like /alarms/vs/0 is validly empty in its
normal, no-alarm state (fridge._active_alarm_codes), so excluding the
entity there would silently drop working sensors on first-poll timing,
not just fix phantom ones.
"""

from typing import cast

import pytest

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities.common import ALARMS
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import SensorDesc
from tests.conftest import _load_device


class _FakeCoordinator:
    def __init__(self, last_resources):
        self.last_resources = last_resources

    def canonical_resources(self, subdevice):
        # Every bound entity in this test file uses the default MAIN
        # subdevice (identity transform), so the canonical view is just the
        # raw snapshot -- same shape as the real
        # LocalThingsCoordinator.canonical_resources for a device with no
        # subdevices (issue #177).
        return self.last_resources

    # _is_included judges existence against the discovery view, which is the
    # live cache for everything but an offline load (issue #295).
    @property
    def discovery_resources(self):
        return self.last_resources

    def discovery_canonical(self, subdevice):
        return self.canonical_resources(subdevice)


def _coord(last_resources) -> LocalThingsCoordinator:
    return cast(LocalThingsCoordinator, _FakeCoordinator(last_resources))


def _bound(desc, href):
    capability = Capability(href=href, entities=(desc,))
    return BoundEntity(href=href, capability=capability, desc=desc)


class TestDefaultFieldGate:
    """No explicit exists_fn -- the field-presence default in entity.py."""

    def _bound(self):
        return _bound(SensorDesc(key="x", field="x.com.samsung.da.value"), "/x/vs/0")

    def test_included_when_field_present(self):
        bound = self._bound()
        coord = _coord({"/x/vs/0": {"x.com.samsung.da.value": "1"}})
        assert _is_included(bound, coord) is True

    def test_excluded_when_field_absent_from_populated_rep(self):
        bound = self._bound()
        coord = _coord({"/x/vs/0": {"x.com.samsung.da.other": "1"}})
        assert _is_included(bound, coord) is False

    def test_included_on_true_stub(self):
        bound = self._bound()
        coord = _coord({"/x/vs/0": {"href": "/x/vs/0"}})
        assert _is_included(bound, coord) is True

    def test_included_on_genuinely_empty_rep(self):
        """The default gate stays permissive on a real {} -- see
        /alarms/vs/0, whose empty rep is the normal no-alarm state, not a
        signal the hardware is unsupported. Only a capability-specific
        exists_fn (e.g. common.ENERGY_METER) opts into excluding on
        confirmed-empty, after verifying that's actually safe for its field."""
        bound = self._bound()
        coord = _coord({"/x/vs/0": {}})
        assert _is_included(bound, coord) is True

    def test_excluded_when_href_missing_from_resources(self):
        bound = self._bound()
        coord = _coord({})
        assert _is_included(bound, coord) is False


class TestExplicitExistsFnGate:
    def test_explicit_exists_fn_overrides_default_field_gate(self):
        desc = SensorDesc(
            key="x",
            field="x.com.samsung.da.value",
            exists_fn=lambda rep, resources: rep.get("flag") is True,
        )
        bound = _bound(desc, "/x/vs/0")
        coord = _coord({"/x/vs/0": {"flag": True}})
        assert _is_included(bound, coord) is True
        coord = _coord({"/x/vs/0": {"flag": False, "x.com.samsung.da.value": "1"}})
        assert _is_included(bound, coord) is False


class TestNoFieldEntities:
    def test_rep_fn_entity_always_included(self):
        desc = SensorDesc(key="x", rep_fn=lambda rep: rep.get("x.com.samsung.da.value"))
        bound = _bound(desc, "/x/vs/0")
        coord = _coord({"/x/vs/0": {}})
        assert _is_included(bound, coord) is True


def _alarm_bound():
    return BoundEntity(href="/alarms/vs/0", capability=ALARMS, desc=ALARMS.entities[0])


@pytest.mark.parametrize(
    "rep",
    [
        pytest.param({}, id="empty-clear"),
        pytest.param({"href": "/alarms/vs/0"}, id="stub"),
        pytest.param({"rt": ["x.com.samsung.da.alarms"]}, id="metadata-only-clear"),
        pytest.param({"x.com.samsung.da.items": []}, id="no-items"),
        pytest.param(
            {
                "x.com.samsung.da.items": [
                    {"x.com.samsung.da.code": "ErrorCode_X", "x.com.samsung.da.state": "Deleted"}
                ]
            },
            id="deleted",
        ),
        pytest.param(
            {"x.com.samsung.da.items": [{"x.com.samsung.da.code": "ErrorCode_OFF"}]}, id="off"
        ),
    ],
)
def test_clear_alarm_shapes_read_none(rep):
    bound = _alarm_bound()
    assert _is_included(bound, _coord({"/alarms/vs/0": rep})) is True
    assert flatten([bound], {"/alarms/vs/0": rep})["alarm_code"] == "none"


def test_alarm_sensor_keeps_active_and_deleted_semantics():
    bound = _alarm_bound()
    resources = {
        "/alarms/vs/0": {
            "x.com.samsung.da.items": [
                {"x.com.samsung.da.code": "DoorA_Opened"},
                {
                    "x.com.samsung.da.code": "ErrorCode_OLD",
                    "x.com.samsung.da.state": "Deleted",
                },
                {"x.com.samsung.da.code": "ErrorCode_OFF"},
                "not-a-row",
            ]
        }
    }
    assert _is_included(bound, _coord(resources)) is True
    assert flatten([bound], resources)["alarm_code"] == "DoorA_Opened"


def test_missing_alarm_resource_is_not_registered():
    assert _is_included(_alarm_bound(), _coord({})) is False


@pytest.mark.parametrize(
    "fixture",
    [
        "refrigerator_tp1x_ref_21k_us",  # clear as {}
        "refrigerator_tp1x_ref_21k_eu",  # clear as OCF metadata, no items
        "refrigerator_tp1x_ref_21k_definite",
        "range_hood",
    ],
)
def test_alarm_sensor_reads_none_on_clear_fixtures(fixture):
    resources = _load_device(fixture)
    bound = _alarm_bound()
    assert _is_included(bound, _coord(resources)) is True
    assert flatten([bound], resources)["alarm_code"] == "none"
