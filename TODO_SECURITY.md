# Security TODOs

Findings from initial security review (2026-06-27). These are hardening issues,
not malicious code — the repo is safe to run. Fix before exposing to untrusted networks.

---

## 1. DTLS Certificate Verification Disabled

**File:** `samsung_appliance/coap_dtls.py:228`
**Severity:** High | **Category:** Authentication Bypass / Crypto

The DTLS context uses `SSL.VERIFY_NONE` with a lambda that accepts any certificate.
The bridge does not verify the appliance's identity.

**Exploit:** LAN attacker ARP-spoofs the appliance IP, presents a self-signed cert,
and either poisons state payloads (e.g. fake oven temp) or intercepts/drops commands.

**Fix:** Use `SSL.VERIFY_PEER` + `ctx.load_verify_locations()` pointing to the Samsung
CA cert already on disk. The `@SECLEVEL=0` SHA-1 workaround can coexist with peer
verification — `VERIFY_NONE` is not required for it.

---

## 2. MQTT — No TLS, No Publisher Authentication

**Files:** `main.py:137`, `samsung_appliance/config.py:68`
**Severity:** High | **Category:** Missing Encryption / Unauthenticated Commands

MQTT connects on plaintext port 1883. `tls_set()` is never called. The bridge
executes any payload published to `<prefix>/cmd/#` — only value range is checked,
not who sent it. Credentials (if set) are sent in cleartext CONNECT packets.

**Exploit:**
- Any LAN host publishes `270` to `samsung_oven/cmd/setpoint` → oven raised to max
- LAN attacker sniffs TCP stream → captures `MQTT_USER`/`MQTT_PASS`
- All appliance state (energy, temp, run state) readable by any subscriber

**Fix:** Call `cli.tls_set()` with the broker CA before connecting. Add
`MQTT_TLS_CA` / `MQTT_TLS_CERT` / `MQTT_TLS_KEY` to the `.env` schema.
Enforce broker-side ACLs so only the bridge credential can publish to `cmd/#`.

---

## 3. AC14K_M Dependency — Verified Required, But Looser Than README Claims

**Files:** `local-tools/setup_cert.py:151`, `local-tools/setup_cert_sha256.py`,
`local-tools/setup_cert_leaf_only.py`, `local-tools/setup_ac14k_match.py`,
`local-tools/setup_selfsigned_cert.py`, `samsung_appliance/coap_dtls.py:229-233`
**Severity:** Medium | **Category:** Crypto / Misleading Documentation

Empirically tested on 10.0.0.129 and 10.0.0.254 (oven-class firmware, port 49154).
Results below pin down exactly what the firmware's DTLS auth check requires:

| Test | Issuer | Sig alg | Chain | Result |
|------|--------|---------|-------|--------|
| Self-signed CA, random DN | ours | SHA-256 | leaf+CA | 4.01 ✗ |
| AC14K_M (real key) | AC14K_M | SHA-1 | leaf+chain | 2.05 ✓ |
| AC14K_M (real key) | AC14K_M | SHA-1 | leaf only | 2.05 ✓ |
| AC14K_M (real key) | AC14K_M | SHA-256 | leaf+chain | 2.05 ✓ |
| Self-signed CA, AC14K_M-shaped DN | ours | SHA-1 | leaf+CA | 4.01 ✗ |

**Conclusion:** the firmware validates the leaf's signature against AC14K_M's
pinned public key. Chain validation, issuer-DN matching, and SHA-1 pinning are
NOT what the firmware is checking. The README's "the appliance validates chains
to AC14K_M" framing (`README.md:73-127`) is more restrictive than reality — the
check is a signature check, not a chain check.

### Cleanup opportunities (low risk, follow-up commits)

1. **`coap_dtls.py:229-233` — drop `@SECLEVEL=0`.** The comment says it's
   needed because "the AC14K_M-rooted ab0b0ac4 chain is SHA-1 signed." But we
   just proved the firmware accepts SHA-256-signed leaves, and `setup_cert.py`
   could trivially be updated to sign with SHA-256 by default. SHA-1 is dead
   weight in the runtime.

2. **`setup_cert.py:151` — sign with SHA-256 instead of SHA-1.** Modern,
   faster, no deprecation noise. Works on tested firmware.

3. **Document leaf-only mode.** `setup_cert_leaf_only.py` shows only the leaf
   cert needs to be sent — the chain is dead weight on every handshake. The
   runtime could be updated to extract the leaf from the fullchain and send
   just that. Saves a few KB per handshake and clarifies the trust model.

4. **Update README auth section.** The current text implies full chain
   validation. The actual check is signature-only. Rewrite to reflect what
   the firmware actually does.

These are not security fixes per se — the current code authenticates fine.
They're correctness/clarity improvements that the test artifacts already
support.

### Pending question: is the UUID validated at all?

**Resolved 2026-06-28.** `local-tools/setup_uuid_probe.py` mints three
AC14K_M-signed leaf certs that vary only in the UUID embedded in the
Subject DN, then authenticates with each against the same appliance:

| Probe | Subject `uuid:` | 10.0.0.129 | 10.0.0.254 |
|-------|----------------|------------|------------|
| `known_good` | `ab0b0ac4-aae9-4958-a04d-8ec36fe1b2f9` (cloud-bridge) | 2.05 ✓ | 2.05 ✓ |
| `other_uuid` | `11111111-2222-3333-4444-555555555555` (not in any ACL) | 4.01 ✗ | 4.01 ✗ |
| `no_uuid` | absent — Subject has no `uuid:` substring at all | 4.01 ✗ | 4.01 ✗ |

**Conclusion:** the firmware DOES validate the UUID. A cert signed by
AC14K_M with the wrong UUID is rejected as unauthorized, and a cert with
no UUID substring at all is rejected the same way. The README's claim
that the firmware extracts `uuid:` and ACL-matches it is confirmed on
both appliances. So while the chain/issuer-DN/sig-alg checks are loose
or absent, the UUID check is tight — exactly the trust anchor the
existing auth model is built around.
