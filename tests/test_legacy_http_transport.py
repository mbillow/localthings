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
from pathlib import Path
from typing import ClassVar

import pytest

from custom_components.localthings.legacy_http import http_status_to_coap
from custom_components.localthings.legacy_http_transport import LegacyHttpTransport
from custom_components.localthings.registry.capabilities import cook

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
    # Keyed by path, or by (method, path) where a GET and a PUT to the same
    # path must answer differently.
    routes: ClassVar[dict] = {}

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self._status = 200
        self._body: dict | None = None

    def request(self, method, path, body=None, headers=None):
        payload = json.loads(body) if body else None
        _FakeConnection.log.append((method, path, payload, dict(headers or {})))
        routes = _FakeConnection.routes
        self._status, self._body = routes.get((method, path), routes.get(path, (404, None)))

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


@pytest.fixture(autouse=True)
def _fresh_appliance_state(monkeypatch):
    """Held values outlive a transport object by design, so each test starts
    from none."""
    monkeypatch.setattr("custom_components.localthings.legacy_http_transport._STATE", {})


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
        "10.0.0.7", 8888, cert_pem="CERT", key_pem="KEY", token="tok123456", family="TP6X_WASHER"
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
            "/st/washercourse/vs/0",
            "/washer/vs/0",
        }

    def test_the_seed_carries_the_course_table(self, transport):
        """Not served by the appliance, but the registry needs it to label a
        cycle by name instead of by its raw code -- so the sweep carries it
        for the family, and it costs no request."""
        before = len(_FakeConnection.log)
        code, body = transport.read(["device", "0"], timeout=10.0)
        reps = {entry["href"]: entry["rep"] for entry in body}

        assert code == 0x45
        assert reps["/st/washercourse/vs/0"] == {PREFIX + "st.courseTable": "Table_00"}
        assert len(_FakeConnection.log) - before == 3

    def test_the_seed_carries_the_power_flag_from_the_model_id(self, transport, monkeypatch):
        """A WW6500 refuses remote power, and says so in its modelID, so the
        sweep carries the setinfo flag the power switch is gated on."""
        info = {
            "Information": {
                **INFORMATION["Information"],
                "modelID": "TP6X_WW6500|FF18E000|20010102001011070000000000000000",
            }
        }
        _FakeConnection.routes["/devices/0/information"] = (200, info)

        _, body = transport.read(["device", "0"], timeout=10.0)
        reps = {entry["href"]: entry["rep"] for entry in body}

        assert reps["/wm/setinfo/vs/0"] == {PREFIX + "isModelSettingPowerOnOff": "false"}

    def test_a_short_model_id_adds_no_power_flag(self, transport):
        _, body = transport.read(["device", "0"], timeout=10.0)

        assert "/wm/setinfo/vs/0" not in {entry["href"] for entry in body}

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

    def test_remote_control_off_reads_as_a_failed_poll_with_its_reason(self, transport):
        """403 SHE-001 is what every request gets while Remote Control is off
        at the panel -- the everyday case, so it carries a reason, not a code."""
        _FakeConnection.routes["/devices/0"] = (403, {"errorCode": "SHE-001"})

        assert transport.read(["device", "0"], timeout=10.0) == (
            0x83,
            "Remote Control is off at the appliance",
        )

    def test_a_rejected_token_raises_rather_than_reading_as_an_outage(self, transport):
        """Only a new token helps, so this is the coordinator's cue to ask
        for one rather than to keep retrying."""
        from custom_components.localthings.transport import AuthRejected

        _FakeConnection.routes["/devices/0"] = (401, {"errorDescription": "Token is not valid"})

        with pytest.raises(AuthRejected):
            transport.read(["device", "0"], timeout=10.0)


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

    def test_a_query_is_refused_rather_than_dropped(self, transport):
        """These endpoints have no interface query, so a read under one is a
        4.02 Bad Option and nothing goes out."""
        assert transport.read(["washer", "vs", "0"], timeout=10.0, query=["if=oic.if.b"]) == (
            0x82,
            None,
        )
        assert _FakeConnection.log == []


