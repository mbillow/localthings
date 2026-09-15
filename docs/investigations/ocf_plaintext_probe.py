#!/usr/bin/env python3
"""Does a Samsung appliance answer plaintext CoAP on UDP 5683?

v7. Each version fixed something the previous one's own output exposed:

  * /oic/res came back as exactly 1024 bytes -- one Block2 block, not a whole
    representation -- and the partial CBOR then failed to decode, which v1
    reported as "install cbor2" whether or not cbor2 was installed. This one
    follows Block2 to the end and says plainly when a decode fails.
  * Multicast went out whichever interface the kernel picked, with no way to
    tell which that was. It now prints the source address and takes
    --interface to send from a specific one, so "no responders" can be told
    apart from "asked on the wrong network". Note that link-local multicast
    (TTL 1) cannot cross a router: if the appliance is not on the same
    segment as this host, silence there is the network, not the device.
  * A CON request was sent once and a single lost datagram read as silence.
    CoAP expects retransmission; this one retries -- repeating the identical
    datagram, message id and token included, as RFC 7252 specifies -- and
    falls back to asking for Block2 block 0 explicitly before giving up.
  * Block2 continuations drew nothing, leaving every transfer stuck after
    block 0. Two candidates, and rather than guess a third time this version
    tries them in order and reports which one the device accepts: a stable
    token across the whole transfer (smartthings-local's accumulator is
    "token-stable" and IoTivity-lite looks its transfer state up by token),
    and the endpoint the continuation is addressed to (5683, or the
    ephemeral port the first block came from). --debug prints every
    exchange.

  * /oic/res was read and then only measured -- size and device ids. Whether
    it actually advertises usable secure endpoints, which is the whole point
    of reading it, went unexamined. It now reports them.
  * That report looked only for OCF 1.0's `eps`, so "no eps advertised" could
    not be told apart from a device using the older `p`.`sec`/`port` form
    smartthings-local also handles. It now shows the shape of the links
    themselves, and asks /oic/res?rt=oic.r.doxm as well -- the same second
    lookup protocol/ocf_discovery falls back to.

Only /oic/res is blockwise, and only the "learn the secure port without a
sweep" idea needs it. Identity comes from /oic/d in a single datagram, which
has answered every time -- so a stalled /oic/res is a curiosity here, not a
blocker.

No dependencies; cbor2 is used if importable and its absence is reported as
such. Run from a machine on the same LAN as the appliance:

    python3 ocf_plaintext_probe.py 10.0.0.129
    python3 ocf_plaintext_probe.py --multicast --interface 10.0.0.5

Nothing here writes to the device, opens a DTLS session, or touches an OCF
security resource. It sends CoAP GETs and prints what comes back.
"""

from __future__ import annotations

import argparse
import contextlib
import secrets
import socket
import struct
import sys
import time

OCF_MULTICAST_GROUP = "224.0.1.187"
OCF_PORT = 5683

_GET = 1
_TYPE_CON = 0
_TYPE_NON = 1
_URI_PATH = 11
_URI_QUERY = 15
_ACCEPT = 17
_BLOCK2 = 23
_CONTENT_FORMAT_CBOR = 60
_CONTENT_FORMAT_OCF_CBOR = 10000
_OCF_VERSION_OPTION = 2049
_OCF_VERSION_1_0 = 2048
_PAYLOAD_MARKER = 0xFF
# SZX 6 is 1024-byte blocks, the largest Samsung's RT-OCF stack offers.
_BLOCK_SZX = 6
_MAX_BLOCKS = 32

try:
    import cbor2
except ImportError:
    cbor2 = None


def _option(delta: int, value: bytes) -> bytes:
    """One CoAP option, including the extended delta/length forms an option
    number as high as OCF's content-format version (2049) needs."""

    def _split(number: int) -> tuple[int, bytes]:
        if number < 13:
            return number, b""
        if number < 269:
            return 13, bytes([number - 13])
        return 14, struct.pack("!H", number - 269)

    delta_nibble, delta_ext = _split(delta)
    length_nibble, length_ext = _split(len(value))
    return bytes([(delta_nibble << 4) | length_nibble]) + delta_ext + length_ext + value


