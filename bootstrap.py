#!/usr/bin/env python3
"""Interactive setup for samsung-appliance-local.

Run this once before `main.py`. It will:

  1. Ask for your dryer's IP and OCF port; verify the port is reachable.
  2. Locate Samsung's AC14K_M intermediate CA cert + key on disk
     (you have to fetch these yourself — see the README link).
  3. Try to discover your SmartThings hub UUID anonymously from the
     dryer's /oic/sec/acl. If that fails, ask you for it.
  4. Generate a leaf cert (SHA-1 RSA, Samsung iot-Identity + role OIDs,
     Subject CN=urn:uuid:<HUB>) signed by AC14K_M, and write
     certs/mega.key + certs/mega_chain.pem.
  5. Offer to populate .env from .env.example with the IP/port.

This script is setup-only — `cryptography` is not a runtime dep. Install
into a venv:

    python -m venv .venv
    .venv/bin/pip install -r requirements-bootstrap.txt
    .venv/bin/python bootstrap.py
"""
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import cbor2
except ImportError:
    sys.exit("cbor2 not installed — pip install -r requirements-bootstrap.txt")

from samsung_dryer.coap import (
    URI_PATH, CSM, enc_opts, enc_tcp, read_tcp, fmt_code,
)


REPO_ROOT = Path(__file__).resolve().parent
CERTS_DIR = REPO_ROOT / 'certs'

# Samsung-specific OIDs the dryer firmware looks for in the leaf.
SAMSUNG_IOT_IDENTITY_OID = '1.3.6.1.4.1.51414.0.1.2'
SAMSUNG_ROLE_OID         = '1.3.6.1.4.1.51414.1.3'

# AC14K_M cert link — used in user-facing error messages so the recipe
# is self-contained.
AC14K_M_SOURCE = (
    'https://github.com/cicciovo/homebridge-samsung-airconditioner '
    '(see ac14k_m.pem and the matching key)'
)


# ---------- tiny UX helpers ------------------------------------------------

BOLD  = '\033[1m'
DIM   = '\033[2m'
GREEN = '\033[32m'
RED   = '\033[31m'
YEL   = '\033[33m'
END   = '\033[0m'

def _tty():
    return sys.stdout.isatty()

def info(msg):  print(f"{BOLD}»{END} {msg}" if _tty() else f"» {msg}")
def ok(msg):    print(f"{GREEN}✓{END} {msg}" if _tty() else f"OK  {msg}")
def warn(msg):  print(f"{YEL}!{END} {msg}"   if _tty() else f"!   {msg}")
def fail(msg):  print(f"{RED}✗{END} {msg}"   if _tty() else f"FAIL {msg}")
def dim(msg):   print(f"{DIM}{msg}{END}"     if _tty() else msg)

def prompt(question, default=None):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        try:
            ans = input(f"  {question}{suffix}: ").strip()
        except EOFError:
            print(); sys.exit(130)
        if ans:
            return ans
        if default is not None:
            return default

def confirm(question, default=True):
    suffix = ' [Y/n]' if default else ' [y/N]'
    while True:
        try:
            ans = input(f"  {question}{suffix}: ").strip().lower()
        except EOFError:
            print(); sys.exit(130)
        if not ans:
            return default
        if ans in ('y', 'yes'):  return True
        if ans in ('n', 'no'):   return False


# ---------- step 1: AC14K_M discovery -------------------------------------
# Note: we deliberately do NOT do a bare TCP reachability probe before
# the real TLS handshake. The dryer's OCF stack treats a plain
# TCP-open-then-close (no TLS) as anomalous and enters a defensive state
# that closes subsequent handshakes' sockets immediately after CSM.
# Empirically observed; see commit history. Reachability is checked
# implicitly when we open TLS in step 3.

def find_ac14km():
    """Look in ./certs/ for the AC14K_M cert + key under any of the
    common filenames. Returns (cert_path, key_path) or (None, None)."""
    cert_candidates = ['ac14k_m.pem', 'AC14K_M.pem', 'cert_1.pem']
    key_candidates  = ['ac14k_m.key', 'AC14K_M.key', 'key.pem', 'ac14k_m_key.pem']
    cert = next((CERTS_DIR / n for n in cert_candidates if (CERTS_DIR / n).exists()), None)
    key  = next((CERTS_DIR / n for n in key_candidates  if (CERTS_DIR / n).exists()), None)
    return cert, key


def check_openssl():
    """Bootstrap shells out to openssl for cert generation — SHA-1 signing
    was removed from python-cryptography in v43, and openssl is ubiquitous
    enough that requiring it is reasonable."""
    if shutil.which('openssl') is None:
        fail("openssl not found in PATH — required for cert generation")
        return False
    return True