class TestWrites:
    def test_a_write_goes_to_the_aggregate_in_the_appliance_envelope(self, transport):
        _FakeConnection.routes["/devices/0"] = (204, None)

        code, _ = transport.write(
            ["operational", "state", "vs", "0"], {PREFIX + "state": "Pause"}, timeout=8.0
        )

        assert code == 0x44  # 2.04 Changed
        method, path, body, _ = _FakeConnection.log[-1]
        assert (method, path) == ("PUT", "/devices/0")
        assert body == {"Device": {"Operation": {"state": "Pause"}}}

    def test_the_appliances_reason_for_refusing_comes_back(self, transport):
        """This firmware puts its own account of a rejected write in the
        body -- `Control fail, <...>` -- which is the only explanation the
        device ever gives. A transport handing back the code alone would
        drop it, so the pair travels together.
        """
        _FakeConnection.routes["/devices/0"] = (
            400,
            {"errorCode": "0", "errorDescription": "Control fail, <Mode.options=Course_63>"},
        )

        code, response = transport.write(
            ["operational", "state", "vs", "0"], {PREFIX + "state": "Run"}, timeout=8.0
        )

        assert code == http_status_to_coap(400)
        assert response == "Control fail, <Mode.options=Course_63>"

    def test_the_other_tokens_in_that_same_array_still_write(self, transport):
        """The rule is about the field, not the resource: LaundryOutTime
        goes to the same options array and holds."""
        _FakeConnection.routes["/devices/0"] = (204, None)

        code, _ = transport.write(
            ["course", "vs", "0"], {PREFIX + "options": ["LaundryOutTime_60"]}, timeout=8.0
        )

        assert code == 0x44
        assert _FakeConnection.log[-1][2] == {
            "Device": {"Mode": {"options": ["LaundryOutTime_60"]}}
        }

    def test_a_write_with_nowhere_to_land_is_refused_rather_than_guessed(self, transport):
        code, _ = transport.write(
            ["energy", "consumption", "vs", "0"], {PREFIX + "cumulativePower": "1"}, timeout=8.0
        )

        assert code == 0x84
        assert _FakeConnection.log == []

    def test_stop_on_an_idle_appliance_sends_nothing(self, transport):
        """`Ready` on an idle WW6500 is not a no-op: it moves the appliance
        to `Pause` and resets the dialled-in settings to the course's
        defaults. The fixture's operation reads `Ready`."""
        code, _ = transport.write(
            ["operational", "state", "vs", "0"], {PREFIX + "state": "Ready"}, timeout=8.0
        )

        assert code == 0x44
        assert [method for method, *_ in _FakeConnection.log] == ["GET"]

    def test_stop_on_a_running_cycle_is_sent(self, transport):
        _FakeConnection.routes["/devices/0/operation"] = (200, {"Operation": {"state": "Run"}})
        _FakeConnection.routes[("PUT", "/devices/0")] = (204, None)

        code, _ = transport.write(
            ["operational", "state", "vs", "0"], {PREFIX + "state": "Ready"}, timeout=8.0
        )

        assert code == 0x44
        assert _FakeConnection.log[-1][:3] == (
            "PUT",
            "/devices/0",
            {"Device": {"Operation": {"state": "Ready"}}},
        )

    def test_stop_is_sent_when_the_state_cannot_be_read(self, transport):
        """No fresh read, no reason to withhold it -- same as before."""
        _FakeConnection.routes["/devices/0/operation"] = (500, None)
        _FakeConnection.routes[("PUT", "/devices/0")] = (204, None)

        transport.write(
            ["operational", "state", "vs", "0"], {PREFIX + "state": "Ready"}, timeout=8.0
        )

        assert _FakeConnection.log[-1][0] == "PUT"


