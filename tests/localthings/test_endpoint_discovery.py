"""Tests for identity-bound OCF endpoint selection."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from custom_components.localthings.endpoint_discovery import (
    IdentityEndpointDiscoveryError,
    discover_identity_endpoint,
    normalize_ocf_device_id,
)

TARGET_DEVICE_ID = "1bb10cd6-3214-4bc5-842e-19a0fe2d8123"


class _DiscoveryModule(ModuleType):
    discover_ocf_secure_ports_multicast: object


def _helper_module(monkeypatch, discover) -> None:
    module = _DiscoveryModule("smartthings_local.protocol.ocf_discovery")
    if discover is not None:
        module.discover_ocf_secure_ports_multicast = discover
    monkeypatch.setitem(sys.modules, module.__name__, module)


def test_normalize_ocf_device_id() -> None:
    assert normalize_ocf_device_id(None) is None
    assert normalize_ocf_device_id("  ") is None
    assert normalize_ocf_device_id(TARGET_DEVICE_ID.upper()) == TARGET_DEVICE_ID
    assert normalize_ocf_device_id(f"uuid:{TARGET_DEVICE_ID}") == TARGET_DEVICE_ID
    assert normalize_ocf_device_id(f"URN:UUID:{TARGET_DEVICE_ID}") == TARGET_DEVICE_ID
    assert (
        normalize_ocf_device_id(bytes.fromhex(TARGET_DEVICE_ID.replace("-", "")))
        == TARGET_DEVICE_ID
    )

    for invalid in (
        "not-a-uuid",
        "00000000-0000-0000-0000-000000000000",
        f"{{{TARGET_DEVICE_ID}}}",
        42,
        [],
        {},
    ):
        with pytest.raises(ValueError, match="invalid OCF device UUID"):
            normalize_ocf_device_id(invalid)


def test_discovery_returns_only_source_bound_candidates(monkeypatch) -> None:
    calls = []

    def discover(target_device_id, *, interface_address):
        calls.append((target_device_id, interface_address))
        return SimpleNamespace(
            found=True,
            address="192.0.2.20",
            ports=(46060,),
            error_code=None,
        )

    _helper_module(monkeypatch, discover)

    result = discover_identity_endpoint(
        "192.0.2.20",
        TARGET_DEVICE_ID,
        "192.0.2.10",
    )

    assert result.address == "192.0.2.20"
    assert result.ports == (46060,)
    assert calls == [(TARGET_DEVICE_ID, "192.0.2.10")]
    rendered = repr(result)
    assert "192.0.2.20" not in rendered
    assert "46060" not in rendered
    assert TARGET_DEVICE_ID not in rendered


@pytest.mark.parametrize(
    ("error_code", "reason"),
    [
        ("interface_unavailable", "unavailable"),
        ("no_ocf_response", "not_found"),
        ("target_not_found", "not_found"),
        ("ambiguous_target", "ambiguous"),
        ("target_not_stable", "ambiguous"),
        ("malformed_ocf_response", "invalid_advertisement"),
        ("no_secure_ports", "invalid_advertisement"),
        ("future_error", "invalid_advertisement"),
    ],
)
def test_discovery_maps_helper_failures(monkeypatch, error_code, reason) -> None:
    _helper_module(
        monkeypatch,
        lambda *args, **kwargs: SimpleNamespace(
            found=False,
            address=None,
            ports=(),
            error_code=error_code,
        ),
    )

    with pytest.raises(IdentityEndpointDiscoveryError) as raised:
        discover_identity_endpoint("192.0.2.20", TARGET_DEVICE_ID, "192.0.2.10")

    assert raised.value.reason == reason
    rendered = str(raised.value)
    assert "192.0.2.20" not in rendered
    assert TARGET_DEVICE_ID not in rendered


def test_discovery_rejects_a_different_response_source(monkeypatch) -> None:
    _helper_module(
        monkeypatch,
        lambda *args, **kwargs: SimpleNamespace(
            found=True,
            address="192.0.2.21",
            ports=(46060,),
            error_code=None,
        ),
    )

    with pytest.raises(IdentityEndpointDiscoveryError) as raised:
        discover_identity_endpoint("192.0.2.20", TARGET_DEVICE_ID, "192.0.2.10")

    assert raised.value.reason == "address_mismatch"


@pytest.mark.parametrize("ports", [(), (0,), (65536,), (True,), (46060, 46060), [46060]])
def test_discovery_rejects_invalid_port_sets(monkeypatch, ports) -> None:
    _helper_module(
        monkeypatch,
        lambda *args, **kwargs: SimpleNamespace(
            found=True,
            address="192.0.2.20",
            ports=ports,
            error_code=None,
        ),
    )

    with pytest.raises(IdentityEndpointDiscoveryError) as raised:
        discover_identity_endpoint("192.0.2.20", TARGET_DEVICE_ID, "192.0.2.10")

    assert raised.value.reason == "invalid_advertisement"


def test_discovery_fails_closed_when_helper_api_is_unavailable(monkeypatch) -> None:
    _helper_module(monkeypatch, None)

    with pytest.raises(IdentityEndpointDiscoveryError) as raised:
        discover_identity_endpoint("192.0.2.20", TARGET_DEVICE_ID, "192.0.2.10")

    assert raised.value.reason == "unavailable"
