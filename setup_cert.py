#!/usr/bin/env python3
"""
setup_cert.py — One-shot client cert generator for local DTLS-CoAP
access to Samsung Tizen/RT-OCF appliances on your LAN.

Builds a client cert keyed to the identity that each appliance's factory
ACL already grants `perm=31` on `href=*`. Everything used at build time
is fetched live from public sources; nothing is hardcoded.

Steps:

1. Fetch the AC14K_M intermediate CA bundle (CA cert + key + upstream
   chain) from a public mirror.
2. Open a TLS connection to a Samsung cloud endpoint, read its
   server cert, and extract the `uuid:<UUID>` token from the subject DN.
3. Generate a fresh RSA-2048 key pair (yours, not Samsung's).
4. Build a CSR with the UUID in CN, OU, and SAN.
5. Sign the CSR with AC14K_M using SHA-1, matching the on-device
   trust hierarchy.
6. Assemble `<uuid>.key`, `<uuid>.pem`, `<uuid>_fullchain.pem`.
7. With `--test`, DTLS-handshake to an appliance and GET
   `/oic/sec/acl`; a 2.05 reply confirms the cert is accepted.

Background:

- The cloud-bridge UUID is published in Samsung's own TLS server cert
  subject DN — anyone can read it with `openssl s_client`.
- TizenRT iotivity locates the peer UUID via `memmem(subject, "uuid:")`,
  so the same UUID in any RDN works.
- The AC14K_M intermediate has been public for years and remains in
  current firmware trust stores.

Fallbacks if the live fetches fail:

  # Manual UUID lookup
  openssl s_client -connect <samsung-host>:443 -servername <samsung-host> \\
                   -showcerts < /dev/null 2>/dev/null \\
    | openssl x509 -noout -subject
  UUID=<paste-uuid-here> python setup_cert.py ...

  # Manual AC14K_M bundle (point at any mirror)
  AC14K_M_CERT_BUNDLE=/path/to/cert.pem python setup_cert.py

Usage:

    python setup_cert.py
    python setup_cert.py --test
    TARGET_IP=192.168.1.1 python setup_cert.py --test

Env overrides (all optional):
    AC14K_M_CERT         AC14K_M cert PEM (skip live fetch)
    AC14K_M_KEY          AC14K_M private key PEM
    AC14K_M_CERT_BUNDLE  combined PEM (key + 4 certs)
    CHAIN_DIR            dir containing cert_1..4.pem
    BRAYSTORM_URL        bundle source URL
    UUID                 supply the UUID manually
    OUT_DIR              output dir (default ./certs/)
    TARGET_IP            device IP for --test
    TARGET_PORT          device port for --test (default 49154)
"""
import argparse
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


SAMSUNG_HOST = 'connect-v2.samsungiotcloud.com'
SAMSUNG_PORT = 443

BRAYSTORM_URL = (
    'https://raw.githubusercontent.com/brayStorm/samsung-appliance-token/main/cert.pem'
)

BUNDLE_CERT_NAMES = ['ac14k_m.pem', 'cert_2.pem', 'cert_3.pem', 'cert_4.pem']