class TestStartOnlyWrites:
    """A cycle and the washer's settings are taken by this firmware only in
    the same body as a start. Sent alone they are answered 204 and dropped,
    so the transport holds them for the Start button instead."""

    @pytest.fixture
    def idle(self, transport, monkeypatch):
        monkeypatch.setattr(
            "custom_components.localthings.legacy_http_transport.time.sleep", lambda s: None
        )
        _FakeConnection.routes[("PUT", "/devices/0")] = (204, None)
        transport.read(["device", "0"], timeout=10.0)
        _FakeConnection.log.clear()
        return transport

    @staticmethod
    def _puts():
        return [body for method, _, body, _ in _FakeConnection.log if method == "PUT"]

    def test_choosing_a_cycle_sends_nothing(self, idle):
        code, _ = idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, 8.0)

        assert code == 0x44
        assert self._puts() == []

    def test_the_held_cycle_is_what_the_select_reads(self, idle):
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, 8.0)

        _, body = idle.read(["device", "0"], timeout=10.0)
        course = next(e["rep"] for e in body if e["href"] == "/course/vs/0")

        assert "Course_63" in course[PREFIX + "options"]
        assert "Course_5B" not in course[PREFIX + "options"]

    def test_a_held_setting_is_what_a_single_read_returns(self, idle):
        idle.write(["washer", "vs", "0"], {PREFIX + "waterTemperature": "40"}, 8.0)

        _, rep = idle.read(["washer", "vs", "0"], timeout=10.0)

        assert rep[PREFIX + "waterTemperature"] == "40"

    def test_turning_the_dial_drops_the_held_cycle(self, idle):
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, 8.0)
        turned = json.loads(json.dumps(AGGREGATE))
        turned["Device"]["Mode"]["options"] = ["Course_5C"]
        _FakeConnection.routes["/devices/0"] = (200, turned)

        _, body = idle.read(["device", "0"], timeout=10.0)
        course = next(e["rep"] for e in body if e["href"] == "/course/vs/0")

        assert course[PREFIX + "options"] == ["Course_5C"]

    def test_start_carries_everything_held_in_one_body(self, idle):
        _FakeConnection.routes["/devices/0/operation"] = (200, {"Operation": {"state": "Run"}})
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, 8.0)
        idle.write(["washer", "vs", "0"], {PREFIX + "spinLevel": "800"}, 8.0)

        code, _ = idle.write(["operational", "state", "vs", "0"], {PREFIX + "state": "Run"}, 8.0)

        assert code == 0x44
        assert self._puts() == [
            {
                "Device": {
                    "Operation": {"state": "Run"},
                    "Mode": {"options": ["Course_63"]},
                    "Washer": {"spinLevel": "800"},
                }
            }
        ]

    def test_a_programme_loaded_but_not_running_gets_a_plain_run(self, idle):
        """Measured: the composed body loads the programme and leaves the
        appliance in Ready; a Run on its own then starts it."""
        _FakeConnection.routes["/devices/0/operation"] = (200, {"Operation": {"state": "Ready"}})
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, 8.0)

        idle.write(["operational", "state", "vs", "0"], {PREFIX + "state": "Run"}, 8.0)

        assert self._puts()[-1] == {"Device": {"Operation": {"state": "Run"}}}
        assert len(self._puts()) == 2

    def test_a_refused_run_on_a_cycle_that_started_anyway_is_no_refusal(self, idle):
        """The cycle got to Run by itself between the read and the plain Run,
        which the appliance then refuses."""

        class _Starts(dict):
            def get(self, key, default=None):
                puts = sum(1 for method, *_ in _FakeConnection.log if method == "PUT")
                if key == ("PUT", "/devices/0") and puts > 1:
                    return (400, {"errorCode": "0", "errorDescription": "Control fail, <Run>"})
                if key == "/devices/0/operation":
                    state = "Run" if puts > 1 else "Ready"
                    return (200, {"Operation": {"state": state}})
                return super().get(key, default)

        _FakeConnection.routes = _Starts(_FakeConnection.routes)
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, 8.0)

        code, _ = idle.write(["operational", "state", "vs", "0"], {PREFIX + "state": "Run"}, 8.0)

        assert code == 0x44
        assert len(self._puts()) == 2

    def test_start_with_nothing_held_is_a_plain_run(self, idle):
        idle.write(["operational", "state", "vs", "0"], {PREFIX + "state": "Run"}, 8.0)

        assert self._puts() == [{"Device": {"Operation": {"state": "Run"}}}]

    def test_a_started_programme_clears_what_was_held(self, idle):
        _FakeConnection.routes["/devices/0/operation"] = (200, {"Operation": {"state": "Run"}})
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, 8.0)
        idle.write(["operational", "state", "vs", "0"], {PREFIX + "state": "Run"}, 8.0)
        _FakeConnection.log.clear()

        idle.write(["operational", "state", "vs", "0"], {PREFIX + "state": "Run"}, 8.0)

        assert self._puts() == [{"Device": {"Operation": {"state": "Run"}}}]

    def test_a_held_cycle_survives_a_reconnect(self, idle):
        """The coordinator builds a new transport after a failed poll; the
        cycle chosen before it must still go out with Start."""
        _FakeConnection.routes["/devices/0/operation"] = (200, {"Operation": {"state": "Run"}})
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, 8.0)
        reconnected = LegacyHttpTransport(
            "10.0.0.7", 8888, cert_pem="C", key_pem="K", token="t", family="TP6X_WASHER"
        )
        reconnected.connect()

        reconnected.write(["operational", "state", "vs", "0"], {PREFIX + "state": "Run"}, 8.0)

        assert self._puts()[0] == {
            "Device": {"Operation": {"state": "Run"}, "Mode": {"options": ["Course_63"]}}
        }

    def test_a_running_appliance_drops_what_was_held(self, idle):
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, 8.0)
        running = json.loads(json.dumps(AGGREGATE))
        running["Device"]["Operation"]["state"] = "Run"
        _FakeConnection.routes["/devices/0"] = (200, running)

        _, body = idle.read(["device", "0"], timeout=10.0)
        course = next(e["rep"] for e in body if e["href"] == "/course/vs/0")

        assert course[PREFIX + "options"] == ["Course_5B"]

    def test_choosing_another_cycle_drops_the_settings_held_for_the_last(self, idle):
        """Cotton at 95C, then Extra Speed, which tops out at 40C: the 95 must
        not go out with the new course."""
        _FakeConnection.routes["/devices/0/operation"] = (200, {"Operation": {"state": "Run"}})
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_5B"]}, 8.0)
        idle.write(["washer", "vs", "0"], {PREFIX + "waterTemperature": "95"}, 8.0)
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_5C"]}, 8.0)

        idle.write(["operational", "state", "vs", "0"], {PREFIX + "state": "Run"}, 8.0)

        assert self._puts() == [
            {"Device": {"Operation": {"state": "Run"}, "Mode": {"options": ["Course_5C"]}}}
        ]

    def test_a_setting_chosen_after_the_cycle_is_kept(self, idle):
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_5C"]}, 8.0)
        idle.write(["washer", "vs", "0"], {PREFIX + "waterTemperature": "30"}, 8.0)

        _, rep = idle.read(["washer", "vs", "0"], timeout=10.0)

        assert rep[PREFIX + "waterTemperature"] == "30"

    def test_the_laundry_out_reminder_is_written_at_once(self, idle):
        """Measured on a WW6500: LaundryOutTime applies on its own, unlike
        the cycle beside it in the same options array."""
        code, _ = idle.write(
            ["course", "vs", "0"], {PREFIX + "options": ["LaundryOutTime_60"]}, 8.0
        )

        assert code == 0x44
        assert self._puts() == [{"Device": {"Mode": {"options": ["LaundryOutTime_60"]}}}]


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
        never = LegacyHttpTransport(
            "10.0.0.7", 8888, cert_pem="C", key_pem="K", token="t", family="TP6X_WASHER"
        )

        with pytest.raises(RuntimeError, match="no session"):
            never.read(["washer", "vs", "0"], timeout=1.0)

    def test_every_request_carries_the_device_token(self, transport):
        """Without it the appliance answers 401, and the flow's own probe
        would be the first thing to hit it."""
        transport.read(["washer", "vs", "0"], timeout=10.0)

        assert _FakeConnection.log[-1][3]["Authorization"] == "Bearer tok123456"