def _block2_value(num: int) -> bytes:
    packed = (num << 4) | _BLOCK_SZX
    return packed.to_bytes(1 if packed < 0x100 else 2, "big")


def build_get(
    path: tuple[str, ...],
    *,
    token: bytes,
    message_id: int,
    confirmable: bool,
    block_num: int | None = None,
    ocf: bool = False,
    query: bytes | None = None,
) -> bytes:
    """`ocf` asks in OCF's own dialect -- content format 10000 plus the
    content-format-version option -- which is what smartthings-local's
    multicast probe falls back to when plain CBOR draws nothing."""
    msg_type = _TYPE_CON if confirmable else _TYPE_NON
    header = struct.pack("!BBH", (1 << 6) | (msg_type << 4) | len(token), _GET, message_id)
    options = b""
    last = 0
    for segment in path:
        options += _option(_URI_PATH - last, segment.encode())
        last = _URI_PATH
    if query is not None:
        options += _option(_URI_QUERY - last, query)
        last = _URI_QUERY
    accept = struct.pack("!H", _CONTENT_FORMAT_OCF_CBOR) if ocf else bytes([_CONTENT_FORMAT_CBOR])
    options += _option(_ACCEPT - last, accept)
    last = _ACCEPT
    if block_num is not None:
        options += _option(_BLOCK2 - last, _block2_value(block_num))
        last = _BLOCK2
    if ocf:
        options += _option(_OCF_VERSION_OPTION - last, struct.pack("!H", _OCF_VERSION_1_0))
    return header + token + options


def parse_response(datagram: bytes) -> tuple[str, bytes, dict[int, bytes], bytes]:
    """(code, token, {option number: value}, payload)."""
    if len(datagram) < 4:
        raise ValueError("short datagram")
    first, code, _mid = struct.unpack("!BBH", datagram[:4])
    offset = 4 + (first & 0x0F)
    token = datagram[4:offset]
    options: dict[int, bytes] = {}
    number = 0
    while offset < len(datagram) and datagram[offset] != _PAYLOAD_MARKER:
        byte = datagram[offset]
        offset += 1
        delta, length = byte >> 4, byte & 0x0F
        if delta == 13:
            delta = datagram[offset] + 13
            offset += 1
        elif delta == 14:
            delta = struct.unpack("!H", datagram[offset : offset + 2])[0] + 269
            offset += 2
        if length == 13:
            length = datagram[offset] + 13
            offset += 1
        elif length == 14:
            length = struct.unpack("!H", datagram[offset : offset + 2])[0] + 269
            offset += 2
        number += delta
        options[number] = datagram[offset : offset + length]
        offset += length
    payload = datagram[offset + 1 :] if offset < len(datagram) else b""
    return f"{code >> 5}.{code & 0x1F:02d}", token, options, payload


def _block2(options: dict[int, bytes]) -> tuple[int, bool] | None:
    """(block number, more-to-come) from a response's Block2 option."""
    raw = options.get(_BLOCK2)
    if raw is None:
        return None
    packed = int.from_bytes(raw, "big")
    return packed >> 4, bool(packed & 0x08)


def _find_device_ids(decoded) -> set[str]:
    """Every `di`/`anchor` UUID anywhere in the decoded tree."""
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("di", "anchor") and isinstance(value, str):
                    found.add(value.removeprefix("ocf://"))
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(decoded)
    return found


# What the config flow sweeps today (const.PROBE_PORT_RANGE), for saying
# whether a device advertises anything that sweep would never reach.
_SWEPT_PORTS = range(49152, 49161)


