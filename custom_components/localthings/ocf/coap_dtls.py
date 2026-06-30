"""CoAP-over-DTLS client for Samsung RT-OCF appliances (RFC 7252 + 6347).

Replaces the TLS-over-TCP transport used in the original dryer bridge.
Both the oven (UDP/49154) and the dryer (UDP/49155) speak CoAP-over-DTLS
with the ECDHE-ECDSA-AES128-GCM-SHA256 cipher and ab0b0ac4 client cert.

Wire-level details that matter (from local-tools/oven-findings.md §17):
  * DTLS ciphertext MTU must be 1200; otherwise OpenSSL fragments the
    client cert across two datagrams and TizenRT drops the second.
  * Samsung's RT-OCF uses ACK+separate-CON for the larger responses.
    The reader MUST correlate by (token, mid) — not arrival order —
    or interleaved one-shot / OBSERVE traffic mis-attributes.
  * Multi-block GET requires the SAME CoAP token across every block
    of the response ("token-stable Block2"). Fresh-token-per-block
    is silently dropped by the server.

Reader thread owns the UDP socket. Callers issue get()/post() and block
on a per-token Event the reader signals. OBSERVE notifications are
delivered via the on_notification callback.
"""
import os
import socket
import struct
import threading
import time
from pathlib import Path

from OpenSSL import SSL

_OCF_ROOT_CA = str(Path(__file__).parent / 'ocf_root_ca.pem')

import logging
logger = logging.getLogger(__name__)
# Diagnostic logging — when DEBUG_BRIDGE=1 in env, the bridge dumps
# every received CoAP frame, every /operational/state/vs/0 + /oven/vs/0
# + /power/vs/0 + /mode/vs/0-options rep change, the full link tree at
# seed time, and the /oic/res directory. Useful for reverse-engineering
# new resources and field semantics; otherwise quiet.
DEBUG_BRIDGE = os.environ.get('DEBUG_BRIDGE') == '1'


# CoAP option numbers (RFC 7252 + 7641 + 7959)
URI_PATH       = 11
URI_QUERY      = 15
OBSERVE        =  6
CONTENT_FORMAT = 12
ACCEPT         = 17
BLOCK2         = 23
SIZE2          = 28

# CoAP message types
TYPE_CON = 0
TYPE_NON = 1
TYPE_ACK = 2
TYPE_RST = 3

# CoAP method codes
METHOD_GET  = 0x01
METHOD_POST = 0x02

# CoAP content-format value for application/cbor
CF_CBOR = b'\x3c'

# OBSERVE option values (RFC 7641 §2)
OBSERVE_REGISTER   = b''           # register / refresh
OBSERVE_DEREGISTER = bytes([1])    # deregister

# Block2 SZX=6 → 1024-byte blocks. The largest size Samsung's RT-OCF
# will honour and the only one the probes have validated end-to-end.
BLOCK_SZX = 6


def _vlen(v):
    """Variable-length integer encoder used in option deltas + lengths."""
    if v < 13:    return v, b''
    if v < 269:   return 13, bytes([v - 13])
    return 14, struct.pack('>H', v - 269)


def encode_options(opts):
    """Encode a list of (option_number, value_bytes) tuples."""
    out = b''
    prev = 0
    for n, val in sorted(opts, key=lambda x: x[0]):
        d, dx = _vlen(n - prev)
        l, lx = _vlen(len(val))
        out += bytes([(d << 4) | l]) + dx + lx + val
        prev = n
    return out