class TestUnmappedFamily:
    """A family with no envelope table is read for its identity alone, so it
    sets up like an unrecognized DTLS device rather than borrowing another
    family's field map."""

    @pytest.fixture
    def unmapped(self, transport):
        device = LegacyHttpTransport(
            "10.0.0.7", 8888, cert_pem="C", key_pem="K", token="t", family="TP6X_DRYER"
        )
        device.connect()
        return device

    def test_the_seed_carries_identity_and_nothing_else(self, unmapped):
        code, body = unmapped.read(["device", "0"], timeout=10.0)

        assert code == 0x45
        assert [entry["href"] for entry in body] == ["/information/vs/0"]

    def test_a_washer_href_is_not_served(self, unmapped):
        assert unmapped.read(["washer", "vs", "0"], timeout=10.0) == (0x84, None)

    def test_diagnostics_carry_the_untranslated_bodies(self, unmapped):
        unmapped.read(["device", "0"], timeout=10.0)

        diag = unmapped.diagnostics()

        assert diag["family"] == "TP6X_DRYER"
        assert diag["family_mapped"] is False
        assert diag["unmapped_resources"] == [
            "Alarms",
            "Configuration",
            "Diagnosis",
            "EnergyConsumption",
            "Mode",
            "Operation",
            "Washer",
        ]
        assert diag["bodies"]["Washer"] == AGGREGATE["Device"]["Washer"]
        # The aggregate's description can carry the serial; name is user-set.
        assert "description" not in diag["bodies"]
        assert "name" not in diag["bodies"]