def fetch_samsung_uuid(timeout=10):
    """Return (uuid, server_cert_pem) or (None, None) on failure."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((SAMSUNG_HOST, SAMSUNG_PORT), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=SAMSUNG_HOST) as s:
                der = s.getpeercert(binary_form=True)
    except Exception as e:
        print(f"[!] Could not fetch Samsung cloud cert: {e}", file=sys.stderr)
        return None, None

    tmp = tempfile.NamedTemporaryFile(suffix='.der', delete=False)
    tmp.write(der); tmp.close()
    try:
        subj = subprocess.run(
            ['openssl', 'x509', '-inform', 'DER', '-in', tmp.name,
             '-noout', '-subject'],
            capture_output=True, text=True, check=True).stdout
        pem = subprocess.run(
            ['openssl', 'x509', '-inform', 'DER', '-in', tmp.name],
            capture_output=True, text=True, check=True).stdout
    finally:
        os.unlink(tmp.name)

    m = re.search(r'uuid:([0-9a-fA-F-]{36})', subj)
    if not m:
        print(f"[!] No `uuid:...` in subject: {subj.strip()}", file=sys.stderr)
        return None, pem
    return m.group(1).lower(), pem


def split_bundle_pem(text):
    """Split a combined PEM into (key_pem, [cert_pem, ...]).
    Expects 1 private key + 4 certificates (leaf + 3 upstream)."""
    key_re = re.compile(
        r'-----BEGIN (?:RSA )?PRIVATE KEY-----.*?-----END (?:RSA )?PRIVATE KEY-----',
        re.DOTALL)
    cert_re = re.compile(
        r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',
        re.DOTALL)
    keys = key_re.findall(text)
    certs = cert_re.findall(text)
    if len(keys) != 1:
        raise ValueError(f"expected 1 private key block, found {len(keys)}")
    if len(certs) != 4:
        raise ValueError(f"expected 4 certificate blocks, found {len(certs)}")
    return keys[0] + '\n', [c + '\n' for c in certs]


def fetch_ac14k_bundle(dest_dir, timeout=15):
    """Download and split the AC14K_M bundle. Returns
    {ac14k_cert, ac14k_key, chain_dir} of paths in dest_dir."""
    url = os.environ.get('BRAYSTORM_URL', BRAYSTORM_URL)
    print(f"  Fetching AC14K_M bundle...")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        raise RuntimeError(f"bundle fetch failed: {e}") from e

    key_pem, cert_pems = split_bundle_pem(data)

    dest = Path(dest_dir); dest.mkdir(parents=True, exist_ok=True)
    key_path = dest / 'ac14k_m.key'
    key_path.write_text(key_pem)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    cert_paths = []
    for name, pem in zip(BUNDLE_CERT_NAMES, cert_pems):
        p = dest / name
        p.write_text(pem)
        cert_paths.append(p)
    (dest / 'cert_1.pem').write_text(cert_pems[0])

    return {
        'ac14k_cert': cert_paths[0],
        'ac14k_key':  key_path,
        'chain_dir':  dest,
    }


def verify_cert_key_pair(cert_path, key_path):
    """Compare modulus to confirm cert and key pair."""
    def modulus(args):
        out = subprocess.run(
            ['openssl'] + args, capture_output=True, text=True, check=True).stdout
        m = re.search(r'Modulus=([0-9A-Fa-f]+)', out)
        return m.group(1) if m else None
    try:
        cm = modulus(['x509', '-noout', '-modulus', '-in', str(cert_path)])
        km = modulus(['rsa', '-noout', '-modulus', '-in', str(key_path)])
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"openssl modulus extraction failed: {e.stderr}") from e
    if not cm or not km:
        raise RuntimeError("could not extract modulus from cert and/or key")
    if cm != km:
        raise RuntimeError(
            f"AC14K_M cert and key do not pair (cert modulus != key modulus)")


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def mint_cert(uuid, ac14k_cert, ac14k_key, chain_files, out_dir):
    """Mint a fresh-keyed client cert with UUID in CN+OU+SAN, signed by
    AC14K_M with SHA-1. Returns dict of output paths."""
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    paths = {
        'key':       out / 'client.key',
        'csr':       out / 'client.csr',
        'leaf':      out / 'client.pem',
        'fullchain': out / 'client_fullchain.pem',
        'ext':       out / 'ext.cnf',
        'srl':       out / 'client.srl',
    }

    paths['ext'].write_text(f"""basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth, serverAuth, 1.3.6.1.4.1.51414.0.1.2
subjectAltName = @alt_names
1.3.6.1.4.1.51414.1.3 = ASN1:UTF8String:samsung.role.hub