def _endpoints(decoded) -> list[tuple[str, str]]:
    """Every (href, ep) pair the representation advertises.

    OCF puts the real transport endpoints in each link's `eps`; a secure one
    is `coaps://host:port`. This is the half of /oic/res that would let setup
    skip its nine-port sweep, so it is worth printing rather than counting.
    """
    found: list[tuple[str, str]] = []

    def walk(node, href):
        if isinstance(node, dict):
            href = node.get("href", href) if isinstance(node.get("href", href), str) else href
            found.extend(
                (href, entry["ep"])
                for entry in node.get("eps") or ()
                if isinstance(entry, dict) and isinstance(entry.get("ep"), str)
            )
            for key, value in node.items():
                if key != "eps":
                    walk(value, href)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item, href)

    walk(decoded, "?")
    return found


def report_link_shape(payload: bytes) -> None:
    """What the links actually look like, when no `eps` was found.

    "No endpoints" and "endpoints in a form this script doesn't read" are
    different answers, and only one of them is about the device.
    """
    if cbor2 is None:
        return
    try:
        decoded = cbor2.loads(payload)
    except Exception:
        return
    links = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("href"), str):
                links.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(decoded)
    if not links:
        print(f"    no links found; representation is a {type(decoded).__name__}")
        return
    keys = sorted({key for link in links for key in link})
    print(f"    {len(links)} links, keys seen: {', '.join(map(str, keys))}")
    print(f"    first link: {links[0]!r}")
    legacy = [
        (link.get("href"), link["p"])
        for link in links
        if isinstance(link.get("p"), dict) and {"port", "sec"} & set(link["p"])
    ]
    if legacy:
        for href, policy in legacy[:4]:
            print(f"    legacy policy on {href}: {policy!r}")
    else:
        print("    no legacy p.sec/p.port either")


def report_endpoints(payload: bytes) -> None:
    if cbor2 is None:
        return
    try:
        decoded = cbor2.loads(payload)
    except Exception:
        return
    endpoints = _endpoints(decoded)
    if not endpoints:
        print("    no eps advertised; what the links do carry:")
        report_link_shape(payload)
        return

    per_endpoint: dict[str, list[str]] = {}
    for href, endpoint in endpoints:
        per_endpoint.setdefault(endpoint, []).append(href)
    for endpoint, hrefs in sorted(per_endpoint.items()):
        print(f"    {endpoint:34} {len(hrefs)} link(s)")

    secure_ports = set()
    for endpoint in per_endpoint:
        scheme, _, rest = endpoint.partition("://")
        port = rest.rpartition(":")[2]
        if scheme == "coaps" and port.isdigit():
            secure_ports.add(int(port))
    if not secure_ports:
        print("    none of them coaps:// -- no secure port advertised here")
        return
    for port in sorted(secure_ports):
        where = "inside" if port in _SWEPT_PORTS else "OUTSIDE"
        print(f"    secure port {port}: {where} the 49152-49160 range setup sweeps")


def _describe(payload: bytes, blocks: int) -> str:
    size = f"{len(payload)} bytes"
    if blocks > 1:
        size += f" over {blocks} blocks"
    if not payload:
        return size
    if cbor2 is None:
        return f"{size} (no cbor2 here, so not decoded)"
    try:
        decoded = cbor2.loads(payload)
    except Exception as exc:
        return f"{size}, CBOR did not decode: {exc}"
    ids = _find_device_ids(decoded)
    if ids:
        return f"{size}, device ids: {', '.join(sorted(ids))}"
    if isinstance(decoded, dict):
        return f"{size}, keys: {', '.join(sorted(map(str, decoded))[:8])}"
    return f"{size}, decoded {type(decoded).__name__} of {len(decoded)}"


