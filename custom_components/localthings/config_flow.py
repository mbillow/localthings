"""Config flow for Local Things integration."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import errno
import json
import logging
import re
import selectors
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    ObjectSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import cloudcourse
from .const import (
    CLIENTHELLO_PROBE_RETRIES,
    CLIENTHELLO_PROBE_TIMEOUT_S,
    CONF_BYPASS_REMOTE_CONTROL,
    CONF_CA_CERT_PEM,
    CONF_CA_KEY_PEM,
    CONF_CLOUD_COURSES_ENABLED,
    CONF_DEVICE_KEY,
    CONF_DEVICE_TYPE,
    CONF_FINISH_TIME_HYSTERESIS_MINUTES,
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_LEARN_MODES,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_OCF_DEVICE_ID,
    CONF_PORT,
    CONF_SERIAL,
    DEFAULT_CLOUD_COURSES_ENABLED,
    DEFAULT_FINISH_TIME_HYSTERESIS_MINUTES,
    DEFAULT_LEARN_MODES,
    DOMAIN,
    LIVENESS_PROBE_TIMEOUT_S,
    PREFERRED_PROBE_PORTS,
    PROBE_GET_TIMEOUT_S,
    PROBE_MAX_WORKERS,
    PROBE_PORT_RANGE,
    SERVICE_WRITE_RESOURCE,
)
from .learned import persist as learned_persist
from .learned import stored as learned_stored
from .registry.capabilities.laundry import cycle_options, personal_course_labels
from .registry.subdevices import MAIN

_TEXT = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
_MULTILINE = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True))
_HYSTERESIS_MINUTES = NumberSelector(
    NumberSelectorConfig(
        min=0,
        max=30,
        step=1,
        mode=NumberSelectorMode.BOX,
    )
)

# Guided download-cycle setup: how long a round waits for the user to
# select a program, and how often it live-reads /course/vs/0 while doing
# so. The read takes the session lock, so the interval is a few seconds
# rather than sub-second -- fast enough to feel immediate to someone
# standing at the appliance, slow enough not to starve polling.
_CLOUD_WAIT_TIMEOUT_S = 180.0
_CLOUD_PROBE_INTERVAL_S = 3.0

_LOGGER = logging.getLogger(__name__)

_SAMSUNG_CLOUD_HOST = "connect-v2.samsungiotcloud.com"


class CannotConnect(Exception):
    """Base for every probe failure.

    `error_key` selects which message the user sees. The subclasses below
    exist because "cannot connect" used to cover wildly different situations
    (nothing at that IP, cloud-only firmware, a stale held session, a
    rejected certificate) all under one unhelpful message. Raising this base
    class directly is still valid for a failure that can't be narrowed down.
    """

    error_key = "cannot_connect"


class NoResponse(CannotConnect):
    """Nothing at that address answered anything at all."""

    error_key = "no_response"


class PortsClosed(CannotConnect):
    """The host is up and actively refused every port in the range."""

    error_key = "ports_closed"


class NoDtlsServer(CannotConnect):
    """Ports are reachable, but nothing there speaks DTLS."""

    error_key = "no_dtls_server"


class HandshakeTimeout(CannotConnect):
    """A DTLS server is confirmed present but never finished the handshake."""

    error_key = "handshake_timeout"


class CertRejected(CannotConnect):
    """The appliance broke off the handshake over our certificate."""

    error_key = "cert_rejected"


class HandshakeFailed(CannotConnect):
    """The appliance broke off the handshake for a non-certificate reason."""

    error_key = "handshake_failed"


class CloudUnreachable(CannotConnect):
    """Samsung's cloud gateway, which mints the UUID, was unreachable."""

    error_key = "cloud_unreachable"


class UnexpectedResponse(CannotConnect):
    """We authenticated, but the device didn't return a usable description."""

    error_key = "unexpected_response"


class IdentityDiscoveryUnavailable(CannotConnect):
    """Identity-bound discovery is unavailable on this host or installation."""

    error_key = "identity_discovery_unavailable"


class IdentityTargetNotFound(CannotConnect):
    """No stable advertisement matched the requested OCF device UUID."""

    error_key = "identity_target_not_found"


class IdentityTargetAmbiguous(CannotConnect):
    """Discovery could not select one stable endpoint for the requested UUID."""

    error_key = "identity_target_ambiguous"


class IdentityAdvertisementInvalid(CannotConnect):
    """The matching discovery response did not contain a safe endpoint."""

    error_key = "identity_advertisement_invalid"


class IdentityNoDtlsServer(CannotConnect):
    """Advertised endpoint candidates did not identify one DTLS listener."""

    error_key = "identity_no_dtls_server"


class IdentityMismatch(CannotConnect):
    """The authenticated endpoint did not report the requested OCF identity."""

    error_key = "identity_mismatch"


class InvalidCA(Exception):
    error_key = "invalid_ca"


def _fetch_samsung_uuid() -> str:
    """Connect to Samsung's cloud gateway and extract the UUID from its TLS
    cert. Verification is disabled: Samsung's chain has a self-signed cert,
    and we only need to read the UUID from the subject, not verify trust."""
    from cryptography import x509 as _x509

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with (
        socket.create_connection((_SAMSUNG_CLOUD_HOST, 443), timeout=15) as raw,
        ctx.wrap_socket(raw, server_hostname=_SAMSUNG_CLOUD_HOST) as tls,
    ):
        der = tls.getpeercert(binary_form=True)
    if der is None:
        raise RuntimeError(f"No certificate received from {_SAMSUNG_CLOUD_HOST}")
    cert = _x509.load_der_x509_certificate(der)
    for attr in cert.subject:
        if attr.oid == _x509.oid.NameOID.ORGANIZATIONAL_UNIT_NAME and isinstance(attr.value, str):
            m = re.search(r"uuid:([0-9a-f-]+)", attr.value, re.IGNORECASE)
            if m:
                return m.group(1)
    raise RuntimeError(f"UUID not found in {_SAMSUNG_CLOUD_HOST} certificate subject")


def _normalize_pem(text: str) -> str:
    """Strip a pasted PEM's BOM, CRLF endings, and blank lines before
    `cryptography` sees it -- a text editor's copy carries all three and
    fails with an opaque InvalidHeader, while the same file dumped via
    `type` doesn't (issue #291)."""
    text = text.lstrip("\ufeff")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line for line in text.split("\n") if line.strip()]
    return "\n".join(lines)


