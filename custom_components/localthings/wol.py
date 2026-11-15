"""Wake-on-LAN for a TV, whose OCF stack is down while it is off.

Wi-Fi wake requires Samsung's "Power On with Mobile" setting; WOWLAN is
reported in /DevConfResURI's `sl`.
"""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Callable, Iterable

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

WOL_PORT = 9
# Wi-Fi drops broadcasts more readily than Ethernet, so more than one, spaced
# like ha-samsungtv-smart's repeats.
WOL_PACKETS = 3
WOL_INTERVAL_S = 0.25
IPV4_BROADCAST = "255.255.255.255"


def magic_packet(mac: str) -> bytes:
    """The magic packet for `mac` (any of aa:bb:.., aa-bb-.., aabb..)."""
    digits = mac.strip().lower().replace(":", "").replace("-", "").replace(".", "")
    if len(digits) != 12 or set(digits) - set("0123456789abcdef"):
        raise ValueError(f"not a MAC address: {mac!r}")
    return b"\xff" * 6 + bytes.fromhex(digits) * 16


def send_magic_packet(
    mac: str,
    targets: Iterable[str],
    *,
    packets: int = WOL_PACKETS,
    interval: float = WOL_INTERVAL_S,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Send `packets` rounds of the magic packet to every target. Blocking.

    Returns how many datagrams went out; a target that refuses (no route,
    say) is skipped, so 0 means none was sent anywhere.
    """
    payload = magic_packet(mac)
    targets = list(dict.fromkeys(targets))
    sent = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for round_ in range(packets):
            if round_:
                sleep(interval)
            for target in targets:
                try:
                    sock.sendto(payload, (target, WOL_PORT))
                    sent += 1
                except OSError as err:
                    _LOGGER.debug("Wake-on-LAN to %s failed: %s", target, err)
    return sent


async def async_targets(hass: HomeAssistant, host: str) -> list[str]:
    """Directed broadcasts for every adapter, plus the TV's unicast address.

    Limited broadcasts use only the default route; unicast can cross a
    router that retains the TV's ARP entry.
    """
    try:
        from homeassistant.components import network

        broadcasts = [str(a) for a in await network.async_get_ipv4_broadcast_addresses(hass)]
    except Exception as err:  # network unavailable: the limited broadcast still works
        _LOGGER.debug("broadcast addresses unavailable (%s); using %s", err, IPV4_BROADCAST)
        broadcasts = [IPV4_BROADCAST]
    return list(dict.fromkeys([*sorted(broadcasts), host]))
