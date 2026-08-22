"""Identity-bound OCF endpoint selection for composite appliance hosts.

This is deliberately separate from the normal known-host port scan.  A host
that exposes several logical OCF devices can advertise more than one secure
endpoint, so callers holding an exact OCF device UUID (``di``) must preserve
the discovery response's ``di -> source address -> secure ports`` binding.

The low-level helper returns candidates, not authenticated identity.  The
config flow still performs a stateless DTLS probe and, after authentication,
checks ``/oic/d.di`` before it accepts the endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from ipaddress import IPv4Address, ip_address
from typing import Final, Literal
from uuid import UUID

IdentityDiscoveryReason = Literal[
    "unavailable",
    "not_found",
    "ambiguous",
    "invalid_advertisement",
    "address_mismatch",
]

_HELPER_ERROR_REASONS: Final[dict[str, IdentityDiscoveryReason]] = {
    "interface_unavailable": "unavailable",
    "no_ocf_response": "not_found",
    "target_not_found": "not_found",
    "ambiguous_target": "ambiguous",
    "target_not_stable": "ambiguous",
    "malformed_ocf_response": "invalid_advertisement",
    "no_secure_ports": "invalid_advertisement",
}


class IdentityEndpointDiscoveryError(Exception):
    """A redacted identity-bound discovery failure."""

    def __init__(self, reason: IdentityDiscoveryReason) -> None:
        self.reason = reason
        super().__init__(f"identity-bound endpoint discovery failed ({reason})")


@dataclass(frozen=True, slots=True, repr=False)
class IdentityEndpoint:
    """Source-bound secure-port candidates without sensitive ``repr`` output."""

    address: str
    ports: tuple[int, ...]

    def __repr__(self) -> str:
        return f"<{type(self).__name__} found=True port_count={len(self.ports)}>"


def normalize_ocf_device_id(value: object) -> str | None:
    """Return one canonical, non-zero OCF device UUID or reject the input."""

    if value is None:
        return None
    if isinstance(value, UUID):
        parsed = value
        if parsed.int == 0:
            raise ValueError("invalid OCF device UUID")
        return str(parsed)
    if isinstance(value, bytes):
        if len(value) == 16:
            parsed = UUID(bytes=value)
            if parsed.int == 0:
                raise ValueError("invalid OCF device UUID")
            return str(parsed)
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid OCF device UUID") from exc
    if not isinstance(value, str):
        raise ValueError("invalid OCF device UUID")
    candidate = (value or "").strip()
    if not candidate:
        return None
    folded = candidate.casefold()
    for prefix in ("urn:uuid:", "uuid:"):
        if folded.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    try:
        parsed = UUID(candidate)
    except (ValueError, AttributeError) as exc:
        raise ValueError("invalid OCF device UUID") from exc
    if parsed.int == 0 or candidate.casefold() != str(parsed):
        raise ValueError("invalid OCF device UUID")
    return str(parsed)


def _literal_ipv4(value: str, *, mismatch: bool = False) -> IPv4Address:
    try:
        parsed = ip_address(value)
    except ValueError as exc:
        reason: IdentityDiscoveryReason = "address_mismatch" if mismatch else "unavailable"
        raise IdentityEndpointDiscoveryError(reason) from exc
    if not isinstance(parsed, IPv4Address):
        reason = "address_mismatch" if mismatch else "unavailable"
        raise IdentityEndpointDiscoveryError(reason)
    return parsed


def discover_identity_endpoint(
    host: str,
    target_device_id: str,
    interface_address: str,
) -> IdentityEndpoint:
    """Discover source-bound secure ports for exactly ``target_device_id``.

    ``host`` and ``interface_address`` must be literal IPv4 addresses.  The
    multicast response is accepted only when its source address is the host
    the user submitted; discovery can select a logical device, but it cannot
    redirect the config flow to another physical host.
    """

    expected_host = _literal_ipv4(host)
    _literal_ipv4(interface_address)
    normalized_device_id = normalize_ocf_device_id(target_device_id)
    if normalized_device_id is None:
        raise IdentityEndpointDiscoveryError("invalid_advertisement")

    try:
        discovery_module = import_module("smartthings_local.protocol.ocf_discovery")
        discover_ocf_secure_ports_multicast = discovery_module.discover_ocf_secure_ports_multicast
    except (ImportError, AttributeError) as exc:
        raise IdentityEndpointDiscoveryError("unavailable") from exc

    try:
        result = discover_ocf_secure_ports_multicast(
            normalized_device_id,
            interface_address=interface_address,
        )
    except Exception as exc:
        raise IdentityEndpointDiscoveryError("unavailable") from exc

    if not result.found:
        reason = _HELPER_ERROR_REASONS.get(result.error_code, "invalid_advertisement")
        raise IdentityEndpointDiscoveryError(reason)
    if not isinstance(result.address, str):
        raise IdentityEndpointDiscoveryError("invalid_advertisement")

    response_host = _literal_ipv4(result.address, mismatch=True)
    if response_host != expected_host:
        raise IdentityEndpointDiscoveryError("address_mismatch")

    raw_ports = result.ports
    if not isinstance(raw_ports, tuple):
        raise IdentityEndpointDiscoveryError("invalid_advertisement")
    ports = tuple(
        sorted(
            {
                port
                for port in raw_ports
                if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535
            }
        )
    )
    if not ports or len(ports) != len(raw_ports):
        raise IdentityEndpointDiscoveryError("invalid_advertisement")
    return IdentityEndpoint(address=str(response_host), ports=ports)