def test_diagnostics_name_what_a_mapped_family_does_not_read(transport):
    """The washer's table reads everything but the usage-file pointer."""
    transport.read(["device", "0"], timeout=10.0)

    assert transport.diagnostics()["unmapped_resources"] == ["EnergyConsumption"]


# The WW6500's own supportedOptions and supported lists, so a held course
# has a record to take its defaults from.
_WW6500_BLOB = (
    "35B847E933FA53F5C841E923FA53F5D8102923FA43F66841E930FA30F5E831E920FA207"
    "5F867E943FA53F60831E930FA43F61841E943FA43F6385209204A204648000913FA53F"
    "6C831E933FA30F65841E920FA30F67843E923FA43F688430923FA53F"
)


class TestHeldCourseDefaults:
    """A held course reads with the defaults its record states, as the dial
    does, and the start still leaves them to the appliance."""

    @pytest.fixture
    def idle(self, transport, monkeypatch):
        monkeypatch.setattr(
            "custom_components.localthings.legacy_http_transport.time.sleep", lambda s: None
        )
        aggregate = json.loads(json.dumps(AGGREGATE))
        aggregate["Device"]["Mode"]["supportedOptions"] = [_WW6500_BLOB]
        aggregate["Device"]["Washer"] = {
            "waterTemperature": "40",
            "supportedWaterTemperature": ["None", "Cold", "20", "30", "40", "60", "95"],
            "rinseCycles": "3",
            "supportedRinseCycles": ["0", "1", "2", "3", "4", "5"],
            "spinLevel": "1400",
            "supportedSpinLevel": ["RinseHold", "NoSpin", "400", "800", "1200", "1400"],
        }
        _FakeConnection.routes["/devices/0"] = (200, aggregate)
        _FakeConnection.routes["/devices/0/washer"] = (
            200,
            {"Washer": aggregate["Device"]["Washer"]},
        )
        _FakeConnection.routes[("PUT", "/devices/0")] = (204, None)
        _FakeConnection.routes["/devices/0/operation"] = (200, {"Operation": {"state": "Run"}})
        transport.read(["device", "0"], timeout=10.0)
        _FakeConnection.log.clear()
        return transport

    @staticmethod
    def _washer(body):
        return next(e["rep"] for e in body if e["href"] == "/washer/vs/0")

    def test_drum_clean_reads_60c_two_rinses_400rpm(self, idle):
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, 8.0)

        _, body = idle.read(["device", "0"], timeout=10.0)
        washer = self._washer(body)

        assert washer[PREFIX + "waterTemperature"] == "60"
        assert washer[PREFIX + "rinseCycles"] == "2"
        assert washer[PREFIX + "spinLevel"] == "400"

    def test_a_single_washer_read_shows_them_too(self, idle):
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, 8.0)

        _, rep = idle.read(["washer", "vs", "0"], timeout=10.0)

        assert rep[PREFIX + "spinLevel"] == "400"

    def test_a_setting_held_after_the_course_wins(self, idle):
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_5B"]}, 8.0)
        idle.write(["washer", "vs", "0"], {PREFIX + "waterTemperature": "95"}, 8.0)

        _, body = idle.read(["device", "0"], timeout=10.0)
        washer = self._washer(body)

        assert washer[PREFIX + "waterTemperature"] == "95"
        assert washer[PREFIX + "rinseCycles"] == "3"  # Cotton's own default

    def test_the_defaults_are_not_sent(self, idle):
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_63"]}, 8.0)
        idle.read(["device", "0"], timeout=10.0)

        idle.write(["operational", "state", "vs", "0"], {PREFIX + "state": "Run"}, 8.0)

        puts = [body for method, _, body, _ in _FakeConnection.log if method == "PUT"]
        assert puts == [
            {"Device": {"Operation": {"state": "Run"}, "Mode": {"options": ["Course_63"]}}}
        ]

    def test_rinse_and_spin_reads_no_temperature(self, idle):
        """Rinse+Spin has no temperature: its record allows none and points
        the default at "None", which is what the appliance reports once the
        course runs. Not the previous course's 40C."""
        idle.write(["course", "vs", "0"], {PREFIX + "options": ["Course_64"]}, 8.0)

        _, body = idle.read(["device", "0"], timeout=10.0)
        washer = self._washer(body)

        assert washer[PREFIX + "waterTemperature"] == "None"
        assert washer[PREFIX + "rinseCycles"] == "1"
        assert washer[PREFIX + "spinLevel"] == "1400"

    def test_nothing_held_reads_as_reported(self, idle):
        _, body = idle.read(["device", "0"], timeout=10.0)

        assert self._washer(body)[PREFIX + "waterTemperature"] == "40"