def _mint_leaf_cert(ca_cert_pem: str, ca_key_pem: str, uuid: str) -> tuple[str, str]:
    """Mint a fresh RSA-2048 leaf cert signed by the CA.

    Returns (fullchain_pem, leaf_key_pem) where fullchain_pem is the leaf cert
    followed by the full CA PEM, suitable for use_certificate_chain_file.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    from cryptography.x509.oid import NameOID

    m = re.search(
        r"(-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----)",
        ca_cert_pem,
        re.DOTALL,
    )
    if not m:
        raise InvalidCA("No certificate found in CA cert PEM")
    try:
        ca_cert = x509.load_pem_x509_certificate(m.group(1).encode())
        ca_key = serialization.load_pem_private_key(ca_key_pem.encode(), password=None)
    except Exception as exc:
        raise InvalidCA(f"Failed to load CA credentials: {exc}") from exc
    if not isinstance(
        ca_key,
        (
            _rsa.RSAPrivateKey,
            ec.EllipticCurvePrivateKey,
            ed25519.Ed25519PrivateKey,
            ed448.Ed448PrivateKey,
            dsa.DSAPrivateKey,
        ),
    ):
        raise InvalidCA(f"CA key is not a signing key (got {type(ca_key).__name__})")

    leaf_key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)

    now = datetime.datetime.now(datetime.UTC)
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COUNTRY_NAME, "KR"),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Samsung Electronics"),
                    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, f"uuid:{uuid}"),
                    x509.NameAttribute(NameOID.COMMON_NAME, f"urn:uuid:{uuid}"),
                ]
            )
        )
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=10 * 365))
        .sign(ca_key, hashes.SHA256())
    )

    leaf_cert_pem = leaf_cert.public_bytes(serialization.Encoding.PEM).decode()
    leaf_key_pem = leaf_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    # Ensure a newline separates the leaf and CA blocks regardless of
    # whether the user's pasted CA PEM had a trailing newline.
    fullchain_pem = leaf_cert_pem.rstrip("\n") + "\n" + ca_cert_pem
    if not fullchain_pem.endswith("\n"):
        fullchain_pem += "\n"
    return fullchain_pem, leaf_key_pem


def _order_candidates(ports: list[int]) -> list[int]:
    """Order live ports so the historically known DTLS ports are tried first."""
    preferred = [p for p in PREFERRED_PROBE_PORTS if p in ports]
    rest = sorted(p for p in ports if p not in PREFERRED_PROBE_PORTS)
    return preferred + rest


# The kernel's way of saying the datagram never had anywhere to go: no route,
# or the host never answered ARP. Distinct from ECONNREFUSED, which is a
# response -- the host is there and told us the port is closed. Both leave a
# port "not live", but mean opposite things about whether anything exists at
# that address.
_UNREACHABLE_ERRNOS = frozenset({errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ENETDOWN})


@dataclass(frozen=True)
class _SweepResult:
    """What the UDP sweep observed, kept as three separate verdicts."""

    live: list[int]  # silent -> open|filtered, worth a handshake
    refused: list[int]  # ICMP port-unreachable -> host is up, port closed
    unreachable: list[int]  # no route / no ARP -> nothing is at that address


def _find_live_ports(host: str, ports: list[int], timeout: float) -> _SweepResult:
    """Fast UDP liveness sweep -- the sweep's own verdict, nothing added.

    UDP is connectionless, but a connected UDP socket surfaces the ICMP
    port-unreachable a closed port returns as ECONNREFUSED on its next recv.
    So we send one probe datagram per port and watch for that error:
    ECONNREFUSED means closed; silence/data means possibly live. The
    in-process equivalent of ``nmap -sU``: takes a nine-port range down to
    the one or two worth a full DTLS handshake, bounded to ``timeout``.

    Deliberately the raw verdict, with no preferred-port rescue folded in
    (that's `_sweep_ports`) -- its shape is evidence about the host, and a
    refusal vs. an unreachable are counted apart rather than both "not
    live" for that reason (see _SweepResult).
    """
    sockets: dict[int, socket.socket] = {}
    sel = selectors.DefaultSelector()
    refused: list[int] = []
    unreachable: list[int] = []
    # A single byte is enough to provoke an ICMP port-unreach from a closed
    # port; a real DTLS ClientHello is unnecessary just to test for life.
    probe = b"\x00"

    def _rule_out(port: int, exc: OSError) -> None:
        (unreachable if exc.errno in _UNREACHABLE_ERRNOS else refused).append(port)

    try:
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            try:
                sock.connect((host, port))
                sock.send(probe)
            except OSError as exc:
                # Failing on the way out means the kernel already knows the
                # datagram can't get there.
                _rule_out(port, exc)
                sock.close()
                continue
            sockets[port] = sock
            sel.register(sock, selectors.EVENT_READ, port)

        # Ports drop out of the selector as they refuse; whatever is still
        # registered when the deadline passes is silent-but-live (a candidate).
        deadline = time.monotonic() + timeout
        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            for key, _ in sel.select(timeout=remaining):
                sock = sockets[key.data]
                try:
                    # Data back means live; an error rules the port out.
                    sock.recv(1)
                except OSError as exc:
                    _rule_out(key.data, exc)
                    sel.unregister(sock)
        live = [key.data for key in sel.get_map().values()]
    finally:
        sel.close()
        for sock in sockets.values():
            with contextlib.suppress(OSError):
                sock.close()

    return _SweepResult(_order_candidates(live), sorted(refused), sorted(unreachable))


def _sweep_ports(host: str, ports: list[int], timeout: float) -> tuple[_SweepResult, list[int]]:
    """`(sweep, candidates)` -- what the host said, and what to actually try.

    The sweep's ICMP-based verdict isn't reliable on every network path --
    issue #192 captured a segregated-VLAN device where it called live ports
    that nmap showed closed, while the port nmap found genuinely open never
    showed up as live at all. Rather than trust a wrong "not live" verdict
    on a port with strong prior evidence, the historically-confirmed ports
    always get a real handshake attempt too (bounded cost: at most
    len(PREFERRED_PROBE_PORTS) extra handshakes, only when the sweep
    disagrees with the prior).

    Both halves are returned, not just the union, since they answer
    different questions: `candidates` is what to hand a handshake, `sweep`
    is what the host actually told us about itself.
    """
    sweep = _find_live_ports(host, ports, timeout)
    rescued = [p for p in PREFERRED_PROBE_PORTS if p in ports and p not in sweep.live]
    return sweep, _order_candidates(sweep.live + rescued)


@dataclass(frozen=True)
class _PortScan:
    """What port detection learned about a host.

    `candidates` is what gets a full DTLS handshake. The other two are the
    evidence behind a failure message: `confirmed` names ports a DTLS
    server was proven on, `swept` is the UDP sweep's own verdict (None
    when the sweep never had to run).
    """

    candidates: list[int]
    confirmed: list[int]
    swept: _SweepResult | None = None


def _clienthello_probe(host: str, port: int):
    """One stateless DTLS ClientHello against `host:port`. Imported lazily
    so an install whose smartthings-local predates the probe (< 0.1.2)
    degrades to the UDP sweep at scan time rather than failing to load the
    config flow at all."""
    from smartthings_local.protocol.dtls_probe import probe

    return probe(
        host,
        port,
        stateless=True,
        timeout=CLIENTHELLO_PROBE_TIMEOUT_S,
        retries=CLIENTHELLO_PROBE_RETRIES,
    )


def _clienthello_scan(host: str, ports: list[int]) -> list[int]:
    """Ports on `host` that answered a DTLS ClientHello -- i.e. ports a real
    DTLS server is listening on (issue #211).

    smartthings-local's stateless probe sends one ClientHello and stops the
    moment the server proves itself with a HelloVerifyRequest, which per RFC
    6347 §4.2.1 the server answers without allocating association state --
    identifies the device's real port in ~1 RTT, far cheaper than throwing N
    full certificate handshakes at it.

    The whole range goes out at once, safely: each probe is bounded by
    CLIENTHELLO_PROBE_TIMEOUT_S rather than DtlsCoapSession's 12s handshake
    timeout, so the pool's shutdown-and-wait on exit costs one probe's
    budget, not the sum of the range.
    """
    with ThreadPoolExecutor(max_workers=min(len(ports), PROBE_MAX_WORKERS)) as ex:
        results = list(ex.map(lambda port: _clienthello_probe(host, port), ports))

    live = []
    for result in results:
        if result.is_dtls_server:
            live.append(result.port)
            _LOGGER.debug("DTLS server on %s:%d (%s)", host, result.port, result)
    return _order_candidates(live)


def _scan_ports(host: str) -> _PortScan:
    """Find the device's DTLS port, preferring proof over absence of evidence.

    The ClientHello probe is authoritative when it finds something: exactly
    one port gets the expensive certificate handshake instead of every port
    the old UDP sweep couldn't rule out (issue #211's 30-40s of 12s handshake
    timeouts).

    It's a gate, not a replacement: when it confirms nothing, we fall back
    to the ICMP-based sweep, which still surfaces a device the probe
    couldn't reach (a network path dropping the ClientHello, or an install
    on smartthings-local < 0.1.2). Issue #192's segregated-VLAN device is
    why that fallback keeps its own preferred-port rescue.
    """
    try:
        confirmed = _clienthello_scan(host, PROBE_PORT_RANGE)
    except Exception as exc:
        _LOGGER.debug("ClientHello probe unavailable (%s); falling back to UDP sweep", exc)
        confirmed = []
    if confirmed:
        _LOGGER.debug("DTLS port(s) confirmed on %s: %s", host, confirmed)
        return _PortScan(confirmed, confirmed)

    sweep, candidates = _sweep_ports(host, PROBE_PORT_RANGE, LIVENESS_PROBE_TIMEOUT_S)
    # No early "nothing here" fast-fail on an empty sweep: the rescue always
    # keeps PREFERRED_PROBE_PORTS as candidates (issue #192), so a real
    # handshake attempt still happens. What the sweep saw is carried along
    # instead, and _classify_handshake_failure turns it into a message once
    # those attempts have actually failed.
    _LOGGER.debug(
        "No DTLS server confirmed on %s; sweep saw live=%s refused=%s unreachable=%s, trying %s",
        host,
        sweep.live,
        sweep.refused,
        sweep.unreachable,
        candidates,
    )
    return _PortScan(candidates, [], sweep)


def _identity_discovery_failure(reason: str) -> CannotConnect:
    """Map a low-level, redacted discovery reason to a stable UI failure."""

    failures: dict[str, type[CannotConnect]] = {
        "unavailable": IdentityDiscoveryUnavailable,
        "not_found": IdentityTargetNotFound,
        "ambiguous": IdentityTargetAmbiguous,
        "invalid_advertisement": IdentityAdvertisementInvalid,
        "address_mismatch": IdentityAdvertisementInvalid,
    }
    failure = failures.get(reason, IdentityAdvertisementInvalid)
    return failure("Identity-bound endpoint discovery could not select a safe endpoint")


def _scan_identity_endpoint(
    host: str,
    target_device_id: str,
    interface_address: str,
) -> _PortScan:
    """Select and statelessly verify only the endpoint bound to one OCF ``di``.

    This path intentionally has no fixed-band or known-host fallback.  Falling
    back after the caller supplied an exact logical-device identity would lose
    the identity-to-source binding that made this path necessary.
    """

    from .endpoint_discovery import (
        IdentityEndpointDiscoveryError,
        discover_identity_endpoint,
    )

    try:
        endpoint = discover_identity_endpoint(host, target_device_id, interface_address)
    except IdentityEndpointDiscoveryError as exc:
        raise _identity_discovery_failure(exc.reason) from exc

    try:
        confirmed = _clienthello_scan(endpoint.address, list(endpoint.ports))
    except Exception as exc:
        raise IdentityDiscoveryUnavailable(
            "Identity-bound endpoint candidates could not be checked"
        ) from exc
    if not confirmed:
        raise IdentityNoDtlsServer(
            "The identity-bound advertisement did not identify a live DTLS endpoint"
        )
    if len(confirmed) != 1:
        raise IdentityTargetAmbiguous(
            "More than one identity-bound endpoint answered the stateless DTLS probe"
        )
    return _PortScan(candidates=confirmed, confirmed=confirmed)


# TLS alerts (RFC 5246 §7.2) that mean "I looked at your certificate and
# said no", as opposed to a protocol/cipher disagreement -- what an
# appliance sends when the CA behind the leaf isn't one it trusts, the
# single most common real setup mistake.
_CERT_ALERTS = frozenset(
    {
        "bad_certificate",
        "unsupported_certificate",
        "certificate_revoked",
        "certificate_expired",
        "certificate_unknown",
        "unknown_ca",
        "access_denied",
        "decrypt_error",
        "certificate_required",
    }
)

# Older smartthings-local (< 0.1.3) rendered a received fatal alert straight
# into the handshake exception's text, e.g. "tlsv1 alert unknown ca" wrapped
# in a ConnectionError -- reading it back told us what the appliance
# objected to. 0.1.3's "redacted typed failures" removed that: connect()'s
# exceptions now carry a fixed, non-sensitive message with the real OpenSSL
# text neither included nor chained (see smartthings_local.errors --
# "backend errors can contain remote endpoints, local paths, or credential
# metadata"). _alert_name is kept as a harmless fallback for exception text
# that does carry it; _resolve_alert below is what actually classifies a
# failure against a current library.
_ALERT_RE = re.compile(r"alert ([a-z0-9 ]+)")


def _alert_name(exc: Exception) -> str | None:
    """The TLS alert an appliance sent, if this failure's exception text
    carried one (only ever true against smartthings-local < 0.1.3)."""
    match = _ALERT_RE.search(str(exc).lower())
    return match.group(1).strip().replace(" ", "_") if match else None


def _diagnostic_alert(host: str, port: int, cert_pem: str, key_pem: str):
    """One opt-in stateful handshake against `port`, using our real
    credentials, so a fatal Alert can be classified from the raw record
    itself rather than parsed out of an exception's text.

    This is the library's diagnose_dtls_handshake -- deliberately not used
    for the primary candidate scan (it commits association state on the
    device, and an orphaned association makes the *next* attempt time out
    per RFC 6347 §4.2.8). Here it only runs once every real candidate has
    already failed, to explain a failure that's happening either way --
    one more orphaned association is a fair trade for a message that says
    why, on a path the user is about to retry regardless.

    Imported lazily, like `_clienthello_probe`, so an install whose
    smartthings-local predates this API degrades to a generic message
    instead of failing to load the config flow at all.
    """
    from smartthings_local.protocol.dtls_probe import diagnose_dtls_handshake

    return diagnose_dtls_handshake(
        host,
        port,
        cert_pem=cert_pem,
        key_pem=key_pem,
        timeout=CLIENTHELLO_PROBE_TIMEOUT_S,
        retries=CLIENTHELLO_PROBE_RETRIES,
    )


def _resolve_alert(exc: Exception, host: str, port: int, cert_pem: str, key_pem: str) -> str | None:
    """The TLS alert `port`'s failed handshake carried, if any -- the
    exception's own text first (cheap, and all an older library ever
    offers), then one bounded diagnostic handshake against a current one
    that redacts it (see _diagnostic_alert)."""
    name = _alert_name(exc)
    if name is not None:
        return name
    try:
        result = _diagnostic_alert(host, port, cert_pem, key_pem)
    except Exception:
        return None
    if result.alert is None:
        return None
    level, name = result.alert
    # ProbeResult.alert is set for a *received* alert record of either
    # level -- fatal (2) means the appliance actually broke off the
    # handshake over it; a warning (1, e.g. close_notify on an otherwise
    # ordinary close) is not evidence of a rejection and must not be read
    # as one. The old exception-text path never had this ambiguity: an
    # OpenSSL exception only ever rendered for a fatal alert.
    return name if level == 2 else None


def _classify_handshake_failure(
    host: str,
    scan: _PortScan,
    failures: list[tuple[int, Exception]],
    alerts: dict[int, str] | None = None,
) -> CannotConnect:
    """Turn "no port worked" into the most specific thing we can honestly
    say, in rough order of how much the evidence tells us: an alert means
    the appliance refused us on purpose (and says whether it was our
    certificate); a confirmed DTLS port that then timed out is likely still
    holding a session from a previous attempt; otherwise the sweep's own
    shape is the evidence.

    `alerts` is the per-port classification `_handshake_and_read` already
    resolved (exception text, or a diagnostic handshake -- see
    _resolve_alert); a caller with only raw failures (or an older library)
    still gets `_alert_name`'s exception-text reading as a fallback.
    """
    resolved = dict(alerts or {})
    for port, exc in failures:
        resolved.setdefault(port, _alert_name(exc))
    alert_names = [name for name in resolved.values() if name]
    cert_alerts = [name for name in alert_names if name in _CERT_ALERTS]
    if cert_alerts:
        return CertRejected(f"{host} rejected our certificate (alert {cert_alerts[0]})")
    if alert_names:
        return HandshakeFailed(f"{host} refused the DTLS handshake (alert {alert_names[0]})")
    if scan.confirmed:
        return HandshakeTimeout(
            f"DTLS server confirmed on {host}:{scan.confirmed} but the handshake never completed"
        )

    sweep = scan.swept
    if sweep is None:
        return CannotConnect(f"no port on {host} completed a handshake")
    if sweep.unreachable and not sweep.refused:
        # Nothing was ever asked -- the kernel never got the datagrams off
        # the host, so "ports closed" would be exactly wrong.
        return NoResponse(f"{host} is unreachable (ports {sweep.unreachable})")
    if not sweep.live:
        # Every port answered ICMP port-unreachable: something is there and
        # not exposing the local API.
        return PortsClosed(
            f"{host} refused every port in {PROBE_PORT_RANGE[0]}-{PROBE_PORT_RANGE[-1]}"
        )
    if len(sweep.live) == len(PROBE_PORT_RANGE):
        # Not one refusal across a nine-port range -- a host that's
        # actually there answers for at least some of it.
        return NoResponse(f"nothing at {host} responded on any probed port")
    return NoDtlsServer(f"ports on {host} are reachable but none answered a DTLS handshake")


def _mint_credentials(ca_cert_pem: str, ca_key_pem: str) -> tuple[str, str]:
    """Fetch the current UUID from Samsung's cloud and mint a leaf cert for it."""
    _LOGGER.debug("Fetching Samsung cloud UUID from %s", _SAMSUNG_CLOUD_HOST)
    try:
        uuid = _fetch_samsung_uuid()
    except Exception as exc:
        _LOGGER.debug("UUID fetch failed: %s", exc, exc_info=True)
        raise CloudUnreachable(f"Failed to fetch Samsung UUID: {exc}") from exc
    _LOGGER.debug("Got UUID: %s", uuid)

    _LOGGER.debug("Minting leaf cert for UUID %s", uuid)
    try:
        fullchain_pem, leaf_key_pem = _mint_leaf_cert(ca_cert_pem, ca_key_pem, uuid)
    except InvalidCA:
        _LOGGER.debug("CA credentials invalid", exc_info=True)
        raise
    except Exception as exc:
        _LOGGER.debug("Leaf cert minting failed: %s", exc, exc_info=True)
        raise CannotConnect(f"Failed to mint leaf cert: {exc}") from exc
    _LOGGER.debug("Leaf cert minted successfully")
    return fullchain_pem, leaf_key_pem