def _run(cmd, **kw):
    """Wrapper that surfaces stderr on failure."""
    res = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if res.returncode != 0:
        raise RuntimeError(
            f"`{' '.join(cmd)}` failed:\n{res.stderr.strip() or res.stdout.strip()}"
        )
    return res


def _openssl_config(common_name, hub_uuid=None, include_samsung_role=True):
    """Return an OpenSSL config snippet matching the proven canonical recipe
    used to generate the original working `mega_chain.pem` for this project
    (see spoof/mega_ext.cnf). All four SAN entries and the `clientAuth,
    serverAuth` EKU values are defensive — the dryer's `memmem` scan only
    cares about the Subject DN, but adjacent tooling reads the rest."""
    v3_lines = [
        "basicConstraints = CA:FALSE",
        "keyUsage = digitalSignature, keyEncipherment",
        f"extendedKeyUsage = clientAuth, serverAuth, {SAMSUNG_IOT_IDENTITY_OID}",
    ]
    if hub_uuid:
        v3_lines.append("subjectAltName = @alt_names")
    if include_samsung_role:
        v3_lines.append(
            f"{SAMSUNG_ROLE_OID} = ASN1:UTF8String:samsung.role.hub")
    sections = [
        "[ req ]",
        "distinguished_name = dn",
        "prompt = no",
        "req_extensions = v3",
        "",
        "[ dn ]",
        f"CN = {common_name}",
        "O = Samsung Electronics",
        "C = KR",
        "",
        "[ v3 ]",
        *v3_lines,
    ]
    if hub_uuid:
        # Belt-and-braces SAN entries — three URI forms and a DNS name.
        # Matches the canonical mega_ext.cnf exactly so the leaf is
        # bit-for-bit equivalent to the cert known to authenticate.
        sections += [
            "",
            "[ alt_names ]",
            f"URI.1 = urn:uuid:{hub_uuid}",
            f"URI.2 = uri:uuid:{hub_uuid}",
            f"URI.3 = uuid:{hub_uuid}",
            f"DNS.1 = {hub_uuid}",
        ]
    return "\n".join(sections) + "\n"


def _generate_signed_cert(*, common_name, hub_uuid, include_samsung_role,
                          ca_cert, ca_key, out_key, out_cert, days):
    """Generate an RSA-2048 key + SHA-1 signed cert via openssl."""
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        conf = tdp / 'leaf.cnf'
        csr  = tdp / 'leaf.csr'
        conf.write_text(_openssl_config(common_name, hub_uuid,
                                        include_samsung_role))
        # 1) key + CSR with extensions baked into req_extensions
        _run(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes',
              '-keyout', str(out_key), '-out', str(csr), '-config', str(conf)])
        # 2) sign with AC14K_M, SHA-1, copy the v3 extensions through
        _run(['openssl', 'x509', '-req', '-in', str(csr),
              '-CA', str(ca_cert), '-CAkey', str(ca_key),
              '-CAcreateserial', '-out', str(out_cert),
              '-days', str(days), '-sha1',
              '-extfile', str(conf), '-extensions', 'v3'])
    os.chmod(out_key, 0o600)


def generate_leaf(hub_uuid, ca_cert, ca_key, out_dir):
    """The real leaf — Subject CN contains `urn:uuid:<HUB_UUID>` so the
    dryer's `memmem` scan recognises us as the SmartThings hub. Writes
    mega.key and mega_chain.pem (leaf || AC14K_M)."""
    subject_uri = f"urn:uuid:{hub_uuid}"
    out_key   = out_dir / 'mega.key'
    out_leaf  = out_dir / 'mega_leaf.pem'
    out_chain = out_dir / 'mega_chain.pem'
    _generate_signed_cert(
        common_name=subject_uri,
        hub_uuid=hub_uuid,
        include_samsung_role=True,
        ca_cert=ca_cert, ca_key=ca_key,
        out_key=out_key, out_cert=out_leaf,
        days=365 * 5,
    )
    # Concatenate leaf || AC14K_M for the bridge's load_cert_chain.
    out_chain.write_bytes(out_leaf.read_bytes() + Path(ca_cert).read_bytes())
    out_leaf.unlink()
    return out_key, out_chain


