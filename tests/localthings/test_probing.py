"""Tests for pre-authentication host probing."""

from __future__ import annotations


def test_order_candidates_prefers_known_ports() -> None:
    """Live ports are ordered with the historically known DTLS ports first,
    then the rest ascending."""
    from custom_components.localthings.probing import order_candidates

    assert order_candidates([49160, 49153, 49155, 49154]) == [
        49154,
        49155,
        49153,
        49160,
    ]
    assert order_candidates([49153]) == [49153]


def test_find_live_ports_detects_silent_port(socket_enabled) -> None:
    """The UDP liveness sweep flags a bound-but-silent port as live and drops
    ports that refuse with ICMP port-unreachable.

    A bound, never-recv'd UDP socket stands in for a device that listens but
    stays silent (open|filtered), like the dishwasher in issue #13 on 49153.
    Two sibling ports are reserved then closed so loopback refuses datagrams
    to them, standing in for the closed ports the scan should discard.

    `socket_enabled` lifts pytest-socket's default block (the HA test harness
    disables real sockets); this test genuinely needs loopback UDP to exercise
    the ICMP-unreachable path.
    """
    import socket

    from custom_components.localthings.probing import find_live_ports

    reserve = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(3)]
    for s in reserve:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in reserve]
    live_sock, live_port = reserve[0], ports[0]
    reserve[1].close()
    reserve[2].close()
    closed_ports = ports[1:]

    try:
        result = find_live_ports(
            "127.0.0.1",
            [closed_ports[0], live_port, closed_ports[1]],
            0.8,
        )
    finally:
        live_sock.close()

    assert result.live == [live_port]
    # Refused, not unreachable: loopback is up and answered. That distinction
    # is what stops a wrong-but-live IP and an address with nothing on it
    # producing the same message.
    assert result.refused == sorted(closed_ports)
    assert result.unreachable == []


def test_sweep_ports_rescues_preferred_ports_the_sweep_missed(
    socket_enabled,
    monkeypatch,
) -> None:
    """Issue #192: a segregated VLAN made the ICMP-based sweep call three
    closed ports live while missing the one port (a historically confirmed
    DTLS port) that nmap showed as genuinely open|filtered. The sweep's
    verdict on a preferred port shouldn't be trusted blindly -- it must
    always come back as a candidate even if the sweep marked it dead, so the
    config flow gets a real handshake attempt against it.

    Uses an OS-assigned port monkeypatched into PREFERRED_PROBE_PORTS rather
    than the real 49154/49155, so this doesn't depend on those specific
    system ports being free on whatever machine runs the suite.
    """
    import socket

    from custom_components.localthings import probing
    from custom_components.localthings.probing import sweep_ports

    # Bind an OS-assigned port and immediately close it, same technique
    # test_find_live_ports_detects_silent_port uses for its "closed" ports --
    # once closed, loopback refuses datagrams to it, standing in for the
    # sweep wrongly ruling out a port we have strong prior evidence for.
    reserved = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reserved.bind(("127.0.0.1", 0))
    preferred_port = reserved.getsockname()[1]
    reserved.close()
    monkeypatch.setattr(probing, "PREFERRED_PROBE_PORTS", [preferred_port])

    live_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    live_sock.bind(("127.0.0.1", 0))
    live_port = live_sock.getsockname()[1]

    try:
        sweep, candidates = sweep_ports("127.0.0.1", [preferred_port, live_port], 0.8)
    finally:
        live_sock.close()

    # The sweep's own verdict stays honest -- it really didn't see the
    # preferred port -- and the rescue shows up only in the candidate list.
    assert sweep.live == [live_port]
    assert set(candidates) == {preferred_port, live_port}
