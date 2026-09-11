"""Tests for LegacyHttpTransport -- the 8888 bridge behind the seam (issue #168).

The fake below answers exactly what a TP6X_WW6500 answers, shapes included:
the aggregate under a `Device` wrapper with two resources it only links to,
and bare field names throughout. What these tests pin down is that the
coordinator's side of the seam sees none of that -- it asks for `/device/0`
and canonical hrefs, and gets the batch and the reps it would get from a
CoAP device.
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from custom_components.localthings.legacy_http_transport import LegacyHttpTransport

AGGREGATE = {
    "Device": {
        "Alarms": [],
        "ConfigurationLink": {"href": "/devices/0/configuration"},
        "Diagnosis": {"diagnosisStart": "Ready"},
        "EnergyConsumption": {"saveLocation": "/files/usage.db"},
        "InformationLink": {"href": "/devices/0/information"},
        "Mode": {"options": ["Course_5B"], "supportedOptions": ["35B847E933FA53F5C841E923FA53F"]},
        "Operation": {
            "state": "Ready",
            "progress": "None",
            "progressPercentage": 1,
            "remainingTime": "04:29:00",
            "power": "On",
            "kidsLock": "Ready",
        },
        "Washer": {"waterTemperature": "60", "rinseCycles": "2", "spinLevel": "1400"},
        "connected": True,
        "description": "TP6X_WW6500(REDACTED)",
        "id": "0",
        "name": "Washer",
    }
}
CONFIGURATION = {"Configuration": {"remoteControlEnabled": True}}
INFORMATION = {
    "Information": {
        "description": "TP6X_WASHER",
        "modelID": "TP6X_WW6500|FF18E000|2001",
        "serialNumber": "REDACTED",
    }
}

PREFIX = "x.com.samsung.da."


class _FakeConnection:
    """Stand-in for http.client.HTTPSConnection, recording every request."""

    log: ClassVar[list[tuple[str, str, dict | None, dict]]] = []
    routes: ClassVar[dict[str, tuple[int, dict | None]]] = {}

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self._status = 200
        self._body: dict | None = None

    def request(self, method, path, body=None, headers=None):
        payload = json.loads(body) if body else None
        _FakeConnection.log.append((method, path, payload, dict(headers or {})))
        self._status, self._body = _FakeConnection.routes.get(path, (404, None))

    def getresponse(self):
        status, body = self._status, self._body
        raw = b"" if body is None else json.dumps(body).encode()

        class _Response:
            def __init__(self):
                self.status = status

            def read(self):
                return raw

        return _Response()

    def close(self):
        pass


@pytest.fixture
def transport(monkeypatch):
    _FakeConnection.log = []
    _FakeConnection.routes = {
        "/devices/0": (200, AGGREGATE),
        "/devices/0/configuration": (200, CONFIGURATION),
        "/devices/0/information": (200, INFORMATION),
        "/devices/0/washer": (200, {"Washer": AGGREGATE["Device"]["Washer"]}),
        "/devices/0/operation": (200, {"Operation": AGGREGATE["Device"]["Operation"]}),
    }
    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.http.client.HTTPSConnection",
        _FakeConnection,
    )
    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.client_context",
        lambda cert_pem, key_pem: object(),
    )
    device = LegacyHttpTransport(
        "10.0.0.7", 8888, cert_pem="CERT", key_pem="KEY", token="tok123456"
    )
    device.connect()
    return device


class TestSeed:
    def test_the_seed_read_returns_a_device0_batch(self, transport):
        """`/device/0` is what the coordinator polls. This firmware has no
        such resource, so the transport assembles one."""
        code, body = transport.read(["device", "0"], timeout=10.0)

        assert code == 0x45
        assert isinstance(body, list)
        assert {entry["href"] for entry in body} == {
            "/alarms/vs/0",
            "/course/vs/0",
            "/diagnosis/vs/0",
            "/information/vs/0",
            "/kidslock/vs/0",
            "/operational/state/vs/0",
            "/power/vs/0",
            "/remotectrl/vs/0",
            "/washer/vs/0",
        }

    def test_the_seed_costs_three_requests(self, transport):
        """The aggregate carries five resources; only the two it links to
        need fetching."""
        transport.read(["device", "0"], timeout=10.0)

        assert [path for _, path, _, _ in _FakeConnection.log] == [
            "/devices/0",
            "/devices/0/configuration",
            "/devices/0/information",
        ]

    def test_a_linked_resource_that_fails_does_not_fail_the_sweep(self, transport):
        """Same posture the CoAP path takes for a resource that goes quiet:
        the rest of the device is still worth having."""
        _FakeConnection.routes["/devices/0/information"] = (503, None)

        code, body = transport.read(["device", "0"], timeout=10.0)

        assert code == 0x45
        assert "/information/vs/0" not in {entry["href"] for entry in body}
        assert "/washer/vs/0" in {entry["href"] for entry in body}

    def test_an_unreachable_appliance_reports_its_status_as_a_coap_code(self, transport):
        """403 SHE-001 -- what it answers while Remote Control is off -- is
        the everyday case, and reads as a failed poll rather than an error."""
        _FakeConnection.routes["/devices/0"] = (403, {"errorCode": "SHE-001"})

        assert transport.read(["device", "0"], timeout=10.0) == (0x83, None)


class TestResourceReads:
    def test_a_canonical_href_reads_its_own_endpoint(self, transport):
        code, rep = transport.read(["washer", "vs", "0"], timeout=10.0)

        assert code == 0x45
        assert rep[PREFIX + "spinLevel"] == "1400"
        assert _FakeConnection.log[-1][1] == "/devices/0/washer"

    def test_a_fanned_out_href_reads_the_endpoint_that_carries_it(self, transport):
        """/power/vs/0 is served inside Operation on this firmware."""
        code, rep = transport.read(["power", "vs", "0"], timeout=10.0)

        assert (code, rep) == (0x45, {PREFIX + "power": "On"})
        assert _FakeConnection.log[-1][1] == "/devices/0/operation"

    def test_an_href_this_firmware_does_not_serve_is_a_404(self, transport):
        """Including /oic/p and /oic/d, which read_identity tries first:
        nginx answers its own HTML for those, and nothing is asked of it."""
        assert transport.read(["oic", "p"], timeout=10.0) == (0x84, None)
        assert _FakeConnection.log == []


class TestWrites:
    def test_a_write_goes_to_the_aggregate_in_the_appliance_envelope(self, transport):
        _FakeConnection.routes["/devices/0"] = (204, None)

        code = transport.write(
            ["operational", "state", "vs", "0"], {PREFIX + "state": "Pause"}, timeout=8.0
        )

        assert code == 0x44  # 2.04 Changed
        method, path, body, _ = _FakeConnection.log[-1]
        assert (method, path) == ("PUT", "/devices/0")
        assert body == {"Device": {"Operation": {"state": "Pause"}}}

    def test_a_cycle_on_its_own_is_refused_rather_than_silently_dropped(self, transport):
        """Measured on the hardware: this firmware answers 204 to a
        `Course_` token sent without a start and then discards it -- which
        in Home Assistant reads as a write that worked."""
        code = transport.write(
            ["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, timeout=8.0
        )

        assert code == 0x85  # 4.05 Method Not Allowed
        assert _FakeConnection.log == []

    def test_the_other_tokens_in_that_same_array_still_write(self, transport):
        """The rule is about the field, not the resource: LaundryOutTime
        goes to the same options array and holds."""
        _FakeConnection.routes["/devices/0"] = (204, None)

        code = transport.write(
            ["course", "vs", "0"], {PREFIX + "options": ["LaundryOutTime_60"]}, timeout=8.0
        )

        assert code == 0x44
        assert _FakeConnection.log[-1][2] == {
            "Device": {"Mode": {"options": ["LaundryOutTime_60"]}}
        }

    def test_a_settings_write_on_its_own_is_refused_too(self, transport):
        code = transport.write(
            ["washer", "vs", "0"], {PREFIX + "waterTemperature": "40"}, timeout=8.0
        )

        assert code == 0x85
        assert _FakeConnection.log == []

    def test_a_write_with_nowhere_to_land_is_refused_rather_than_guessed(self, transport):
        code = transport.write(
            ["energy", "consumption", "vs", "0"], {PREFIX + "cumulativePower": "1"}, timeout=8.0
        )

        assert code == 0x84
        assert _FakeConnection.log == []


class TestCapabilities:
    def test_this_transport_has_no_push(self):
        assert LegacyHttpTransport.supports_observe is False

    def test_the_observe_operations_say_so_rather_than_pretending(self, transport):
        with pytest.raises(NotImplementedError):
            transport.subscribe(["power", "vs", "0"])
        with pytest.raises(NotImplementedError):
            transport.refresh_observes([("power", "vs", "0")])

    def test_use_before_connect_is_an_error_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(
            "custom_components.localthings.legacy_http_transport.http.client.HTTPSConnection",
            _FakeConnection,
        )
        never = LegacyHttpTransport("10.0.0.7", 8888, cert_pem="C", key_pem="K", token="t")

        with pytest.raises(RuntimeError, match="no session"):
            never.read(["washer", "vs", "0"], timeout=1.0)

    def test_every_request_carries_the_device_token(self, transport):
        """Without it the appliance answers 401, and the flow's own probe
        would be the first thing to hit it."""
        transport.read(["washer", "vs", "0"], timeout=10.0)

        assert _FakeConnection.log[-1][3]["Authorization"] == "Bearer tok123456"
