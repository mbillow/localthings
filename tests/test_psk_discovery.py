"""Regression checks for PSK discovery that must not preflight a certificate."""

import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant

from custom_components.localthings import config_flow, probing
from custom_components.localthings.const import PLAINTEXT_DISCOVERY_PORTS

HOST = "192.0.2.36"
IDENTITY = probing.PlaintextIdentity(
    "00000000-0000-0000-0000-000000000001", "ARTIK051", "", "", "", ("oic.d.airconditioner",)
)


class PskDiscoveryTests(unittest.TestCase):
    def test_public_artik_port_is_tried(self):
        self.assertIn(49154, PLAINTEXT_DISCOVERY_PORTS)

    def test_advertised_psk_endpoint_avoids_certificate_preflight(self):
        with (
            patch.object(probing, "_discover_advertised_ports", return_value=((49155,), 49154)),
            patch.object(probing, "_legacy_http_open", return_value=False),
            patch.object(probing, "_read_plaintext_identity", return_value=IDENTITY),
            patch.object(probing, "_clienthello_scan") as certificate_probe,
        ):
            result = probing.look(HOST, confirm_dtls=False)
        self.assertEqual(result.candidates, [49155])
        self.assertEqual(result.confirmed, [])
        self.assertEqual(result.plaintext_port, 49154)
        certificate_probe.assert_not_called()

    def test_missing_advertisement_keeps_existing_scan_fallback(self):
        with (
            patch.object(probing, "_discover_advertised_ports", return_value=((), None)),
            patch.object(probing, "_legacy_http_open", return_value=False),
            patch.object(probing, "_clienthello_scan", return_value=[49155]) as scan,
        ):
            result = probing.look(HOST, confirm_dtls=False)
        self.assertEqual(result.confirmed, [49155])
        scan.assert_called_once()

    def test_certificate_path_keeps_its_existing_preflight(self):
        with (
            patch.object(probing, "_discover_advertised_ports", return_value=((49155,), 49154)),
            patch.object(probing, "_legacy_http_open", return_value=False),
            patch.object(probing, "_read_plaintext_identity", return_value=IDENTITY),
            patch.object(probing, "_clienthello_scan", return_value=[49155]) as scan,
        ):
            result = probing.look(HOST)
        self.assertEqual(result.confirmed, [49155])
        scan.assert_called_once()


class DirectOwnerPskTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_psk_can_skip_initial_certificate_attempt(self):
        flow = config_flow.LocalThingsConfigFlow()
        executor = AsyncMock()
        flow.hass = cast(
            HomeAssistant,
            SimpleNamespace(
                config_entries=SimpleNamespace(async_entries=Mock(return_value=[])),
                async_add_executor_job=executor,
            ),
        )
        with (
            patch.object(
                flow, "_finish_probe", new=AsyncMock(return_value={"step_id": "certificate_probe"})
            ),
            patch.object(
                flow, "async_step_psk_owner", new=AsyncMock(return_value={"step_id": "psk_owner"})
            ),
        ):
            result = await flow.async_step_user({"host": HOST, "use_owner_psk": True})
        self.assertEqual(result["step_id"], "psk_owner")
        self.assertEqual(flow._host, HOST)
        executor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
