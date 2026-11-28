"""Regression for a live wall unit whose collection endpoint is silent."""

import unittest
from unittest.mock import Mock, patch

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.identity import DeviceIdentity
from custom_components.localthings.registry.subdevices import Subdevice, enumerate_subdevices

SUB = "00000000-0000-4000-8000-000000000001"


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now


class PropertyOnlyTransport:
    def __init__(self, clock, live=True):
        self.clock = clock
        self.live = live
        self.reads = []

    def pace(self):
        pass

    def read(self, path, timeout):
        href = "/" + "/".join(path)
        self.reads.append(href)
        if self.live and href in (
            "/" + SUB + "/mode/vs/0",
            "/" + SUB + "/power/0",
            "/" + SUB + "/information/vs/0",
        ):
            self.clock.now += 0.1
            return 69, {"value": False} if href.endswith("/power/0") else {
                "model": "wall",
                "mode": "Cool",
            }
        self.clock.now += timeout
        raise TimeoutError()


class SubdeviceBudgetTests(unittest.TestCase):
    def resources(self):
        return {
            "/subdevices/vs/0": {"x.com.samsung.da.subdeviceIdList": [SUB]},
            "/information/vs/0": {"x.com.samsung.da.modelNum": "ARTIK051_FAC_TIME_21K"},
            "/power/0": {"value": True},
            "/mode/vs/0": {"mode": "Dry"},
        }

    def test_silent_collection_cannot_hide_live_wall_unit(self):
        clock = Clock()
        transport = PropertyOnlyTransport(clock)
        with patch(
            "custom_components.localthings.registry.subdevices.time.monotonic", clock.monotonic
        ):
            subs, extra = enumerate_subdevices(
                transport,
                self.resources(),
                [],
                preferred_hrefs=("/mode/vs/0", "/power/0"),
                time_budget=5,
                collection_timeout=10,
                property_timeout=1,
            )
        self.assertEqual(len(subs), 1)
        self.assertIn("/mode/vs/0", subs[0].flat_hrefs)
        self.assertEqual(extra["/" + SUB + "/power/0"], {"value": False})
        self.assertNotIn("/" + SUB + "/device/0", transport.reads)

    def test_advertised_working_collection_remains_authoritative(self):
        clock = Clock()
        transport = PropertyOnlyTransport(clock)
        original = transport.read

        def read(path, timeout):
            if path == [SUB, "device", "0"]:
                transport.reads.append("/" + SUB + "/device/0")
                return 69, [{"href": "/mode/vs/0", "rep": {"mode": "Cool"}}]
            return original(path, timeout)

        with (
            patch.object(transport, "read", read),
            patch(
                "custom_components.localthings.registry.subdevices.time.monotonic", clock.monotonic
            ),
        ):
            subs, extra = enumerate_subdevices(
                transport,
                self.resources(),
                [{"href": "/" + SUB + "/device/0"}],
                preferred_hrefs=("/mode/vs/0",),
                time_budget=5,
                collection_timeout=10,
                property_timeout=1,
            )
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].seed_path, (SUB, "device", "0"))
        self.assertEqual(transport.reads[0], "/" + SUB + "/device/0")
        self.assertEqual(extra["/" + SUB + "/mode/vs/0"], {"mode": "Cool"})

    def test_other_firmware_keeps_working_unadvertised_collection(self):
        clock = Clock()
        transport = PropertyOnlyTransport(clock)
        original = transport.read

        def read(path, timeout):
            if path == [SUB, "device", "0"]:
                transport.reads.append("/" + SUB + "/device/0")
                if timeout < 2:
                    clock.now += timeout
                    raise TimeoutError()
                clock.now += 2
                return 69, [{"href": "/child-only/vs/0", "rep": {"value": "unique"}}]
            return original(path, timeout)

        resources = self.resources()
        resources["/information/vs/0"] = {"x.com.samsung.da.modelNum": "TP2X_FAC_BORA_21K"}
        with (
            patch.object(transport, "read", read),
            patch(
                "custom_components.localthings.registry.subdevices.time.monotonic", clock.monotonic
            ),
        ):
            subs, extra = enumerate_subdevices(
                transport,
                resources,
                [],
                preferred_hrefs=("/mode/vs/0",),
                time_budget=5,
                collection_timeout=10,
                property_timeout=1,
            )
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].seed_path, (SUB, "device", "0"))
        self.assertEqual(extra["/" + SUB + "/child-only/vs/0"], {"value": "unique"})

    def test_silent_candidate_is_not_materialized(self):
        clock = Clock()
        transport = PropertyOnlyTransport(clock, live=False)
        with patch(
            "custom_components.localthings.registry.subdevices.time.monotonic", clock.monotonic
        ):
            subs, extra = enumerate_subdevices(
                transport,
                self.resources(),
                [],
                preferred_hrefs=("/mode/vs/0",),
                time_budget=5,
                collection_timeout=10,
                property_timeout=1,
            )
        self.assertEqual(subs, [])
        self.assertEqual(extra, {})

    def test_growing_property_set_does_not_duplicate_cached_wall_unit(self):
        coordinator = object.__new__(LocalThingsCoordinator)
        coordinator._session = Mock()
        old = Subdevice(kind="prefixed", key=SUB, seed_path=(), flat_hrefs=("/mode/vs/0",))
        fresh = Subdevice(
            kind="prefixed", key=SUB, seed_path=(), flat_hrefs=("/mode/vs/0", "/power/0")
        )
        coordinator.subdevices = [old, old]
        resources = {"/" + SUB + "/mode/vs/0": {"mode": "Cool"}}
        with (
            patch.object(coordinator, "_oic_res", return_value=[]),
            patch.object(coordinator, "_subdevice_probe_priority", return_value=()),
            patch(
                "custom_components.localthings.coordinator.enumerate_subdevices",
                return_value=([fresh], {}),
            ),
        ):
            coordinator._enumerate_subdevices_blocking(resources)
        self.assertEqual(coordinator.subdevices, [fresh])

    def test_partial_rediscovery_preserves_cached_property_set(self):
        coordinator = object.__new__(LocalThingsCoordinator)
        coordinator._session = Mock()
        old = Subdevice(
            kind="prefixed", key=SUB, seed_path=(), flat_hrefs=("/mode/vs/0", "/power/0")
        )
        fresh = Subdevice(kind="prefixed", key=SUB, seed_path=(), flat_hrefs=("/mode/vs/0",))
        coordinator.subdevices = [old]
        resources = {"/" + SUB + "/mode/vs/0": {"mode": "Cool"}}
        with (
            patch.object(coordinator, "_oic_res", return_value=[]),
            patch.object(coordinator, "_subdevice_probe_priority", return_value=()),
            patch(
                "custom_components.localthings.coordinator.enumerate_subdevices",
                return_value=([fresh], {}),
            ),
        ):
            coordinator._enumerate_subdevices_blocking(resources)
        self.assertEqual(coordinator.subdevices, [old])

    def test_climate_dependencies_precede_meter_probes(self):
        coordinator = object.__new__(LocalThingsCoordinator)
        coordinator._identity = DeviceIdentity(
            manufacturer="Samsung",
            model="ARTIK051_FAC_TIME_21K",
            name="",
            serial=None,
            device_types=("oic.d.airconditioner",),
        )
        resources = {
            "/energy/consumption/vs/0": {"value": 1},
            "/humidity/vs/0": {"value": 50},
            "/mode/vs/0": {"x.com.samsung.da.modes": ["Cool"]},
            "/power/0": {"value": True},
            "/temperatures/vs/0": {"items": []},
        }
        priority = coordinator._subdevice_probe_priority(resources)
        self.assertEqual(priority[0], "/mode/vs/0")
        self.assertLess(priority.index("/power/0"), priority.index("/energy/consumption/vs/0"))
        self.assertLess(priority.index("/temperatures/vs/0"), priority.index("/humidity/vs/0"))


if __name__ == "__main__":
    unittest.main()