def _read_device(
    sess,
    host: str,
    port: int,
    expected_device_id: str | None = None,
) -> dict:
    """Resolve this device's identity over an already-connected session.

    /oic/d before /device/0, deliberately: the device's own OCF device-type
    declaration is the primary detection signal when a board populates it
    (see registry/by_type's resolve()), and read_identity's three small
    GETs settle it long before the blockwise /device/0 dump lands.
    read_identity is defensive on every GET, so a device answering neither
    /oic/p nor /oic/d falls through to the model-string/resource-signature
    path.

    Everything the entry needs to name and key the device comes from here,
    so the coordinator never has to mint a registry key from a placeholder
    (issue #236).
    """
    import cbor2

    from .registry.batch import parse_device0_batch
    from .registry.by_type import resolve as resolve_registry
    from .registry.identity import (
        read_identity,
        resolve_device_key,
        resolve_model,
        resolve_serial,
    )

    identity = read_identity(sess, None)
    if expected_device_id is not None:
        from .endpoint_discovery import normalize_ocf_device_id

        raw_device = identity.raw.get("/oic/d")
        raw_device_id = (
            raw_device.get("di")
            if isinstance(raw_device, dict) and "di" in raw_device
            else identity.device_id
        )
        try:
            actual_device_id = normalize_ocf_device_id(raw_device_id)
        except ValueError:
            actual_device_id = None
        if actual_device_id != expected_device_id:
            # Never fall back to pi, serial, or host here.  The caller chose
            # this path specifically to bind one logical OCF device, and the
            # authenticated /oic/d response is the authoritative final gate.
            raise IdentityMismatch(
                "The authenticated endpoint did not report the requested OCF device identity"
            )

    code, payload = sess.get(["device", "0"], timeout=PROBE_GET_TIMEOUT_S)
    if code != 0x45 or not payload:
        # Authenticated fine, so this isn't a connectivity or credentials
        # problem -- whatever is on this port just isn't an appliance whose
        # /device/0 we understand.
        raise UnexpectedResponse(
            f"{host}:{port} answered /device/0 with {code >> 5}.{code & 0x1F:02d} "
            f"({code:#04x}), payload {len(payload or b'')} bytes"
        )
    body = cbor2.loads(payload)
    resources = parse_device0_batch(body) if isinstance(body, list) else {}

    info = resources.get("/information/vs/0", {})
    registry = resolve_registry(resources, device_types=identity.device_types)
    raw_serial = info.get("x.com.samsung.da.serialNum")
    return {
        "port": port,
        # Resolved through the same helpers _run_discovery uses, so the device
        # the coordinator registers up front is the one discovery would have
        # produced -- no rename, and no re-key, once the first poll lands.
        #
        # `device_key` is what the entry is actually keyed on; the serial is
        # kept alongside it because the coordinator corroborates a later
        # change of key against it (issue #381). read_identity has already
        # fetched /oic/p and /oic/d above, so this costs no extra round trip.
        "device_key": expected_device_id or resolve_device_key(identity, raw_serial, host),
        "serial": resolve_serial(raw_serial, host),
        "model": resolve_model(info.get("x.com.samsung.da.modelNum", ""), identity),
        "manufacturer": identity.manufacturer or "Samsung",
        "device_type_name": registry.name if registry is not None else None,
        "device_type_recognized": registry is not None,
    }