def parse_coap(data):
    """Decode a CoAP datagram. Returns (mtype, code, mid, token,
    options, payload). options is a list of (num, value_bytes)."""
    mt = (data[0] >> 4) & 0x03
    tkl = data[0] & 0x0F
    code = data[1]
    mid = int.from_bytes(data[2:4], 'big')
    tok = data[4:4 + tkl]
    i = 4 + tkl
    opts = []
    prev = 0
    payload = b''
    while i < len(data):
        b = data[i]
        if b == 0xFF:
            payload = data[i + 1:]
            break
        d_nib, l_nib = b >> 4, b & 0x0F
        i += 1
        if d_nib == 13:
            delta = 13 + data[i]; i += 1
        elif d_nib == 14:
            delta = 269 + int.from_bytes(data[i:i + 2], 'big'); i += 2
        elif d_nib == 15:
            raise ValueError("reserved option delta nibble 15")
        else:
            delta = d_nib
        if l_nib == 13:
            length = 13 + data[i]; i += 1
        elif l_nib == 14:
            length = 269 + int.from_bytes(data[i:i + 2], 'big'); i += 2
        elif l_nib == 15:
            raise ValueError("reserved option length nibble 15")
        else:
            length = l_nib
        num = prev + delta
        opts.append((num, data[i:i + length]))
        i += length
        prev = num
    return mt, code, mid, tok, opts, payload


def build_coap(mtype, code, mid, token, options, payload=b''):
    """Build a CoAP datagram. mtype: CON/NON/ACK/RST. token: bytes (may
    be empty for ACK). options: list of (num, value_bytes)."""
    tkl = len(token)
    hdr = bytes([(1 << 6) | (mtype << 4) | tkl, code,
                 (mid >> 8) & 0xFF, mid & 0xFF])
    body = hdr + token + encode_options(options)
    if payload:
        body += b'\xFF' + payload
    return body


def block_value(num, more, szx):
    """Encode a CoAP Block-N option value."""
    v = (num << 4) | ((more & 1) << 3) | (szx & 7)
    if v <= 0xFF:    return bytes([v])
    if v <= 0xFFFF:  return struct.pack('>H', v)
    return struct.pack('>I', v)[1:]


def fmt_code(c):
    """0x45 → '2.05', 0x84 → '4.04'. Used in log lines."""
    return f"{c >> 5}.{c & 0x1F:02d}"


def _split_dtls(buf):
    """Split a UDP datagram that contains one-or-more DTLS records.
    OpenSSL sometimes hands the BIO multiple records back-to-back; we
    must send each as its own UDP datagram or TizenRT drops them."""
    o, out = 0, []
    while o + 13 <= len(buf):
        L = int.from_bytes(buf[o + 11:o + 13], 'big')
        end = o + 13 + L
        if end > len(buf):
            break
        out.append(buf[o:end])
        o = end
    return out


