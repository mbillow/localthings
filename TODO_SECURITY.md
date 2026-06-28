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