def _diagnose_failures(
    host: str,
    scan: _PortScan,
    failures: list[tuple[int, Exception]],
    cert_pem: str,
    key_pem: str,
) -> dict[int, str]:
    """At most one diagnostic handshake (see _diagnostic_alert) across every
    port `_handshake_and_read` just gave up on -- not one per port.

    Called only after that loop has fully exhausted `scan.candidates`, never
    interleaved with it: `_diagnostic_alert`'s own docstring says the extra
    orphaned association it costs is a fair trade "on a path the user is
    about to retry regardless" -- true for the retry `_probe_and_validate`
    itself makes on a CertRejected (a fresh `_handshake_and_read` call
    against this same `scan`), but only if that retry's real handshake
    attempts are the ones landing on a clean slate. Running the diagnostic
    per candidate mid-loop would pollute exactly the port(s) that retry is
    about to reattempt; running several of them multiplies both the latency
    (each is its own bounded handshake) and the pollution for no extra
    classification value, since _classify_handshake_failure only ever needs
    one alert to decide.

    Targets a confirmed-live port over an unconfirmed sweep candidate --
    the one actually worth spending the extra handshake on.
    """
    if not failures:
        return {}
    by_port = dict(failures)
    port = next((p for p in scan.confirmed if p in by_port), next(iter(by_port)))
    alert = _resolve_alert(by_port[port], host, port, cert_pem, key_pem)
    return {port: alert} if alert is not None else {}


