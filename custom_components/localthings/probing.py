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
    LIVENESS_PROBE_TIMEOUT_S,
    PREFERRED_PROBE_PORTS,
    PROBE_MAX_WORKERS,
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
    """Order live ports so the historically known DTLS ports are tried first."""
    preferred = [p for p in PREFERRED_PROBE_PORTS if p in ports]
    rest = sorted(p for p in ports if p not in PREFERRED_PROBE_PORTS)
    return preferred + rest


@dataclass(frozen=True)
class HostProbe:
    """What one bounded look at `host` established.

    `candidates` is what gets a full DTLS handshake. The other two are the
    evidence behind a failure message: `confirmed` names ports a DTLS
    server was proven on, `swept` is the UDP sweep's own verdict (None
    when the sweep never had to run).
    """

    host: str
    candidates: list[int]
    confirmed: list[int]
    swept: SweepResult | None = None


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
    return order_candidates(live)


def look(host: str) -> HostProbe:
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
        return HostProbe(host=host, candidates=confirmed, confirmed=confirmed)

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
    return HostProbe(host=host, candidates=candidates, confirmed=[], swept=sweep)