def generate_probe(ca_cert, ca_key, tmp_dir):
    """Throwaway leaf with NO `uuid:` in the Subject DN — the dryer treats
    us as an anonymous-but-CA-trusted peer. Used once to attempt the
    anonymous ACL read; never written to disk outside tmp_dir."""
    out_key   = tmp_dir / 'probe.key'
    out_leaf  = tmp_dir / 'probe.pem'
    out_chain = tmp_dir / 'probe_chain.pem'
    _generate_signed_cert(
        common_name='samsung-local-bootstrap-probe',
        hub_uuid=None,
        include_samsung_role=False,
        ca_cert=ca_cert, ca_key=ca_key,
        out_key=out_key, out_cert=out_leaf,
        days=30,
    )
    out_chain.write_bytes(out_leaf.read_bytes() + Path(ca_cert).read_bytes())
    out_leaf.unlink()
    return out_key, out_chain


# ---------- step 4: anonymous ACL read ------------------------------------

def open_tls(host, port, cert_path, key_path, timeout=8):
    """Same pattern as samsung_dryer.bridge._open_tls — drop OpenSSL 3.x
    security level so SHA-1 leaves are accepted."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers('DEFAULT:@SECLEVEL=0')
    except ssl.SSLError:
        pass
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    raw = socket.create_connection((host, port), timeout=timeout)
    sock = ctx.wrap_socket(raw)
    sock.send(CSM)
    sock.settimeout(2)
    try: read_tcp(sock)
    except (socket.timeout, ConnectionError): pass
    sock.settimeout(timeout)
    return sock


def coap_get(sock, path_segs, token=b'\x01\x02\x03\x04'):
    opts = [(URI_PATH, s.encode()) for s in path_segs]
    sock.send(enc_tcp(0x01, token=token, opts_b=enc_opts(opts)))
    code, _tok, _opts, pl = read_tcp(sock)
    return code, pl


def extract_hub_uuid_from_doxm(doxm_payload):
    """Parse the CBOR-encoded /oic/sec/doxm response and return the hub
    UUID. On this firmware, `devowneruuid` and `rowneruuid` both carry
    the SmartThings hub's UUID — they're the same value in practice and
    we prefer devowneruuid (the OCF spec field for the device's owner)."""
    try:
        doc = cbor2.loads(doxm_payload)
    except Exception as e:
        warn(f"doxm CBOR decode failed: {e}")
        return None
    if not isinstance(doc, dict):
        warn(f"doxm decoded to {type(doc).__name__}, expected dict")
        return None
    for key in ('devowneruuid', 'rowneruuid'):
        val = doc.get(key)
        if isinstance(val, str) and looks_like_uuid(val):
            return val
    warn(f"doxm payload had no devowneruuid/rowneruuid (keys: "
         f"{list(doc.keys())})")
    return None


def try_anonymous_doxm_read(host, port, ca_cert, ca_key):
    """Discover the hub UUID by reading /oic/sec/doxm anonymously.

    Mechanism: the dryer's baseline ACL contains a wildcard ACE
    (`subjectuuid=*` perm=2) granting any authenticated peer read access
    to /oic/sec/doxm. We don't need to be the hub — we just need to
    complete a chain-valid TLS handshake. doxm.devowneruuid is the
    SmartThings hub's UUID."""
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        try:
            key_path, chain_path = generate_probe(ca_cert, ca_key, tdp)
        except RuntimeError as e:
            warn(f"probe cert generation failed: {e}")
            return None
        try:
            sock = open_tls(host, port, chain_path, key_path)
        except ConnectionRefusedError:
            fail(f"connection refused at {host}:{port} — wrong port, or "
                 f"the dryer isn't on the LAN.")
            return None
        except (ssl.SSLError, OSError) as e:
            warn(f"anonymous TLS handshake failed: {e}")
            return None
        try:
            code, pl = coap_get(sock, ['oic', 'sec', 'doxm'])
        except ConnectionError as e:
            warn(f"dryer closed the CoAP session immediately after CSM: {e}")
            dim("    This usually means the dryer's OCF stack is in a "
                "defensive cooldown — typically caused by a concurrent "
                "TLS session (the bridge running) or rapid recent probes. "
                "Stop main.py / the bridge container, wait ~60s, then re-run.")
            return None
        finally:
            try: sock.close()
            except Exception: pass
        if code != 0x45:
            warn(f"GET /oic/sec/doxm → {fmt_code(code)} (expected 2.05) "
                 f"— switching to manual entry")
            return None
        return extract_hub_uuid_from_doxm(pl)


# ---------- step 5: .env ---------------------------------------------------

def maybe_write_env(appliance_ip, appliance_port):
    env_path = REPO_ROOT / '.env'
    example  = REPO_ROOT / '.env.example'
    if not example.exists():
        warn(".env.example missing — skipping .env generation")
        return
    if env_path.exists():
        if not confirm("Overwrite existing .env with new IP/port? (other "
                       "values preserved)", default=False):
            dim("    leaving .env untouched")
            return
    text = example.read_text()
    text = _replace_kv(text, 'APPLIANCE_IP',       appliance_ip)
    text = _replace_kv(text, 'APPLIANCE_OCF_PORT', str(appliance_port))
    env_path.write_text(text)
    ok(f"wrote {env_path} — fill in MQTT_BROKER / MQTT_USER / MQTT_PASS before running main.py")


def _replace_kv(text, key, value):
    out = []
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
        else:
            out.append(line)
    return '\n'.join(out) + ('\n' if text.endswith('\n') else '')


# ---------- step 6: hub UUID validation -----------------------------------

def looks_like_uuid(s):
    import re
    return bool(re.fullmatch(
        r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
        r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', s.strip()))


# ---------- main ----------------------------------------------------------

def main():
    print()
    print(f"{BOLD}samsung-appliance-local — bootstrap{END}" if _tty()
          else "samsung-appliance-local — bootstrap")
    print(f"{DIM}This will discover your dryer, locate your CA cert, and "
          f"generate the leaf used to authenticate as the SmartThings hub.{END}"
          if _tty() else
          "This will discover your dryer, locate your CA cert, and generate "
          "the leaf used to authenticate as the SmartThings hub.")
    print()

    # --- 1. dryer location ---
    # Reachability is verified implicitly by the TLS handshake in step 3.
    # We can't do a bare TCP probe here — that knocks the dryer's OCF
    # session into a defensive state and breaks the subsequent TLS attempt.
    info("Step 1 — dryer location")
    appliance_ip   = prompt("Dryer IP on your LAN", default=None)
    appliance_port = int(prompt("OCF port (newer firmware uses 49154)",
                                default='49154'))
    dim(f"    Will connect to {appliance_ip}:{appliance_port} once we have "
        f"a probe cert.")
    print()

    # --- 2. AC14K_M ---
    info("Step 2 — locate Samsung's AC14K_M intermediate CA")
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = find_ac14km()
    if cert_path is None or key_path is None:
        fail(f"AC14K_M cert + key not found in {CERTS_DIR}/")
        dim(f"    Fetch them from: {AC14K_M_SOURCE}")
        dim(f"    Place as: {CERTS_DIR}/ac14k_m.pem and "
            f"{CERTS_DIR}/ac14k_m.key (other common names accepted)")
        return 2
    ok(f"found CA cert: {cert_path.name}")
    ok(f"found CA key:  {key_path.name}")
    if not check_openssl():
        return 2
    print()

    # --- 3. hub UUID ---
    info("Step 3 — discover your SmartThings hub UUID")
    dim("    Reading /oic/sec/doxm anonymously — the dryer's baseline ACL")
    dim("    allows any authenticated peer to read it (wildcard ACE).")
    hub_uuid = try_anonymous_doxm_read(appliance_ip, appliance_port,
                                       cert_path, key_path)
    if hub_uuid:
        ok(f"discovered hub UUID from /oic/sec/doxm: {hub_uuid}")
        if not confirm("Use this UUID?", default=True):
            hub_uuid = None
    if not hub_uuid:
        warn("Falling back to manual entry. Options B/C in the README "
             "describe how to obtain it.")
        while True:
            hub_uuid = prompt("Hub UUID (8-4-4-4-12 hex)", default=None)
            if looks_like_uuid(hub_uuid):
                hub_uuid = hub_uuid.strip().lower()
                break
            warn("That doesn't look like a UUID. Format: "
                 "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
    print()

    # --- 4. leaf ---
    info("Step 4 — generate the leaf cert (mega.key + mega_chain.pem)")
    mega_key   = CERTS_DIR / 'mega.key'
    mega_chain = CERTS_DIR / 'mega_chain.pem'
    if mega_key.exists() or mega_chain.exists():
        warn(f"existing leaf cert detected in {CERTS_DIR}/")
        if not confirm("Overwrite?", default=False):
            dim("    leaving existing leaf in place — skipping generation")
            print()
            maybe_write_env(appliance_ip, appliance_port)
            print()
            ok("Done.")
            return 0
    try:
        key_out, chain_out = generate_leaf(hub_uuid, cert_path, key_path,
                                           CERTS_DIR)
    except RuntimeError as e:
        fail(f"leaf cert generation failed: {e}")
        return 2
    ok(f"wrote {key_out}")
    ok(f"wrote {chain_out}")
    print()

    # --- 5. .env ---
    info("Step 5 — populate .env")
    maybe_write_env(appliance_ip, appliance_port)
    print()

    ok("Done. Next: edit .env to fill in MQTT_BROKER / MQTT_USER / "
       "MQTT_PASS, then run main.py.")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(); sys.exit(130)