def _exchange(
    sock: socket.socket,
    peer: tuple[str, int],
    path: tuple[str, ...],
    *,
    token: bytes,
    block_num: int | None,
    ocf: bool,
    query: bytes | None,
    timeout: float,
    attempts: int,
    debug: bool,
):
    """One request/response, retransmitted like a CON message should be.

    A single lost datagram is otherwise indistinguishable from a device that
    does not serve the resource -- which is exactly the wrong conclusion to
    draw here. A retransmission repeats the identical datagram, message id
    and token included (RFC 7252 4.2), so the device can recognize it as one
    rather than as a second request.
    """
    host = peer[0]
    request = build_get(
        path,
        token=token,
        message_id=secrets.randbelow(65536),
        confirmable=True,
        block_num=block_num,
        ocf=ocf,
        query=query,
    )
    wait = timeout
    for attempt in range(1, attempts + 1):
        if debug:
            print(
                f"    -> {peer[0]}:{peer[1]} token={token.hex()} "
                f"block={block_num} ocf={ocf} attempt={attempt}"
            )
        sock.sendto(request, peer)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            sock.settimeout(max(deadline - time.monotonic(), 0.01))
            try:
                datagram, source = sock.recvfrom(8192)
            except TimeoutError:
                break
            except OSError as exc:
                return None, f"no answer ({exc.strerror or exc})", attempt
            # Samsung's stack answers from an ephemeral port, not 5683, so
            # the source address is what to match on -- the token is what
            # actually correlates the response.
            if source[0] != host:
                continue
            code, echoed, options, payload = parse_response(datagram)
            if echoed != token:
                if debug:
                    print(f"    <- {source[1]} token mismatch {echoed.hex()}, ignored")
                continue
            if debug:
                print(
                    f"    <- {code} from {source[0]}:{source[1]} "
                    f"{len(payload)} bytes block2={_block2(options)}"
                )
            return (code, options, payload, source), None, attempt
        wait *= 2
    if debug:
        print("    <- nothing")
    return None, None, attempts


# How to ask for the blocks after the first. The device accepted block 0 from
# a plain GET in every run; what it does with a continuation is the open
# question, so these are tried in order and the one that works is reported.
_CONTINUATIONS = (
    ("stable token, port that answered", True, True, False),
    ("stable token, 5683", True, False, False),
    ("stable token, OCF content format", True, True, True),
    ("fresh token, port that answered", False, True, False),
)


def _has_eps(payload: bytes) -> bool:
    if cbor2 is None:
        return True  # can't tell; don't chase a fallback on a guess
    try:
        return bool(_endpoints(cbor2.loads(payload)))
    except Exception:
        return True


def unicast(
    host: str,
    path: tuple[str, ...],
    timeout: float,
    dump: bool,
    attempts: int,
    debug: bool,
    query: bytes | None = None,
) -> bytes | None:
    """GET one resource, following Block2 to the end. Returns the payload."""
    label = "/" + "/".join(path) + (f"?{query.decode()}" if query else "")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    well_known = (host, OCF_PORT)
    # One token for the whole transfer: smartthings-local's accumulator is
    # "token-stable" and IoTivity-lite looks a transfer's state up by token,
    # so a fresh one per block asks about a transfer the device never began.
    token = secrets.token_bytes(4)
    collected = bytearray()
    blocks = 0
    tries = 0
    strategy = None
    try:
        block_num: int | None = None
        peer = well_known
        while blocks < _MAX_BLOCKS:
            if blocks == 0:
                candidates = [("first block", True, False, False)]
            elif strategy is not None:
                candidates = [strategy]
            else:
                candidates = list(_CONTINUATIONS)

            answer = error = None
            for candidate in candidates:
                name, stable, pinned, ocf = candidate
                if debug and blocks and strategy is None:
                    print(f"  {label}: trying continuation via {name}")
                answer, error, used = _exchange(
                    sock,
                    peer if pinned else well_known,
                    path,
                    token=token if stable else secrets.token_bytes(4),
                    block_num=block_num,
                    ocf=ocf,
                    query=query,
                    timeout=timeout,
                    attempts=attempts,
                    debug=debug,
                )
                tries += used
                if error or answer:
                    if answer and blocks:
                        strategy = candidate
                    break

            if error:
                print(f"  {label:22} {error}")
                return None
            if answer is None:
                if blocks == 0 and block_num is None:
                    # Nothing at all: ask for block 0 by name before concluding.
                    block_num = 0
                    continue
                break

            code, options, payload, source = answer
            peer = source
            if not code.startswith("2."):
                print(f"  {label:22} {code} from {source[0]}:{source[1]}")
                return None
            collected += payload
            blocks += 1
            block = _block2(options)
            if block is None or not block[1]:
                note = f", {tries} requests" if tries > blocks else ""
                if strategy:
                    note += f", continued via {strategy[0]}"
                print(
                    f"  {label:22} {code} from {source[0]}:{source[1]}, "
                    f"{_describe(bytes(collected), blocks)}{note}"
                )
                if path == ("oic", "res"):
                    report_endpoints(bytes(collected))
                if dump and cbor2 is not None:
                    with contextlib.suppress(Exception):
                        print(f"    {cbor2.loads(bytes(collected))!r}")
                return bytes(collected)
            block_num = block[0] + 1
    finally:
        sock.close()
    if collected:
        print(
            f"  {label:22} stalled after block {blocks - 1}: "
            f"{_describe(bytes(collected), blocks)}; no continuation accepted"
        )
        return bytes(collected)
    print(f"  {label:22} silence after {tries} requests")
    return None