[alt_names]
URI.1 = urn:uuid:{uuid}
URI.2 = uri:uuid:{uuid}
URI.3 = uuid:{uuid}
DNS.1 = {uuid}
""")

    run(['openssl', 'genrsa', '-out', str(paths['key']), '2048'])
    try:
        os.chmod(paths['key'], 0o600)
    except OSError:
        pass

    subject = (
        f"/OU=uuid:{uuid}"
        f"/CN=urn:uuid:{uuid}"
        f"/O=Samsung Electronics"
        f"/C=KR"
    )
    run(['openssl', 'req', '-new', '-key', str(paths['key']),
         '-out', str(paths['csr']), '-subj', subject])

    run(['openssl', 'x509', '-req', '-in', str(paths['csr']),
         '-CA', str(ac14k_cert), '-CAkey', str(ac14k_key),
         '-CAcreateserial', '-CAserial', str(paths['srl']),
         '-out', str(paths['leaf']), '-days', '3650',
         '-extfile', str(paths['ext']), '-sha1'])

    parts = [paths['leaf'].read_text()]
    for p in chain_files:
        parts.append(Path(p).read_text())
    paths['fullchain'].write_text(''.join(parts))

    return paths


def test_handshake(target_ip, target_port, cert_path, key_path):
    """DTLS-handshake to a device and GET /oic/sec/acl.
    2.05 means the cert authenticated; 4.01 means it didn't."""
    try:
        from OpenSSL import SSL
    except ImportError:
        print("[!] pyOpenSSL not installed — skipping connectivity test")
        print("    Install with: pip install pyOpenSSL")
        return None

    import time

    ctx = SSL.Context(SSL.DTLS_METHOD)
    ctx.set_verify(SSL.VERIFY_NONE, lambda *a: True)
    ctx.set_cipher_list(b'ECDHE-ECDSA-AES128-GCM-SHA256:@SECLEVEL=0')
    ctx.use_certificate_chain_file(str(cert_path))
    ctx.use_privatekey_file(str(key_path))
    ctx.check_privatekey()
    conn = SSL.Connection(ctx, None)
    conn.set_connect_state(); conn.set_ciphertext_mtu(1200)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)
    dest = (target_ip, target_port)

    def split_dtls(buf):
        o, out = 0, []
        while o + 13 <= len(buf):
            L = int.from_bytes(buf[o+11:o+13], 'big'); end = o + 13 + L
            if end > len(buf): break
            out.append(buf[o:end]); o = end
        return out

    print(f"[+] DTLS handshake to {target_ip}:{target_port}...")
    t0 = time.time()
    handshake_ok = False
    while time.time() - t0 < 12:
        try:
            conn.do_handshake(); handshake_ok = True; break
        except SSL.WantReadError: pass
        except SSL.Error as e:
            print(f"    SSL error: {e}"); return False
        try:
            out = conn.bio_read(65535)
            if out:
                for r in split_dtls(out): sock.sendto(r, dest)
        except SSL.WantReadError: pass
        try:
            data, _ = sock.recvfrom(65535)
            if data: conn.bio_write(data)
        except socket.timeout: pass
        time.sleep(0.05)

    if not handshake_ok:
        print(f"    handshake TIMEOUT after {time.time()-t0:.1f}s")
        sock.close(); return False
    print(f"    handshake OK in {time.time()-t0:.2f}s")

    msg = (
        bytes([0x41, 0x01, 0xab, 0x00, 0xaa])
        + bytes([0xb3]) + b'oic' + bytes([0x03]) + b'sec' + bytes([0x03]) + b'acl'
        + bytes([0x61]) + b'\x3c'
    )
    conn.send(msg)
    try:
        while True:
            out = conn.bio_read(65535)
            if not out: break
            sock.sendto(out, dest)
    except SSL.WantReadError: pass

    deadline = time.time() + 6
    while time.time() < deadline:
        try:
            data, _ = sock.recvfrom(65535)
            if data:
                conn.bio_write(data)
                try:
                    pl = conn.recv(65535)
                    code = pl[1]
                    print(f"    GET /oic/sec/acl -> {code>>5}.{code&0x1F:02d}")
                    if code == 0x45:
                        print(f"    OK — cert accepted by the device ACL")
                        sock.close(); return True
                    else:
                        print(f"    Unexpected response code")
                        sock.close(); return False
                except SSL.WantReadError: continue
        except socket.timeout: pass
        time.sleep(0.05)
    print(f"    GET /oic/sec/acl TIMEOUT")
    sock.close(); return False


def resolve_ac14k_inputs(out_dir):
    """Return (ac14k_cert, ac14k_key, chain_files).

    Resolution order: env-supplied cert+key+chain dir, then env-supplied
    combined bundle, then live fetch from BRAYSTORM_URL."""
    env_cert = os.environ.get('AC14K_M_CERT')
    env_key  = os.environ.get('AC14K_M_KEY')
    env_dir  = os.environ.get('CHAIN_DIR')
    env_bundle = os.environ.get('AC14K_M_CERT_BUNDLE')

    if env_cert and env_key and env_dir:
        print(f"  Using AC14K_M materials from env vars")
        for path, label in [(env_cert, 'AC14K_M_CERT'), (env_key, 'AC14K_M_KEY')]:
            if not Path(path).is_file():
                raise FileNotFoundError(f"{label} not found: {path}")
        chain = sorted(Path(env_dir).glob('cert_*.pem'))
        if len(chain) < 4:
            raise RuntimeError(
                f"CHAIN_DIR needs cert_1..cert_4.pem (leaf + 3 upstream); "
                f"found: {[p.name for p in chain]}")
        return Path(env_cert), Path(env_key), chain

    bundle_dir = Path(out_dir) / '.bundle'

    if env_bundle:
        print(f"  Splitting AC14K_M bundle from {env_bundle}")
        text = Path(env_bundle).read_text()
        key_pem, cert_pems = split_bundle_pem(text)
        bundle_dir.mkdir(parents=True, exist_ok=True)
        key_path = bundle_dir / 'ac14k_m.key'
        key_path.write_text(key_pem)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        for name, pem in zip(BUNDLE_CERT_NAMES, cert_pems):
            (bundle_dir / name).write_text(pem)
        (bundle_dir / 'cert_1.pem').write_text(cert_pems[0])
        chain = sorted(bundle_dir.glob('cert_*.pem'))
        return bundle_dir / 'ac14k_m.pem', key_path, chain

    try:
        result = fetch_ac14k_bundle(bundle_dir)
    except Exception as e:
        msg = (
            f"\n[!] Could not fetch AC14K_M bundle: {e}\n"
            f"\n  Workarounds:\n"
            f"    - Point at a local PEM:  AC14K_M_CERT_BUNDLE=/path/to/cert.pem python setup_cert.py\n"
            f"    - Point at a mirror:     BRAYSTORM_URL=https://<mirror>/cert.pem python setup_cert.py\n"
        )
        print(msg, file=sys.stderr)
        raise SystemExit(3)
    chain = sorted(result['chain_dir'].glob('cert_*.pem'))
    return result['ac14k_cert'], result['ac14k_key'], chain