def _handshake_and_read(
    host: str,
    scan: _PortScan,
    cert_pem: str,
    key_pem: str,
    expected_device_id: str | None = None,
) -> dict:
    """Handshake each candidate in turn, returning the first device that answers."""
    from smartthings_local.protocol.dtls_session import DtlsCoapSession

    failures: list[tuple[int, Exception]] = []
    for port in scan.candidates:
        sess = None
        try:
            sess = DtlsCoapSession(host, port, cert_pem=cert_pem, key_pem=key_pem)
            sess.connect()
            sess.start_reader()
            return _read_device(sess, host, port, expected_device_id)
        except CannotConnect:
            # The device answered, just not with something we can use --
            # trying the remaining ports can't improve on that.
            raise
        except Exception as exc:
            failures.append((port, exc))
            _LOGGER.debug("port %d failed: %s", port, exc)
        finally:
            if sess is not None:
                with contextlib.suppress(Exception):
                    sess.close()
    alerts = _diagnose_failures(host, scan, failures, cert_pem, key_pem)
    raise _classify_handshake_failure(host, scan, failures, alerts)


def _probe_and_validate(
    host: str,
    ca_cert_pem: str,
    ca_key_pem: str,
    existing_leaf: tuple[str, str] | None = None,
    target_device_id: str | None = None,
    interface_address: str | None = None,
) -> dict:
    """Find the device's port, authenticate to it, and resolve its identity.

    Port detection runs first and needs no credentials, so an unreachable
    host fails here rather than after a round trip to Samsung's cloud.

    `existing_leaf` is another entry's already-minted leaf (issue #211).
    Every appliance accepts the same leaf, so adding a second device can
    skip the fetch and mint entirely -- independent of Samsung-cloud
    reachability, not merely faster. If that reused leaf turns out to be
    stale (the UUID does rotate), a confirmed-live device rejecting it
    re-mints and retries once, so the reuse stays self-correcting.
    """
    if target_device_id is None:
        scan = _scan_ports(host)
    else:
        if interface_address is None:
            raise IdentityDiscoveryUnavailable(
                "No local IPv4 interface was available for identity-bound discovery"
            )
        scan = _scan_identity_endpoint(host, target_device_id, interface_address)

    if existing_leaf is not None:
        cert_pem, key_pem = existing_leaf
        _LOGGER.debug("Reusing the leaf certificate from an existing entry")
    else:
        cert_pem, key_pem = _mint_credentials(ca_cert_pem, ca_key_pem)

    try:
        info = _handshake_and_read(host, scan, cert_pem, key_pem, target_device_id)
    except CertRejected:
        # The only failure a fresh certificate can fix, and only worth a
        # second pass when the certificate wasn't freshly minted already.
        if existing_leaf is None:
            raise
        _LOGGER.debug("Reused leaf rejected by %s; re-minting and retrying", host)
        cert_pem, key_pem = _mint_credentials(ca_cert_pem, ca_key_pem)
        info = _handshake_and_read(host, scan, cert_pem, key_pem, target_device_id)

    return {**info, "leaf_cert_pem": cert_pem, "leaf_key_pem": key_pem}


class LocalThingsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    # v3 relabels the particulate sensors' recorded statistics; a freshly
    # created entry has none to relabel, so it starts at the migrated
    # version rather than walking through v2 (see async_migrate_entry).
    # v4 keys the entry on the OCF device UUID (issue #381), which the probe
    # below resolves up front -- so a new entry is already on the v4 shape
    # and has nothing to re-key either.
    VERSION = 4

    def __init__(self) -> None:
        self._host: str = ""
        self._ca_cert_pem: str = ""
        self._ca_key_pem: str = ""
        self._pending_info: dict | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> LocalThingsOptionsFlow:
        return LocalThingsOptionsFlow()

    def _create_entry(self, info: dict) -> ConfigFlowResult:
        """Persist everything the probe resolved, identity included.

        The identity fields aren't decoration: the coordinator seeds
        `device_key` and its DeviceInfo from them at construction time,
        so entity unique_ids are correct from the first entity that
        registers, even if the first poll is slow or fails (issue #236).
        """
        from .registry.identity import device_display_name

        return self.async_create_entry(
            title=f"{device_display_name(info['device_type_name'], '')} ({self._host})",
            data={
                CONF_HOST: self._host,
                CONF_PORT: info["port"],
                CONF_CA_CERT_PEM: self._ca_cert_pem,
                CONF_CA_KEY_PEM: self._ca_key_pem,
                CONF_LEAF_CERT_PEM: info["leaf_cert_pem"],
                CONF_LEAF_KEY_PEM: info["leaf_key_pem"],
                CONF_DEVICE_KEY: info["device_key"],
                CONF_SERIAL: info["serial"],
                CONF_MODEL: info["model"],
                CONF_MANUFACTURER: info["manufacturer"],
                CONF_DEVICE_TYPE: info["device_type_name"],
            },
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        existing = self.hass.config_entries.async_entries(DOMAIN)
        has_creds = bool(existing)

        errors: dict[str, str] = {}

        if has_creds:
            schema = vol.Schema(
                {
                    vol.Required(CONF_HOST): _TEXT,
                    vol.Optional(CONF_OCF_DEVICE_ID): _TEXT,
                }
            )
            step_id = "user_reuse"
        else:
            schema = vol.Schema(
                {
                    vol.Required(CONF_HOST): _TEXT,
                    vol.Optional(CONF_OCF_DEVICE_ID): _TEXT,
                    vol.Required(CONF_CA_CERT_PEM): _MULTILINE,
                    vol.Required(CONF_CA_KEY_PEM): _MULTILINE,
                }
            )
            step_id = "user"

        if user_input is not None:
            from .endpoint_discovery import normalize_ocf_device_id

            try:
                target_device_id = normalize_ocf_device_id(user_input.get(CONF_OCF_DEVICE_ID))
            except ValueError:
                errors[CONF_OCF_DEVICE_ID] = "invalid_ocf_device_id"
                return self.async_show_form(
                    step_id=step_id,
                    data_schema=self.add_suggested_values_to_schema(schema, user_input),
                    errors=errors,
                )

            self._host = user_input[CONF_HOST].strip()
            existing_leaf = None
            if has_creds:
                self._ca_cert_pem = existing[0].data[CONF_CA_CERT_PEM]
                self._ca_key_pem = existing[0].data[CONF_CA_KEY_PEM]
                leaf_cert = existing[0].data.get(CONF_LEAF_CERT_PEM)
                leaf_key = existing[0].data.get(CONF_LEAF_KEY_PEM)
                if leaf_cert and leaf_key:
                    existing_leaf = (leaf_cert, leaf_key)
            else:
                # Normalized here, not just before minting: this is also
                # what gets stored and reused to re-mint the leaf later.
                self._ca_cert_pem = _normalize_pem(user_input[CONF_CA_CERT_PEM])
                self._ca_key_pem = _normalize_pem(user_input[CONF_CA_KEY_PEM])

            try:
                if target_device_id is None:
                    # Preserve the existing scan and executor call exactly for
                    # ordinary setup.  Identity-aware discovery is opt-in and
                    # cannot add latency to this path.
                    info = await self.hass.async_add_executor_job(
                        _probe_and_validate,
                        self._host,
                        self._ca_cert_pem,
                        self._ca_key_pem,
                        existing_leaf,
                    )
                else:
                    from homeassistant.components import network

                    try:
                        interface_address = await network.async_get_source_ip(
                            self.hass,
                            target_ip=self._host,
                        )
                    except Exception as exc:
                        raise IdentityDiscoveryUnavailable(
                            "No local IPv4 route was available for identity-bound discovery"
                        ) from exc
                    info = await self.hass.async_add_executor_job(
                        _probe_and_validate,
                        self._host,
                        self._ca_cert_pem,
                        self._ca_key_pem,
                        existing_leaf,
                        target_device_id,
                        interface_address,
                    )
            except (CannotConnect, InvalidCA) as exc:
                # Every probe failure carries the message that fits it (see
                # CannotConnect); the log line is where the specifics live.
                _LOGGER.warning("Probe of %s failed [%s]: %s", self._host, exc.error_key, exc)
                errors["base"] = exc.error_key
            except Exception:
                _LOGGER.exception("Unexpected error during device probe")
                errors["base"] = "unknown"
            else:
                # An entry created before v4 still carries the serial-keyed
                # unique_id until its first *live* poll adopts the UUID
                # (coordinator._resolve_identity) -- which can be a long
                # while for an appliance that is off, since an entry loads
                # from its snapshot in the meantime (issue #295). The UUID
                # check below can't see such an entry, so re-adding this
                # very appliance during that window would be waved through
                # as a second entry; the two would then collide the moment
                # the older one re-keyed, and rekey_entry resolves a
                # collision by *deleting* the duplicate rows -- taking the
                # original entry's entity_ids, history and automations with
                # them. Matched on the legacy key together with the host, so
                # issue #381's two units (same serial, different addresses)
                # stay separable.
                legacy_unique_id = f"localthings_{info['serial']}"
                if any(
                    other.unique_id == legacy_unique_id
                    and other.data.get(CONF_HOST) == self._host
                    and CONF_DEVICE_KEY not in other.data
                    for other in existing
                ):
                    return self.async_abort(reason="already_configured")
                # Keyed on the OCF device UUID rather than the serialNum
                # (issue #381): two units of a model that ship the same
                # well-formed serial are indistinguishable here otherwise,
                # and the second one is turned away as already configured.
                await self.async_set_unique_id(f"localthings_{info['device_key']}")
                self._abort_if_unique_id_configured()
                if info["device_type_recognized"]:
                    return self._create_entry(info)
                self._pending_info = info
                return await self.async_step_confirm_unknown_type()

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_user_reuse(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the localized host-only form for additional appliances."""
        return await self.async_step_user(user_input)

    async def async_step_confirm_unknown_type(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Shown only when the probe already knows the device type is unrecognized."""
        info = self._pending_info or {}
        if user_input is not None:
            assert self._pending_info is not None
            return self._create_entry(self._pending_info)
        return self.async_show_form(
            step_id="confirm_unknown_type",
            data_schema=vol.Schema({}),
            # The probe already knows the board string detection failed on;
            # showing it here means a user filing the device-support issue
            # this step asks for can quote it without digging through logs.
            description_placeholders={"model": info.get("model") or "unknown"},
        )


class LocalThingsOptionsFlow(config_entries.OptionsFlow):
    """Per-device options: the remote-control-off write-block override
    (issue #54) plus a debug panel for writing an arbitrary body to an
    arbitrary resource href, so a user can pin down device-specific write
    behavior without waiting on a new release.

    The remote-control override exists because not every model actually
    enforces the block most devices do, so a user who's confirmed their
    device accepts writes anyway can turn it off for just that device. The
    debug panel goes further, bypassing that block (and every write_fn/
    validate_fn) entirely.
    """

    def __init__(self) -> None:
        self._debug_href: str = ""
        self._debug_result: tuple[int, dict] | None = None
        # Guided download-cycle setup. `_cloud_task` is created once per
        # round and reused across re-entries (Home Assistant re-enters a
        # progress step while its spinner is up).
        self._cloud_task: asyncio.Task[str | None] | None = None
        self._cloud_slot: str | None = None
        self._cloud_baseline: str | None = None

    def _coordinator(self):
        return self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["settings", "forget_learned_modes", "debug_write"]
        # Only offered on an appliance that actually advertises downloaded
        # programs (issue #342) -- every other device would get a menu entry
        # leading to an empty screen.
        coord = self._coordinator()
        if coord is not None and cloudcourse.supports_cloud_courses(
            coord.cloud_course_rep(), cycle_options(coord.canonical_resources(MAIN))
        ):
            menu.insert(1, "cloud_courses")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BYPASS_REMOTE_CONTROL,
                        default=self.config_entry.options.get(CONF_BYPASS_REMOTE_CONTROL, False),
                    ): bool,
                    vol.Required(
                        CONF_FINISH_TIME_HYSTERESIS_MINUTES,
                        default=self.config_entry.options.get(
                            CONF_FINISH_TIME_HYSTERESIS_MINUTES,
                            DEFAULT_FINISH_TIME_HYSTERESIS_MINUTES,
                        ),
                    ): _HYSTERESIS_MINUTES,
                    vol.Required(
                        CONF_LEARN_MODES,
                        default=self.config_entry.options.get(
                            CONF_LEARN_MODES, DEFAULT_LEARN_MODES
                        ),
                    ): bool,
                    vol.Required(
                        CONF_CLOUD_COURSES_ENABLED,
                        default=self.config_entry.options.get(
                            CONF_CLOUD_COURSES_ENABLED, DEFAULT_CLOUD_COURSES_ENABLED
                        ),
                    ): bool,
                }
            ),
        )

    async def async_step_forget_learned_modes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm-and-clear for the learned-mode store (issue #327).

        The point of learning is that it's permanent, so a code learned
        from a one-off firmware hiccup would otherwise sit in an option
        list forever. An empty schema renders as a plain confirmation
        form; the description lists what's about to be forgotten.
        """
        coord = self._coordinator()
        # learned.py owns the entry key and the persisted shape, so this
        # step never parses or writes it itself -- including on an unloaded
        # entry, where a malformed record would otherwise abort the one
        # screen that can clear it.
        learned = (
            coord.learned_snapshot() if coord is not None else learned_stored(self.config_entry)
        )
        codes = sorted({code for codes in learned.values() for code in codes})

        if user_input is not None:
            if coord is not None:
                coord.forget_learned_modes()
            else:
                learned_persist(self.hass, self.config_entry, {})
            return self.async_create_entry(data=dict(self.config_entry.options))

        return self.async_show_form(
            step_id="forget_learned_modes",
            data_schema=vol.Schema({}),
            description_placeholders={"codes": ", ".join(codes) if codes else "(none)"},
        )

    async def async_step_cloud_courses(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Entry point for download-cycle setup (issue #342).

        Guided setup is offered first because it is the only version of this
        that a first-time user can complete confidently: it asks about a
        program in the moment they select it, rather than about a list of hex
        ids some time later. The bulk form stays for renaming afterwards,
        which guided setup is bad at.

        Setup still works with cloud_courses_enabled off (see
        CONF_CLOUD_COURSES_ENABLED's own comment for why) -- someone who
        wants to name one cycle without turning the feature fully on for
        everything else still can (issue #364). The catalog's own
        description covers that state directly rather than through a
        description_placeholder built here: a placeholder is a literal
        substitution HA never runs back through translation, so an
        English sentence assembled in Python would show untranslated text
        in every other locale -- unlike the SmartThings screen names
        quoted elsewhere in this catalog, which stay English everywhere
        because that's a third-party app's own label, not one of ours.
        """
        return self.async_show_menu(
            step_id="cloud_courses",
            menu_options=["cloud_guided", "cloud_manual"],
        )

    @callback
    def async_remove(self) -> None:
        """Stop probing when the flow goes away.

        Closing the dialog is the documented way to leave guided setup, so it
        has to actually stop: an abandoned round would otherwise go on
        live-reading /course/vs/0 every few seconds until its timeout, taking
        the session lock each time, for a user who has walked away.
        """
        if self._cloud_task is not None and not self._cloud_task.done():
            self._cloud_task.cancel()
        self._cloud_task = None

    async def async_step_cloud_guided(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start (or restart) a guided discovery round."""
        coord = self._coordinator()
        if coord is None:
            return self.async_abort(reason="not_loaded")
        self._cloud_task = None
        self._cloud_slot = None
        # Baseline: whatever is loaded right now. The round completes when
        # the appliance moves off it, so the program the user has *already*
        # selected can't immediately re-trigger and loop the flow.
        self._cloud_baseline = cloudcourse.loaded_slot(coord.cloud_course_rep())
        return await self.async_step_cloud_wait()

    async def async_step_cloud_wait(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for the user to select a different downloaded program.

        The task is created once and reused across re-entries -- Home
        Assistant polls this step while the spinner is up, and building a
        fresh task each time would restart the wait forever.
        """
        coord = self._coordinator()
        if coord is None:
            return self.async_abort(reason="not_loaded")

        if self._cloud_task is None:
            self._cloud_task = self.hass.async_create_task(
                self._await_cloud_selection(coord), eager_start=False
            )
        if not self._cloud_task.done():
            return self.async_show_progress(
                step_id="cloud_wait",
                progress_action="cloud_wait",
                progress_task=self._cloud_task,
                description_placeholders=self._cloud_progress_placeholders(coord),
            )

        self._cloud_slot = self._cloud_task.result()
        self._cloud_task = None
        if self._cloud_slot is None:
            return self.async_show_progress_done(next_step_id="cloud_timeout")
        return self.async_show_progress_done(next_step_id="cloud_name")

    async def _await_cloud_selection(self, coord) -> str | None:
        """Poll until the loaded program changes; None on timeout."""
        deadline = time.monotonic() + _CLOUD_WAIT_TIMEOUT_S
        while time.monotonic() < deadline:
            slot = await coord.async_probe_cloud_courses()
            # Only a slot the store actually recorded. The probe reports
            # whatever payload is loaded, while observe() declines one whose
            # slot the device doesn't advertise -- offering to name that would
            # take a name and silently discard it, since there is no record to
            # hang it on and no payload to replay.
            if (
                slot is not None
                and slot != self._cloud_baseline
                and coord.cloud_courses.snapshot()["slots"].get(slot)
            ):
                return slot
            await asyncio.sleep(_CLOUD_PROBE_INTERVAL_S)
        return None

    def _cloud_progress_placeholders(self, coord) -> dict[str, str]:
        """Counts plus the names assigned so far.

        Listing them is what makes a nine-program walk followable -- it is
        the only orientation available, since the programs still to do are
        unnamed by definition. Shown while naming too, where it doubles as
        duplicate avoidance: the form rejects a repeated name, so seeing the
        others first beats being bounced.

        In the appliance's own advertised order, which is at least a stable
        order, without numbering them -- whether that order matches the dial
        is plausible but unverified, and implying it would be worse than
        saying nothing.
        """
        rep = coord.cloud_course_rep()
        courses = cycle_options(coord.canonical_resources(MAIN))
        record = coord.cloud_courses.snapshot()
        slots = cloudcourse.cloud_slots(rep, courses)
        names = [n for s in slots if (n := (record["slots"].get(s) or {}).get("name"))]
        return {
            "named": str(len(names)),
            "total": str(len(slots)),
            "named_list": ", ".join(names) if names else "none yet",
        }

    async def async_step_cloud_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name the program the user just selected.

        Persists immediately rather than batching to the end of the flow, so
        closing the dialog at any point is a clean "save and exit" -- there is
        no pending work to lose, and reopening resumes from the store.
        """
        coord = self._coordinator()
        if coord is None or self._cloud_slot is None:
            return self.async_abort(reason="not_loaded")
        slot = self._cloud_slot
        existing = (coord.cloud_courses.snapshot()["slots"].get(slot) or {}).get("name", "")

        if user_input is not None:
            # Rebuilt into the shared validator's shape. `download_course`
            # is forwarded only when this form actually carried it, so its
            # absence still means "not asked about" rather than "clear it".
            payload: dict[str, Any] = {f"name_{slot}": str(user_input.get("name", "")).strip()}
            if "download_course" in user_input:
                payload["download_course"] = user_input["download_course"]
            errors = self._apply_cloud_course_names(coord, [slot], payload)
            if errors:
                return self._cloud_name_form(coord, slot, existing, errors=errors)
            # Straight back to waiting: the appliance is still sitting on this
            # program, and the next round baselines on it, so there is nothing
            # to click through.
            self._cloud_baseline = slot
            return await self.async_step_cloud_wait()

        return self._cloud_name_form(coord, slot, existing)

    def _cloud_name_form(
        self, coord, slot: str, existing: str, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """One text field, with the copy switched on whether this program is
        already set up. Re-selecting one is not an error -- it is how someone
        checks their work -- so it gets an edit form rather than a rejection,
        and the counter deliberately does not move.

        The Download course joins the form the first time round, and only
        until it is confirmed. Guided setup would otherwise finish having
        collected names but no course, and a program needs both before it can
        be offered -- so the whole walk would produce nothing selectable. It
        is asked here rather than up front because this is the first moment
        there is evidence to prefill: the user has just loaded a program, so
        the course showing alongside it is the Download one.
        """
        placeholders = self._cloud_progress_placeholders(coord)
        placeholders["slot"] = slot
        placeholders["remaining"] = (
            coord.resource("/operational/state/vs/0").get("x.com.samsung.da.remainingTime") or "--"
        )
        fields: dict[Any, Any] = {vol.Optional("name", default=existing): _TEXT}
        if not coord.cloud_courses.snapshot()["download_course"]:
            fields[
                vol.Optional("download_course", description={"suggested_value": self._cloud_course})
            ] = self._cloud_course_selector(coord)
        return self.async_show_form(
            step_id="cloud_name",
            data_schema=vol.Schema(fields),
            errors=errors or {},
            description_placeholders=placeholders,
            last_step=False,
        )

    @property
    def _cloud_course(self) -> str | None:
        """The best observed candidate, narrowed to what the selector offers.

        Unfiltered, a candidate the appliance's own course list no longer
        contains would prefill a dropdown that rejects it, and the form would
        fail validation on a value the user never chose.
        """
        coord = self._coordinator()
        if coord is None:
            return None
        available = cycle_options(coord.canonical_resources(MAIN))
        return next((c for c in coord.cloud_courses.download_candidates() if c in available), None)

    def _cloud_course_selector(self, coord):
        """The appliance's own course codes, observed candidates first.

        custom_value stays off deliberately: whatever lands here becomes the
        Course_ token of a real write, and a typed-in code the appliance
        doesn't offer would start something nobody chose.
        """
        available = cycle_options(coord.canonical_resources(MAIN))
        candidates = [c for c in coord.cloud_courses.download_candidates() if c in available]
        ordered = candidates + [c for c in available if c not in candidates]
        return SelectSelector(
            SelectSelectorConfig(
                options=ordered,
                custom_value=False,
                mode=SelectSelectorMode.DROPDOWN,
            )
        )

    async def async_step_cloud_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Nothing was selected in time. Offer another round rather than
        dropping the user out of the flow -- and an explicit finish, for
        anyone who doesn't think to close the dialog."""
        coord = self._coordinator()
        if coord is None:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(
            step_id="cloud_timeout",
            menu_options=["cloud_guided", "cloud_finish"],
            description_placeholders=self._cloud_progress_placeholders(coord),
        )

    async def async_step_cloud_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_create_entry(data=dict(self.config_entry.options))

    async def async_step_cloud_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name the cloud "Download" programs this appliance has (issue #342).

        The device advertises how many downloaded programs it holds but never
        what any of them is called, and only ever exposes the replay payload
        for the one currently loaded. So this screen can only offer the ones
        already seen loaded, and asks the user for the names -- the appliance
        has no name to give and inventing one is not an option (the same rule
        that governs unrecognized local course codes).

        Also confirms which course code means Download. It is auto-detected
        by observation, but never used for a write until confirmed here: the
        options array replaces tokens by prefix and never evicts them, so a
        stale program token can be reported alongside an unrelated course and
        make an ordinary wash cycle look like the Download one. Writing the
        wrong code would start the wrong cycle.
        """
        coord = self._coordinator()
        if coord is None:
            return self.async_abort(reason="not_loaded")

        store = coord.cloud_courses
        rep = coord.cloud_course_rep()
        record = store.snapshot()
        slots = record["slots"]
        advertised = cloudcourse.cloud_slots(rep, cycle_options(coord.canonical_resources(MAIN)))
        # Learned slots keep the appliance's own ordering; anything learned
        # but no longer advertised still gets a row so a name isn't stranded.
        known = [s for s in advertised if s in slots] + [s for s in slots if s not in advertised]

        if user_input is not None:
            errors = self._apply_cloud_course_names(coord, known, user_input)
            if not errors:
                return self.async_create_entry(data=dict(self.config_entry.options))
            return self._cloud_courses_form(coord, known, advertised, errors=errors)

        return self._cloud_courses_form(coord, known, advertised)

    def _apply_cloud_course_names(self, coord, known, user_input) -> dict[str, str]:
        """Validate and store the submitted names + Download course code.

        The select maps a chosen label back to a raw value by matching display
        text, so two options sharing a label resolve to whichever comes first.
        Two sources of collision are checkable here and both are rejected:
        the user's own names against each other, and against the appliance's
        personal-course labels, which the device reports verbatim and the
        select renders as-is.

        A collision with a *translated* local course name is deliberately not
        checked. The catalog this process can read is English (catalog.py),
        while what the user actually sees is localized in the frontend -- so
        checking it would reject "Cotton" for a German user whose dropdown
        says "Baumwolle", and still miss the real collision when they type
        "Baumwolle". Wrong in both directions outside one locale, against an
        outcome the option ordering already makes deterministic (local
        courses come first, so a shared label resolves to the real cycle).
        """
        names = {slot: str(user_input.get(f"name_{slot}", "")).strip() for slot in known}
        stored_slots = coord.cloud_courses.snapshot()["slots"]
        taken = {name.casefold() for name in self._device_course_names(coord)}
        # Programs this form isn't editing. The bulk form edits every slot at
        # once so this adds nothing there, but guided setup submits one at a
        # time -- without it, naming two programs the same was accepted, and
        # the select resolves a shared label to whichever option comes first,
        # so picking the second would run the first one's payload.
        taken |= {
            record["name"].casefold()
            for slot, record in stored_slots.items()
            if record["name"] and slot not in known
        }
        for name in names.values():
            if not name:
                continue
            if name.casefold() in taken:
                return {"base": "cloud_course_name_duplicate"}
            taken.add(name.casefold())

        # Absent means "this form didn't ask" -- the guided name form drops
        # the field once the course is confirmed -- which must leave the
        # stored value alone rather than clearing it. A program is only
        # offerable when both a name and the course are set, so clearing it
        # here would make naming things remove them from the cycle list.
        if "download_course" not in user_input:
            coord.apply_cloud_courses(names)
            return {}

        # Belt and braces over the selector's own custom_value=False: this
        # value becomes the Course_ token of a real write, so it is checked
        # against the appliance's own course list here too, where the store
        # is actually updated.
        course = user_input.get("download_course") or None
        if course is not None and course not in cycle_options(coord.canonical_resources(MAIN)):
            return {"base": "cloud_course_unknown_course"}

        coord.apply_cloud_courses(names, course)
        return {}

    def _device_course_names(self, coord) -> set[str]:
        """Course names this appliance reports itself.

        Only the personal-course labels: the device sends these as text and
        the select renders them unchanged, so they are the same string in
        every locale and can be compared against safely. See the caller for
        why translated course names are not included.
        """
        resources = coord.canonical_resources(MAIN)
        personal = personal_course_labels(resources)
        return {name for code in cycle_options(resources) if (name := personal.get(code.upper()))}

    def _cloud_courses_form(
        self, coord, known, advertised, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        store = coord.cloud_courses
        record = store.snapshot()
        slots = record["slots"]

        fields: dict[Any, Any] = {}
        for slot in known:
            fields[vol.Optional(f"name_{slot}", default=slots.get(slot, {}).get("name", ""))] = (
                _TEXT
            )

        suggested = record["download_course"] or self._cloud_course
        fields[vol.Optional("download_course", description={"suggested_value": suggested})] = (
            self._cloud_course_selector(coord)
        )

        pending = [s for s in advertised if s not in slots]
        return self.async_show_form(
            step_id="cloud_manual",
            data_schema=vol.Schema(fields),
            errors=errors or {},
            description_placeholders={
                "found": str(len(known)),
                "total": str(len(advertised)) if advertised else str(len(known)),
                "pending": ", ".join(pending) if pending else "(none)",
            },
        )

    async def async_step_debug_write(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coord = self._coordinator()
        if coord is None:
            return self.async_abort(reason="not_loaded")

        if user_input is not None:
            self._debug_href = user_input["href"]
            return await self.async_step_debug_edit()

        hrefs = sorted(coord.last_resources.keys())
        return self.async_show_form(
            step_id="debug_write",
            data_schema=vol.Schema(
                {
                    vol.Required("href"): SelectSelector(
                        SelectSelectorConfig(
                            options=hrefs,
                            custom_value=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    def _show_debug_edit_form(
        self,
        href: str,
        current: dict,
        errors: dict[str, str],
        payload,
    ) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="debug_edit",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "payload",
                        default=(payload if payload is not None else {}),
                    ): ObjectSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "href": href,
                "current_value": (
                    json.dumps(current, indent=2, ensure_ascii=False) if current else "{}"
                ),
            },
        )

    async def async_step_debug_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coord = self._coordinator()
        if coord is None:
            return self.async_abort(reason="not_loaded")

        href = self._debug_href
        current = coord.resource(href)

        if user_input is not None:
            payload = user_input.get("payload") or {}
            if not isinstance(payload, dict) or not payload:
                return self._show_debug_edit_form(
                    href, current, {"payload": "empty_payload"}, payload
                )
            # Goes through the write_resource service (issue #300), not
            # coord.async_raw_write directly, so there is exactly one code
            # path that performs a raw write. MAIN's own device -- the
            # panel's href dropdown already lists actual hrefs off
            # coord.last_resources, and MAIN.to_actual is identity, so
            # this preserves the panel's existing behavior byte for byte.
            dev = dr.async_get(self.hass).async_get_device(
                identifiers=coord.device_info["identifiers"]
            )
            if dev is None:
                return self.async_abort(reason="not_loaded")
            try:
                response = await self.hass.services.async_call(
                    DOMAIN,
                    SERVICE_WRITE_RESOURCE,
                    {"writes": [{"href": href, "payload": payload}]},
                    target={"device_id": dev.id},
                    blocking=True,
                    return_response=True,
                )
                results = (response or {}).get("results")
                first = results[0] if isinstance(results, list) and results else None
                raw_code = first.get("raw_code") if isinstance(first, dict) else None
                after = first.get("after") if isinstance(first, dict) else None
                if not isinstance(raw_code, int) or not isinstance(after, dict):
                    raise RuntimeError("write_resource service returned an unexpected shape")
                code, new_rep = raw_code, after
            except Exception:
                _LOGGER.exception("debug raw write failed for %s", href)
                return self._show_debug_edit_form(href, current, {"base": "write_failed"}, payload)
            self._debug_result = (code, new_rep)
            return await self.async_step_debug_result()

        return self._show_debug_edit_form(href, current, {}, None)

    async def async_step_debug_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        code, new_rep = self._debug_result or (0, {})
        return self.async_show_menu(
            step_id="debug_result",
            menu_options=["debug_write", "finish"],
            description_placeholders={
                "code": f"{code >> 5}.{code & 0x1F:02d} ({code:#04x})",
                "new_value": (
                    json.dumps(new_rep, indent=2, ensure_ascii=False) if new_rep else "{}"
                ),
            },
        )

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Close the flow without altering saved options.
        return self.async_create_entry(data=dict(self.config_entry.options))