class TestWallOvenStart:
    """The cook start's Collection batch over 8888 (#572): this firmware has no
    Collections, so the batch goes as one aggregate PUT."""

    FIXTURE = Path(__file__).parent / "fixtures" / "oven_lcd_ov_wall_16k_8888.json"

    @pytest.fixture
    def oven(self, transport):
        bodies = json.loads(self.FIXTURE.read_text(encoding="utf-8"))["bodies"]
        _FakeConnection.routes["/devices/0"] = (200, {"Device": bodies})
        _FakeConnection.routes[("PUT", "/devices/0")] = (204, None)
        device = LegacyHttpTransport(
            "10.0.0.8", 8888, cert_pem="CERT", key_pem="KEY", token="tok", family="LCD_OV_WALL_16K"
        )
        device.connect()
        return device

    def test_a_start_goes_as_one_aggregate_body(self, oven):
        _, seed = oven.read(["device", "0"], timeout=10.0)
        resources = {e["href"]: e["rep"] for e in seed}
        batch = cook.plan_start(resources, "UpperConvectionBake", 350, 5400).batch()
        _FakeConnection.log.clear()

        code, _ = oven.write(["device", "0"], batch, timeout=8.0)

        assert code == 0x44
        assert [entry[:3] for entry in _FakeConnection.log] == [
            (
                "PUT",
                "/devices/0",
                {
                    "Device": {
                        "Mode": {"modes": ["UpperConvectionBake"]},
                        "Temperatures": [{"id": "0", "desired": 350, "unit": "Fahrenheit"}],
                        "Operation": {"operationTime": "01:30:00", "state": "Run"},
                    }
                },
            )
        ]

    def test_a_batch_anywhere_else_is_refused(self, oven):
        code, _ = oven.write(
            ["mode", "vs", "0"], [{"href": "/mode/vs/0", "rep": {PREFIX + "modes": ["Bake"]}}], 8.0
        )

        assert code == 0x85
        assert _FakeConnection.log == []

    def test_a_batch_that_would_lose_an_element_sends_nothing(self, oven):
        batch = [
            {"href": "/energy/consumption/vs/0", "rep": {PREFIX + "cumulativePower": "1"}},
            {"href": "/operational/state/vs/0", "rep": {PREFIX + "state": "Run"}},
        ]

        code, _ = oven.write(["device", "0"], batch, timeout=8.0)

        assert code == 0x84
        assert _FakeConnection.log == []

    def test_the_rac_takes_no_batch(self, transport):
        rac = LegacyHttpTransport(
            "10.0.0.9", 8888, cert_pem="CERT", key_pem="KEY", token="tok", family="TP6X_RAC_16K"
        )
        rac.connect()
        batch = [{"href": "/operational/state/vs/0", "rep": {PREFIX + "state": "Run"}}]

        code, _ = rac.write(["device", "0"], batch, timeout=8.0)

        assert code == 0x85
        assert _FakeConnection.log == []