def _primary_address(target: str) -> str:
    """The local address the kernel would use to reach `target`."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((target, OCF_PORT))
        return probe.getsockname()[0]
    finally:
        probe.close()


def multicast(timeout: float, interface: str) -> None:
    token = secrets.token_bytes(4)
    request = build_get(
        ("oic", "res"), token=token, message_id=secrets.randbelow(65536), confirmable=False
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
    sock.settimeout(0.5)
    sock.bind((interface, 0))
    print(f"  sending from {interface}:{sock.getsockname()[1]}")
    responders: dict[str, str] = {}
    try:
        sock.sendto(request, (OCF_MULTICAST_GROUP, OCF_PORT))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                datagram, source = sock.recvfrom(8192)
            except TimeoutError:
                continue
            try:
                code, echoed, _options, payload = parse_response(datagram)
            except ValueError:
                continue
            if echoed != token:
                continue
            responders[source[0]] = f"{code}, {_describe(payload, 1)}"
    finally:
        sock.close()

    if not responders:
        print("  no responders on this interface")
        return
    for address, detail in sorted(responders.items()):
        print(f"  {address:15} {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", nargs="?", help="a known appliance's IP address")
    parser.add_argument("--multicast", action="store_true", help="ask the OCF multicast group")
    parser.add_argument("--interface", help="local IPv4 address to send multicast from")
    parser.add_argument("--dump", action="store_true", help="print each decoded representation")
    parser.add_argument("--debug", action="store_true", help="trace every request and response")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument(
        "--attempts", type=int, default=3, help="retransmissions per request (CoAP CON)"
    )
    args = parser.parse_args()

    if not args.host and not args.multicast:
        parser.error("give an appliance IP, --multicast, or both")
    if cbor2 is None:
        print("note: cbor2 not importable here; payloads are reported by size only\n")

    if args.host:
        print(f"unicast CoAP to {args.host}:{OCF_PORT}")
        for path in (("oic", "res"), ("oic", "d"), ("oic", "p")):
            payload = unicast(args.host, path, args.timeout, args.dump, args.attempts, args.debug)
            if path == ("oic", "res") and payload is not None and not _has_eps(payload):
                # The fallback protocol/ocf_discovery uses when the plain
                # directory carries no secure endpoint.
                unicast(
                    args.host,
                    ("oic", "res"),
                    args.timeout,
                    args.dump,
                    args.attempts,
                    args.debug,
                    query=b"rt=oic.r.doxm",
                )

    if args.multicast:
        interface = args.interface or _primary_address(args.host or "8.8.8.8")
        print(f"\nmulticast CoAP to {OCF_MULTICAST_GROUP}:{OCF_PORT} (TTL 1)")
        multicast(args.timeout, interface)

    return 0


if __name__ == "__main__":
    sys.exit(main())