def main():
    p = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__)
    p.add_argument('--test', action='store_true',
                   help='After minting, attempt a DTLS handshake to TARGET_IP:TARGET_PORT')
    args = p.parse_args()

    out_dir    = os.environ.get('OUT_DIR', './certs/')
    target_ip  = os.environ.get('TARGET_IP')
    target_port = int(os.environ.get('TARGET_PORT', 49154))
    uuid_override = os.environ.get('UUID')

    print("=" * 60)
    print("Phase 1: AC14K_M signing materials")
    print("=" * 60)
    try:
        ac14k_cert, ac14k_key, chain_files = resolve_ac14k_inputs(out_dir)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[!] {e}", file=sys.stderr)
        return 2
    print(f"  AC14K_M cert: {ac14k_cert}")
    print(f"  AC14K_M key:  {ac14k_key}")
    print(f"  chain:        {len(chain_files)} certs ({', '.join(p.name for p in chain_files)})")

    try:
        verify_cert_key_pair(ac14k_cert, ac14k_key)
    except RuntimeError as e:
        print(f"[!] AC14K_M cert/key sanity check failed: {e}", file=sys.stderr)
        return 2
    print(f"  cert/key modulus pair OK")

    print()
    print("=" * 60)
    print("Phase 2: identify peer UUID")
    print("=" * 60)
    samsung_pem = None
    if uuid_override:
        uuid = uuid_override.lower()
        print(f"  Using UUID from env: {uuid}")
    else:
        print(f"  Fetching from {SAMSUNG_HOST}:{SAMSUNG_PORT}...")
        uuid, samsung_pem = fetch_samsung_uuid()
        if uuid is None:
            print(f"\n  [!] Live fetch failed.", file=sys.stderr)
            print(f"\n  Workaround:", file=sys.stderr)
            print(f"  1. From any machine with internet access, run:", file=sys.stderr)
            print(f"       openssl s_client -connect {SAMSUNG_HOST}:{SAMSUNG_PORT} \\", file=sys.stderr)
            print(f"                        -servername {SAMSUNG_HOST} \\", file=sys.stderr)
            print(f"                        -showcerts < /dev/null 2>/dev/null \\", file=sys.stderr)
            print(f"         | openssl x509 -noout -subject", file=sys.stderr)
            print(f"  2. Find OU=uuid:<UUID> in the subject.", file=sys.stderr)
            print(f"  3. Re-run with UUID=<uuid> ...", file=sys.stderr)
            return 3
        print(f"  Extracted UUID: {uuid}")
        if samsung_pem:
            samsung_ref = Path(out_dir); samsung_ref.mkdir(parents=True, exist_ok=True)
            (samsung_ref / 'samsung_cloud_leaf.pem').write_text(samsung_pem)
            print(f"  Saved server leaf cert to "
                  f"{samsung_ref / 'samsung_cloud_leaf.pem'}")

    print()
    print("=" * 60)
    print(f"Phase 3: mint client cert with UUID {uuid}")
    print("=" * 60)
    paths = mint_cert(uuid, ac14k_cert, ac14k_key, chain_files, out_dir)
    print(f"  key:       {paths['key']}")
    print(f"  leaf:      {paths['leaf']}")
    print(f"  fullchain: {paths['fullchain']}")

    subj_out = run(['openssl', 'x509', '-in', str(paths['leaf']), '-noout', '-subject'])
    print(f"  Subject: {subj_out.stdout.strip().replace('subject=', '')}")

    if args.test:
        print()
        print("=" * 60)
        print("Phase 4: verify cert against target appliance")
        print("=" * 60)
        if not target_ip:
            print("  [!] TARGET_IP not set; cannot run connectivity test", file=sys.stderr)
        else:
            result = test_handshake(target_ip, target_port, paths['fullchain'], paths['key'])
            if result is True:
                print("\n  Cert is functional. Drop fullchain.pem + key into your bridge config.")
            elif result is False:
                print("\n  Cert failed verification. Check target IP/port and try again.")

    print()
    print("=" * 60)
    print("Done. Output dir:", Path(out_dir).resolve())
    print("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())
