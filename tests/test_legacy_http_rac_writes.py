"""Transport-level TP6X_RAC write routing tests.

PR #565 proves that the RAC resources translate to the right legacy wrapper
bodies, but its transport still sends every write to the washer-style
aggregate endpoint. These tests pin the HTTP contract used by the Samsung
8888 RAC API.
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from custom_components.localthings.legacy_http_transport import LegacyHttpTransport

PREFIX = "x.com.samsung.da."


class _FakeConnection:
    """Stand-in for http.client.HTTPSConnection, recording requests."""

    log: ClassVar[list[tuple[str, str, dict | None, dict]]] = []
    routes: ClassVar[dict] = {}

    def __init__(self, host, port, context=None, timeout=None):
        self._status = 200
        self._body: dict | None = None

    def request(self, method, path, body=None, headers=None):
        payload = json.loads(body) if body else None
        _FakeConnection.log.append((method, path, payload, dict(headers or {})))
        self._status, self._body = _FakeConnection.routes.get(
            (method, path),
            _FakeConnection.routes.get(path, (404, None)),
        )

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
def rac(monkeypatch):
    monkeypatch.setattr("custom_components.localthings.legacy_http_transport._STATE", {})
    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.http.client.HTTPSConnection",
        _FakeConnection,
    )
    monkeypatch.setattr(
        "custom_components.localthings.legacy_http_transport.client_context",
        lambda cert_pem, key_pem: object(),
    )

    _FakeConnection.log = []
    _FakeConnection.routes = {
        ("PUT", "/devices/0"): (204, None),
        ("PUT", "/devices/0/mode"): (204, None),
        ("PUT", "/devices/0/wind"): (204, None),
        ("PUT", "/devices/0/temperatures/0"): (204, None),
    }

    device = LegacyHttpTransport(
        "10.0.0.8",
        8888,
        cert_pem="CERT",
        key_pem="KEY",
        token="tok",
        family="TP6X_RAC_16K",
    )
    device.connect()
    return device


class TestTp6xRacWriteRoutes:
    def test_power_uses_the_aggregate_path_without_the_device_wrapper(self, rac):
        code, _ = rac.write(["power", "vs", "0"], {PREFIX + "power": "Off"}, timeout=8.0)

        assert code == 0x44
        assert _FakeConnection.log[-1][:3] == (
            "PUT",
            "/devices/0",
            {"Operation": {"power": "Off"}},
        )

    def test_mode_uses_the_mode_endpoint_with_an_unwrapped_body(self, rac):
        code, _ = rac.write(
            ["mode", "vs", "0"],
            {PREFIX + "modes": ["Cool"]},
            timeout=8.0,
        )

        assert code == 0x44
        assert _FakeConnection.log[-1][:3] == (
            "PUT",
            "/devices/0/mode",
            {"modes": ["Cool"]},
        )

    def test_mode_options_use_the_same_mode_endpoint(self, rac):
        code, _ = rac.write(
            ["mode", "vs", "0"],
            {PREFIX + "options": ["Comode_Nano"]},
            timeout=8.0,
        )

        assert code == 0x44
        assert _FakeConnection.log[-1][:3] == (
            "PUT",
            "/devices/0/mode",
            {"options": ["Comode_Nano"]},
        )

    def test_fan_speed_uses_the_wind_endpoint_and_stays_numeric(self, rac):
        code, _ = rac.write(
            ["airflow", "vs", "0"],
            {PREFIX + "speedLevel": "1"},
            timeout=8.0,
        )

        assert code == 0x44
        assert _FakeConnection.log[-1][:3] == (
            "PUT",
            "/devices/0/wind",
            {"speedLevel": 1},
        )

    def test_swing_uses_the_wind_endpoint(self, rac):
        code, _ = rac.write(
            ["airflow", "vs", "0"],
            {PREFIX + "direction": "Up_And_Low"},
            timeout=8.0,
        )

        assert code == 0x44
        assert _FakeConnection.log[-1][:3] == (
            "PUT",
            "/devices/0/wind",
            {"direction": "Up_And_Low"},
        )

    def test_temperature_uses_the_item_endpoint_without_the_id_field(self, rac):
        code, _ = rac.write(
            ["temperatures", "vs", "0"],
            {
                PREFIX + "items": [
                    {
                        PREFIX + "id": "0",
                        PREFIX + "desired": "24",
                    }
                ]
            },
            timeout=8.0,
        )

        assert code == 0x44
        assert _FakeConnection.log[-1][:3] == (
            "PUT",
            "/devices/0/temperatures/0",
            {"desired": 24},
        )

    def test_temperature_without_an_item_id_is_refused_instead_of_guessed(self, rac):
        code, _ = rac.write(
            ["temperatures", "vs", "0"],
            {PREFIX + "items": [{PREFIX + "desired": "24"}]},
            timeout=8.0,
        )

        assert code == 0x80
        assert _FakeConnection.log == []
