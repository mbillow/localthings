"""What is at this address, before we authenticate to it.

Everything here runs against a host we have no credentials for: a UDP
liveness sweep, a stateless DTLS ClientHello probe, and (later) an
unauthenticated CoAP read. It returns evidence and never decides what to
tell the user -- `config_flow` owns the exception hierarchy and the
translation keys, so this module stays usable from `coordinator` too.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import selectors
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .const import (
    CLIENTHELLO_PROBE_RETRIES,
    CLIENTHELLO_PROBE_TIMEOUT_S,
    LEGACY_HTTP_PORT,
    LEGACY_HTTP_PROBE_TIMEOUT_S,
    LIVENESS_PROBE_TIMEOUT_S,
    MULTICAST_SECURE_PORT,
    PLAINTEXT_DISCOVERY_PORTS,
    PLAINTEXT_DISCOVERY_RETRIES,
    PLAINTEXT_DISCOVERY_TIMEOUT_S,
    PLAINTEXT_READ_TIMEOUT_S,
    PREFERRED_PROBE_PORTS,
    PROBE_PORT_RANGE,
)

_LOGGER = logging.getLogger(__name__)

# The kernel's way of saying the datagram never had anywhere to go: no route,
# or the host never answered ARP. Distinct from ECONNREFUSED, which is a
# response -- the host is there and told us the port is closed. Both leave a
# port "not live", but mean opposite things about whether anything exists at
# that address.
_UNREACHABLE_ERRNOS = frozenset({errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ENETDOWN})


@dataclass(frozen=True)
class SweepResult:
    """What the UDP sweep observed, kept as three separate verdicts."""

    live: list[int]  # silent -> open|filtered, worth a handshake
    refused: list[int]  # ICMP port-unreachable -> host is up, port closed
    unreachable: list[int]  # no route / no ARP -> nothing is at that address


def find_live_ports(host: str, ports: list[int], timeout: float) -> SweepResult:
    """Fast UDP liveness sweep -- the sweep's own verdict, nothing added.

    UDP is connectionless, but a connected UDP socket surfaces the ICMP
    port-unreachable a closed port returns as ECONNREFUSED on its next recv.
    So we send one probe datagram per port and watch for that error:
    ECONNREFUSED means closed; silence/data means possibly live. The
    in-process equivalent of ``nmap -sU``: takes a nine-port range down to
    the one or two worth a full DTLS handshake, bounded to ``timeout``.

    Deliberately the raw verdict, with no preferred-port rescue folded in
    (that's `sweep_ports`) -- its shape is evidence about the host, and a
    refusal vs. an unreachable are counted apart rather than both "not
    live" for that reason (see SweepResult).
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

    return SweepResult(order_candidates(live), sorted(refused), sorted(unreachable))


def sweep_ports(host: str, ports: list[int], timeout: float) -> tuple[SweepResult, list[int]]:
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
    sweep = find_live_ports(host, ports, timeout)
    rescued = [p for p in PREFERRED_PROBE_PORTS if p in ports and p not in sweep.live]
    return sweep, order_candidates(sweep.live + rescued)


def order_candidates(ports: list[int]) -> list[int]:
    """Order live ports: historically known DTLS ports first, 5684 last.

    5684 answers on every board in this family without being the socket
    that serves a session (issue #482), so it sorts behind everything --
    including an advertised port like 46060, which would otherwise lose to
    it on a numeric sort.
    """
    preferred = [port for port in PREFERRED_PROBE_PORTS if port in ports]
    trailing = [port for port in ports if port == MULTICAST_SECURE_PORT]
    rest = sorted(
        port
        for port in ports
        if port not in PREFERRED_PROBE_PORTS and port != MULTICAST_SECURE_PORT
    )
    return preferred + rest + trailing


@dataclass(frozen=True)
class HostProbe:
    """What one bounded look at `host` established.

    `candidates` is what gets a full DTLS handshake. The rest is evidence
    behind a failure message: `confirmed` names ports a DTLS server was
    proven on, `swept` is the UDP sweep's own verdict (None when the sweep
    never had to run), and `advertised`/`plaintext` are what the device
    said about itself before we authenticated.
    """

    host: str
    candidates: list[int]
    confirmed: list[int]
    swept: SweepResult | None = None
    advertised: tuple[int, ...] = ()
    plaintext: PlaintextIdentity | None = None
    plaintext_port: int | None = None
    legacy_http: bool = False


def _legacy_http_open(host: str) -> bool:
    """True if this host serves the legacy HTTPS bridge (issue #168).

    A board with the bridge has no CoAP server at all -- proven on a
    TP6X_WW6500 in PR #467 -- so one TCP connect tells the two lineages
    apart and the user only ever types an address.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(LEGACY_HTTP_PROBE_TIMEOUT_S)
        try:
            return probe.connect_ex((host, LEGACY_HTTP_PORT)) == 0
        except OSError:
            return False


def _clienthello_scan(host: str, ports: list[int], preferred: int | None = None) -> list[int]:
    """Ports on `host` a DTLS server is proven to be listening on (issues #211, #486).

    Selection is on the port a reply came *from*, not the port dialled: an
    OCF stack binds its DTLS socket to port 0 and answers a first flight
    from that ephemeral port whatever port was addressed, so "a reply
    arrived after dialling N" says nothing about N. `probe_dtls_ports` does
    that selection; `preferred` is the device's own advertisement, which is
    what breaks a tie when several ports answer.

    Imported lazily so an install on a wheel older than 0.1.18 degrades to
    the UDP sweep at scan time rather than failing to load the config flow.
    """
    from smartthings_local.protocol.dtls_probe import probe_dtls_ports

    # probe_dtls_ports refuses more than 32 ports; the caller's list is far
    # under that, but the cap is the library's to set, not ours to assume.
    result = probe_dtls_ports(
        host,
        ports[:32],
        preferred_port=preferred,
        timeout=CLIENTHELLO_PROBE_TIMEOUT_S,
        retries=CLIENTHELLO_PROBE_RETRIES,
    )
    _LOGGER.debug(
        "DTLS probe of %s: outcome=%s selected=%s responders=%s dialled_live=%s",
        host,
        result.outcome,
        result.selected_port,
        result.responder_ports,
        result.live_ports,
    )
    if result.selected_port is not None:
        return [result.selected_port]
    # Ambiguous: several distinct responders, all proven servers. Handing
    # back every one keeps the try-each-in-turn behaviour the candidate
    # list has always had.
    return order_candidates(list(result.responder_ports))


def _probe_ports(advertised: tuple[int, ...]) -> list[int]:
    """Advertised ports first, then the conventional range, then 5684.

    Not order_candidates: that sorts by the historical priors, which would
    bury an advertised 46060 behind 49154 (issue #435). The device's own
    answer about itself outranks a prior. 5684 goes last for the opposite
    reason -- it answers everywhere and proves least (issue #482), so a
    board that advertises exactly 5684 (a portless endpoint defaults to it
    in the library, see _secure_endpoint_for_source) must not get to skip
    the queue on its own advertisement.
    """
    ports = [port for port in advertised if port != MULTICAST_SECURE_PORT]
    ports += [port for port in PROBE_PORT_RANGE if port not in ports]
    if MULTICAST_SECURE_PORT not in ports:
        ports.append(MULTICAST_SECURE_PORT)
    return ports


def _preferred_port(advertised: tuple[int, ...]) -> int | None:
    """The advertised port to break a tie with, never 5684 (issue #482).

    A portless advertisement defaults to 5684 in the library, so trusting
    `advertised[0]` unconditionally would hand the tie-break -- and thus
    `probe_dtls_ports`'s unconditional selection -- to the one port the
    design deliberately proves least.
    """
    return next((port for port in advertised if port != MULTICAST_SECURE_PORT), None)


def look(host: str) -> HostProbe:
    """Find the device's DTLS port, preferring proof over absence of evidence.

    Tiered, cheapest and most authoritative first: the device's own
    plaintext advertisement, then a stateless ClientHello that proves it,
    then the ICMP-based UDP sweep for a device neither reached. The sweep
    keeps its preferred-port rescue -- issue #192's segregated-VLAN device
    is why it exists.
    """
    with ThreadPoolExecutor(max_workers=2) as ex:
        advertised_future = ex.submit(_discover_advertised_ports, host)
        legacy_future = ex.submit(_legacy_http_open, host)
        try:
            advertised, plaintext_port = advertised_future.result()
        except Exception as exc:  # an older wheel lacks ocf_discovery entirely
            _LOGGER.debug(
                "Plaintext CoAP discovery unavailable on %s (%s); degrading to tier 2/3",
                host,
                exc,
            )
            advertised, plaintext_port = (), None
        legacy_http = legacy_future.result()

    plaintext = None
    if plaintext_port is not None:
        try:
            plaintext = _read_plaintext_identity(host, plaintext_port)
        except Exception as exc:  # same lazy import as above, guarded independently
            _LOGGER.debug(
                "Plaintext identity read unavailable on %s (%s); degrading to tier 2/3",
                host,
                exc,
            )
            plaintext = None

    if legacy_http and plaintext_port is None and not advertised:
        # Nothing to scan for: this lineage serves no CoAP at all.
        return HostProbe(host=host, candidates=[], confirmed=[], legacy_http=True)
    if legacy_http and (plaintext_port is not None or advertised):
        # Both answered: CoAP wins and the flow proceeds as an ordinary
        # device, but the 8888 answer is unusual enough to want in the log.
        _LOGGER.debug("%s answered both legacy 8888 and plaintext CoAP; CoAP wins", host)

    try:
        confirmed = _clienthello_scan(host, _probe_ports(advertised), _preferred_port(advertised))
    except Exception as exc:  # an older wheel degrades to the sweep
        _LOGGER.debug("ClientHello probe unavailable (%s); falling back to UDP sweep", exc)
        confirmed = []
    if confirmed:
        _LOGGER.debug("DTLS port(s) confirmed on %s: %s", host, confirmed)
        return HostProbe(
            host=host,
            candidates=confirmed,
            confirmed=confirmed,
            advertised=advertised,
            plaintext=plaintext,
            plaintext_port=plaintext_port,
            legacy_http=legacy_http,
        )

    sweep, candidates = sweep_ports(host, PROBE_PORT_RANGE, LIVENESS_PROBE_TIMEOUT_S)
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
    return HostProbe(
        host=host,
        candidates=candidates,
        confirmed=[],
        swept=sweep,
        advertised=advertised,
        plaintext=plaintext,
        plaintext_port=plaintext_port,
        legacy_http=legacy_http,
    )


@dataclass(frozen=True)
class PlaintextIdentity:
    """What the device said about itself before we authenticated to it.

    Unauthenticated, so it is good for naming a port and quoting a board
    string in a failure message -- never for keying an entry, which needs
    the identity a handshake proved.
    """

    device_id: str | None
    model: str
    vendor_id: str
    firmware: str
    name: str


def _discover_advertised_ports(host: str) -> tuple[tuple[int, ...], int | None]:
    """Secure ports the device advertises, and the plaintext port that answered.

    Candidates go out one at a time, stopping at the first port that
    advertises something: two concurrent blockwise reads against one device
    corrupt each other -- IoTivity classic appears to key transfer state per
    peer address, not per port -- which measured as 10/12 malformed
    responses in parallel vs 12/12 clean sequentially on a real dishwasher.
    Multicast would find the same socket and is deliberately not used -- it
    is TTL 1, so it finds nothing on the routed or VLAN'd install this
    integration keeps meeting (issues #192, #321), and since 5683 is bound
    to INADDR_ANY there is nothing it would add (issue #482).
    """
    from smartthings_local.protocol.ocf_discovery import discover_ocf_secure_ports

    ports: list[int] = []
    answered: int | None = None
    for port in PLAINTEXT_DISCOVERY_PORTS:
        result = discover_ocf_secure_ports(
            host,
            discovery_port=port,
            timeout=PLAINTEXT_DISCOVERY_TIMEOUT_S,
            retries=PLAINTEXT_DISCOVERY_RETRIES,
        )
        if result.response_received and answered is None:
            answered = port
        for advertised in result.ports:
            if advertised not in ports:
                ports.append(advertised)
        if ports:
            break
    _LOGGER.debug("Plaintext CoAP on %s: answered on %s, advertised %s", host, answered, ports)
    return tuple(ports), answered


def _read_plaintext_identity(host: str, port: int) -> PlaintextIdentity | None:
    """/oic/d and /oic/p, unauthenticated, on the port that just answered."""
    import cbor2
    from smartthings_local.protocol.ocf_discovery import read_plaintext_ocf_resource

    def _read(href: str) -> dict:
        result = read_plaintext_ocf_resource(
            host,
            href,
            port=port,
            timeout=PLAINTEXT_READ_TIMEOUT_S,
            retries=PLAINTEXT_DISCOVERY_RETRIES,
        )
        if not result.successful or not result.payload:
            return {}
        try:
            body = cbor2.loads(result.payload)
        except Exception:  # a malformed rep is just no answer, not an error
            return {}
        return body if isinstance(body, dict) else {}

    with ThreadPoolExecutor(max_workers=2) as ex:
        device, platform = list(ex.map(_read, ["/oic/d", "/oic/p"]))

    if not device and not platform:
        return None
    return PlaintextIdentity(
        device_id=device.get("di"),
        model=platform.get("mnmo", ""),
        vendor_id=platform.get("vid", ""),
        firmware=platform.get("mnfv", ""),
        name=device.get("n", ""),
    )