class DtlsCoapSession:
    """Single sustained DTLS-CoAP session.

    Caller drives lifecycle:
        sess = DtlsCoapSession(host, port, cert, key)
        sess.connect()
        sess.start_reader()
        sess.subscribe([...], on_notification=cb)   # OBSERVE
        code, body = sess.get(['device', '0'])      # Block2 fetch
        code, _    = sess.post(['mode','vs','0'], cbor)
        sess.close()
    """

    HANDSHAKE_TIMEOUT_S = 12.0
    READER_RECV_TIMEOUT_S = 1.0  # short so stop_event propagates quickly
    MAX_BLOCKS = 32              # safety bound for Block2 fetches

    def __init__(self, host, port, cert_path, key_path,
                 on_notification=None, mtu=1200):
        self.host = host
        self.port = port
        self.cert_path = str(cert_path)
        self.key_path  = str(key_path)
        self.on_notification = on_notification  # fn(href, payload_bytes)
        self.mtu = mtu

        self.sock = None
        self.conn = None
        self.dest = None

        self._send_lock = threading.Lock()
        # Randomize MID and token counter starting points so reconnects
        # don't reuse identifiers from previous sessions — Samsung's
        # RT-OCF appears to remember observer state across DTLS
        # sessions, and re-registering with a token it still thinks is
        # active is silently no-ops.
        self._mid = int.from_bytes(os.urandom(2), 'big')
        self._tok_counter = int.from_bytes(os.urandom(4), 'big')
        # OBSERVE tokens are 1-byte (Samsung silently drops TKL>1
        # OBSERVE registrations). Pick a random starting byte in the
        # 0x40..0xff range so each session uses fresh values.
        self._observe_tok_counter = 0x40 + (os.urandom(1)[0] & 0xBF)
        # token (bytes) → (Event, container_dict)
        self._pending = {}
        # token (bytes) → href (str)
        self._observe_tokens = {}

        self._stop = threading.Event()
        self._reader_thread = None

    # ---- lifecycle ---------------------------------------------------

    def connect(self):
        """DTLS handshake. Blocks up to HANDSHAKE_TIMEOUT_S. Raises
        ConnectionError / TimeoutError on failure."""
        ctx = SSL.Context(SSL.DTLS_METHOD)

        ctx.load_verify_locations(_OCF_ROOT_CA)
        ctx.set_verify(SSL.VERIFY_PEER, lambda conn, cert, err, depth, ok: ok)
        # @SECLEVEL=0 permits SHA-1 in Samsung's server cert chain (AC14K_M
        # intermediate is SHA-1 signed). This is the only channel that reaches
        # the OpenSSL instance cryptography bundles — ctypes and cffi bindings
        # do not expose SSL_CTX_set_security_level on this build.
        ctx.set_cipher_list(b'ECDHE-ECDSA-AES128-GCM-SHA256:@SECLEVEL=0')
        ctx.use_certificate_chain_file(self.cert_path)
        ctx.use_privatekey_file(self.key_path)
        ctx.check_privatekey()

        conn = SSL.Connection(ctx, None)
        conn.set_connect_state()
        conn.set_ciphertext_mtu(self.mtu)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
        dest = (self.host, self.port)
        logger.debug(
            "DTLS handshake starting to %s:%d (timeout=%.1fs)",
            self.host, self.port, self.HANDSHAKE_TIMEOUT_S,
        )

        t0 = time.time()
        iterations = 0
        while time.time() - t0 < self.HANDSHAKE_TIMEOUT_S:
            iterations += 1
            try:
                conn.do_handshake()
                logger.debug(
                    "DTLS handshake succeeded after %d iterations", iterations,
                )
                break
            except SSL.WantReadError:
                pass
            except SSL.Error as e:
                logger.debug("DTLS SSL.Error on iter %d: %s", iterations, e)
                sock.close()
                raise ConnectionError(f"DTLS handshake error: {e}") from e
            try:
                o = conn.bio_read(65535)
                if o:
                    for r in _split_dtls(o):
                        sock.sendto(r, dest)
            except SSL.WantReadError:
                pass
            try:
                d, _ = sock.recvfrom(65535)
                if d:
                    conn.bio_write(d)
            except socket.timeout:
                pass
            time.sleep(0.05)
        else:
            logger.debug(
                "DTLS handshake timeout after %d iterations, elapsed=%.2fs",
                iterations, time.time() - t0,
            )
            sock.close()
            raise TimeoutError(
                f"DTLS handshake timeout to {self.host}:{self.port}")

        self.sock = sock
        self.conn = conn
        self.dest = dest
        self._stop.clear()

    def start_reader(self):
        """Spawn the reader thread. Must be called after connect()."""
        if self.sock is None:
            raise RuntimeError("connect() before start_reader()")
        t = threading.Thread(target=self._reader_loop,
                             daemon=True, name='dtls-reader')
        t.start()
        self._reader_thread = t

    def join(self):
        """Block until the reader thread exits (i.e. socket dies)."""
        if self._reader_thread is not None:
            self._reader_thread.join()

    def _send_observe_dereg(self, tok, path_segs):
        """Send a single OBSERVE deregister GET (Observe option = 1)
        on the existing token. Best-effort — caller swallows errors."""
        if self.conn is None:
            return
        mid = self._next_mid()
        opts = [(URI_PATH, s.encode()) for s in path_segs]
        opts.append((OBSERVE, OBSERVE_DEREGISTER))
        opts.append((ACCEPT, CF_CBOR))
        self._send_dgram(
            build_coap(TYPE_CON, METHOD_GET, mid, tok, opts))

    def close(self):
        """Tear down session. Sends best-effort OBSERVE deregisters
        first so Samsung's RT-OCF cleans up its observer table —
        without this, the per-cert observer state survives DTLS close
        and a quick reconnect with the same tokens silently no-ops."""
        # Send dereg for every active observation while the conn is
        # still healthy. Tiny sleep lets the records reach the wire
        # before we shut DTLS down.
        if self.conn is not None and self._observe_tokens:
            for tok, href in list(self._observe_tokens.items()):
                segs = [s for s in href.split('/') if s]
                try:
                    self._send_observe_dereg(tok, segs)
                except Exception as e:
                    logger.warning("dereg %s: %s", href, e)
            time.sleep(0.1)

        self._stop.set()
        if self.conn is not None:
            try:
                self.conn.shutdown()
            except Exception:
                pass
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
        for tok, (ev, container) in list(self._pending.items()):
            container.setdefault('err', 'socket closed')
            ev.set()
        self._pending.clear()
        self._observe_tokens.clear()
        self.sock = None
        self.conn = None

    # ---- send / receive plumbing -------------------------------------

    def _next_mid(self):
        self._mid = (self._mid + 1) & 0xFFFF
        return self._mid

    def _next_tok(self):
        self._tok_counter = (self._tok_counter + 1) & 0xFFFFFFFF
        # 4-byte tokens — fits within tkl=8 cap with headroom and
        # avoids collisions across long-running OBSERVE subscriptions.
        return self._tok_counter.to_bytes(4, 'big')

    def _next_observe_tok(self):
        # Single-byte tokens for OBSERVE registrations. Samsung
        # RT-OCF accepts these but silently drops TKL=4 OBSERVE
        # registrations. Counter is randomly seeded per session so
        # reconnects don't collide with stale observer state Samsung
        # may still be holding from the previous run.
        self._observe_tok_counter = (self._observe_tok_counter + 1) & 0xFF
        # Avoid 0x00 — some CoAP stacks treat an all-zero token as
        # equivalent to "no token" / empty (TKL=0).
        if self._observe_tok_counter == 0:
            self._observe_tok_counter = 1
        return bytes([self._observe_tok_counter])

    def _send_dgram(self, datagram):
        """Send a CoAP datagram. Holds the send lock for the
        BIO-drain so two writers can't interleave records."""
        with self._send_lock:
            if self.conn is None:
                raise ConnectionError("DTLS session closed")
            try:
                self.conn.send(datagram)
                while True:
                    o = self.conn.bio_read(65535)
                    if not o:
                        break
                    for r in _split_dtls(o):
                        self.sock.sendto(r, self.dest)
            except SSL.WantReadError:
                pass

    def _reader_loop(self):
        """Pump UDP socket → DTLS BIO → CoAP parser. Demuxes to pending
        / observe handlers. Exits on socket error or stop event."""
        sock = self.sock
        conn = self.conn
        sock.settimeout(self.READER_RECV_TIMEOUT_S)
        try:
            while not self._stop.is_set():
                try:
                    d, _ = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except (OSError, ValueError):
                    return
                if not d:
                    continue
                # pyOpenSSL's SSL.Connection is not thread-safe — the
                # same SSL object must not be touched by multiple
                # threads concurrently. Drain decrypted records into a
                # local list under _send_lock so the reader never races
                # a sender's conn.send()/bio_read(). Dispatch happens
                # AFTER releasing the lock because _dispatch_coap may
                # call _send_dgram (auto-ACK for CON frames), which
                # re-acquires the lock — holding it across dispatch
                # would deadlock.
                packets = []
                exit_reader = False
                with self._send_lock:
                    try:
                        conn.bio_write(d)
                    except SSL.Error as e:
                        logger.warning("DTLS bio_write: %s", e)
                        return
                    while True:
                        try:
                            pl = conn.recv(65535)
                        except SSL.WantReadError:
                            break
                        except SSL.ZeroReturnError:
                            logger.info("DTLS peer closed connection")
                            exit_reader = True
                            break
                        except SSL.Error as e:
                            logger.warning("DTLS recv: %s", e)
                            exit_reader = True
                            break
                        if not pl:
                            break
                        packets.append(pl)
                for pl in packets:
                    try:
                        self._dispatch_coap(pl)
                    except Exception as e:
                        logger.warning("dispatch: %s", e)
                if exit_reader:
                    return
        finally:
            # Make sure pending waiters don't hang if the reader dies.
            for tok, (ev, container) in list(self._pending.items()):
                container.setdefault('err', 'reader exited')
                ev.set()

    def _dispatch_coap(self, datagram):
        try:
            mt, code, mid, tok, ropts, payload = parse_coap(datagram)
        except Exception as e:
            logger.debug("malformed CoAP: %s", e)
            return

        if DEBUG_BRIDGE:
            kind = ['CON', 'NON', 'ACK', 'RST'][mt]
            logger.info("rx %s code=%s mid=%04x tok=%s opts=%d pl=%d",
                        kind, fmt_code(code), mid, tok.hex() or '-',
                        len(ropts), len(payload))

        # ACK back any CON from the device to suppress retransmits.
        # RFC 7252 §4.2 — ACK is a bare frame (token len 0, code 0).
        if mt == TYPE_CON:
            try:
                self._send_dgram(build_coap(TYPE_ACK, 0, mid, b'', []))
            except Exception as e:
                logger.warning("ACK send: %s", e)

        # Empty ACK with no options & no payload = "separate response
        # coming" — used by Samsung's RT-OCF for the larger reads. Stop
        # the retransmit timer on the client side and wait for the CON.
        if mt == TYPE_ACK and code == 0 and not payload and not ropts:
            return

        # Pending one-shot? Resolve and return.
        rec = self._pending.get(tok)
        if rec is not None:
            ev, container = rec
            container['code']    = code
            container['mtype']   = mt
            container['mid']     = mid
            container['options'] = ropts
            container['payload'] = payload
            ev.set()
            return

        # OBSERVE notification?
        href = self._observe_tokens.get(tok)
        if href is not None:
            if code != 0x45:
                logger.warning("observe %s: non-2.05 %s",
                               href, fmt_code(code))
                return
            cb = self.on_notification
            if cb is not None:
                try:
                    cb(href, payload)
                except Exception as e:
                    logger.warning("notification callback %s: %s",
                                   href, e)
            return

        # Stale token (post-reconnect or unknown) — drop quietly.
        logger.debug(
            "stale CoAP response: code=%s tok=%s mid=%04x",
            fmt_code(code), tok.hex() or '-', mid,
        )

    # ---- request primitives ------------------------------------------

    def get(self, path_segs, query=(), timeout=10.0):
        """Token-stable Block2 GET. Returns (code, payload_bytes).

        Reuses one CoAP token across every block of a multi-block
        response — Samsung's server keys per-transfer state on the
        token, and dropping a fresh token on block 1+ silently drops
        the request."""
        if self.conn is None:
            raise ConnectionError("DTLS session closed")
        tok = self._next_tok()
        logger.debug(
            "GET /%s token=%s timeout=%.1fs",
            '/'.join(path_segs), tok.hex(), timeout,
        )
        blob = b''
        num = 0
        last_code = None
        last_opts = []
        deadline = time.time() + timeout
        while True:
            ev = threading.Event()
            container = {}
            self._pending[tok] = (ev, container)
            try:
                mid = self._next_mid()
                opts = [(URI_PATH, s.encode()) for s in path_segs]
                for q in query:
                    opts.append((URI_QUERY, q.encode()))
                opts.append((ACCEPT, CF_CBOR))
                if num > 0:
                    opts.append((BLOCK2, block_value(num, 0, BLOCK_SZX)))
                self._send_dgram(
                    build_coap(TYPE_CON, METHOD_GET, mid, tok, opts))
                wait = max(0.1, deadline - time.time())
                if not ev.wait(wait):
                    logger.debug(
                        "GET /%s block %d: timed out after %.2fs",
                        '/'.join(path_segs), num, wait,
                    )
                    raise TimeoutError(
                        f"GET /{'/'.join(path_segs)} block {num} timeout")
                code_seen = container.get('code', 0)
                # 4.01 = OCF cert/ACL rejection — surface at WARNING
                # so it's visible without debug logging.
                if code_seen == 0x81:
                    logger.warning(
                        "GET /%s → 4.01 Unauthorized",
                        '/'.join(path_segs),
                    )
                else:
                    logger.debug(
                        "GET /%s block %d: %s (%d bytes)",
                        '/'.join(path_segs), num,
                        fmt_code(code_seen),
                        len(container.get('payload', b'')),
                    )
                if 'err' in container:
                    raise ConnectionError(container['err'])
            finally:
                self._pending.pop(tok, None)

            code = container['code']
            payload = container['payload']
            ropts = container['options']
            last_code = code
            last_opts = ropts
            blob += payload
            # 4.xx / 5.xx responses don't carry Block2 continuation —
            # bail with whatever we got. Caller decides if 4.xx is fatal.
            if code >> 5 != 2:
                return code, blob
            b2 = [v for n, v in ropts if n == BLOCK2]
            more = 0
            if b2:
                bv = int.from_bytes(b2[0], 'big')
                more = (bv >> 3) & 1
            if not more:
                break
            num += 1
            if num > self.MAX_BLOCKS:
                raise ConnectionError(
                    f"GET /{'/'.join(path_segs)}: >{self.MAX_BLOCKS} "
                    f"blocks, aborting")
        return last_code, blob

    def post(self, path_segs, body_cbor, timeout=8.0):
        """Single-frame POST with a CBOR-encoded body. Returns
        (code, payload_bytes). body_cbor must already be encoded."""
        if self.conn is None:
            raise ConnectionError("DTLS session closed")
        tok = self._next_tok()
        mid = self._next_mid()
        opts = [(URI_PATH, s.encode()) for s in path_segs]
        opts.append((CONTENT_FORMAT, CF_CBOR))
        opts.append((ACCEPT, CF_CBOR))
        datagram = build_coap(TYPE_CON, METHOD_POST, mid, tok, opts,
                              body_cbor)
        ev = threading.Event()
        container = {}
        self._pending[tok] = (ev, container)
        try:
            self._send_dgram(datagram)
            if not ev.wait(timeout):
                raise TimeoutError(
                    f"POST /{'/'.join(path_segs)} timeout")
            if 'err' in container:
                raise ConnectionError(container['err'])
            return container['code'], container['payload']
        finally:
            self._pending.pop(tok, None)

    def ping(self):
        """RFC 7252 §4.4 CoAP Ping — empty CON, no token, no payload.
        Peer MUST respond with an RST sharing the same Message ID.
        We don't block waiting for the RST here; the reader thread
        sees it, finds nothing in _pending/_observe_tokens for an
        empty token, and drops it quietly — which is the documented
        behaviour for unsolicited RST.

        Sole purpose is keepalive: gives Samsung's RT-OCF stack a
        visible "client still here" signal so it doesn't expire our
        OBSERVE subscriptions (symptom of expiry: POSTs still get
        2.04 but OBSERVE notifications stop arriving)."""
        if self.conn is None:
            raise ConnectionError("DTLS session closed")
        mid = self._next_mid()
        # Empty message: ver=01, type=CON, tkl=0, code=0.00, mid, no
        # token, no options, no payload. build_coap with empty token
        # and no options yields exactly that.
        self._send_dgram(build_coap(TYPE_CON, 0, mid, b'', []))
        return mid

    def subscribe(self, path_segs):
        """Register an OBSERVE on the given path. The initial 2.05
        notification and all subsequent state-change notifications
        will fire on_notification(href, payload_bytes).

        Returns the token used (in case the caller wants to deregister
        later)."""
        if self.conn is None:
            raise ConnectionError("DTLS session closed")
        tok = self._next_observe_tok()
        href = '/' + '/'.join(path_segs)
        # Register the token BEFORE sending — otherwise the device
        # could respond between send() and the dict insert, and the
        # reader thread would drop the initial 2.05 as "stale".
        self._observe_tokens[tok] = href
        mid = self._next_mid()
        opts = [(URI_PATH, s.encode()) for s in path_segs]
        opts.append((OBSERVE, OBSERVE_REGISTER))
        opts.append((ACCEPT, CF_CBOR))
        self._send_dgram(
            build_coap(TYPE_CON, METHOD_GET, mid, tok, opts))
        return tok
