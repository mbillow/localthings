# SmartThings-Local

**Local-first Home Assistant integration for newer-generation Samsung connected appliances.** One process supervises multiple appliances (dryer + oven currently), each over its own CoAP-DTLS session, publishing state + writes through MQTT with HA auto-discovery — no SmartThings cloud round-trip for any of it.

<img width="778" height="367" alt="image" src="https://github.com/user-attachments/assets/cc1dca15-f272-4625-a13c-2dc82283ff95" />


> ### Proof of concept — collaborators wanted
>
> This is working code running in my home and I rely on it daily, but it's a **proof of concept**, not a polished product. No unit tests; one person's hardware as the validation set (one dryer model, one oven model); hand-rolled MQTT-based integration instead of a proper HA custom component; "wired-but-untested" comments scattered through the oven descriptor; brittle to per-firmware quirks (the "oven doesn't push OBSERVE on options writes" finding is the kind of thing that needs ongoing care).
>
> **I would love for someone to take this further and build a proper HA integration out of it.** All the protocol research is done — DTLS auth via Samsung's published cloud identity, token-stable Block2 reads, OBSERVE-then-fetchback notifications, write semantics, the optimistic-publish-then-verify pattern, brick-avoiding resource boundaries — and the descriptor pattern is the seed of a clean per-appliance abstraction. The HA-side polish that's missing is custom-component shape: config flow, native entity classes, async-Python DTLS instead of MQTT round-trips, error surfacing into HA's notification system, support across more firmware versions, and someone who actually lives in the HA codebase.
>
> If you're that person, get in touch — happy to co-author, hand off, or hand over entirely.

### What you get

- **Multi-appliance, one container.** Single Docker service holds N DTLS sessions in parallel, one per appliance, sharing one MQTT client. Adding an appliance class is ~150 lines and one descriptor file.
- **Bounded state latency.** Hot-tier resources (job state, door, operational state) refresh on a sub-second cadence regardless of whether the appliance has internet. Worst-case lag is the tier interval (≤1s idle, ≤500ms during an active cycle on the dryer).
- **Writes that work**: dryer Start/Pause/Stop, course selection, wrinkle prevent; oven lamp (light entity), sound, fast preheat, setpoint slider, mode select, stop.
- **Optimistic publish + verify**: HA sees the new value the instant the device 2.04-confirms the write; the PollScheduler verifies on its next tier tick (after a 4s defer past Samsung's fetchback-revert window).
- **HA Energy Dashboard ready** (dryer): live watts + cumulative kWh as `total_increasing`.
- **Bridge logs tagged per-appliance** with `<class>.<serial>` once each device's serial is read on connect — `dryer.<serial>` vs `oven.<serial>` interleaved in the same log stream, easy to grep.
- **Zero HA YAML.** Every entity is auto-discovered via MQTT discovery.
- **Your state stays on your LAN.** Bridge → broker → HA. Samsung's cloud sees nothing from HA. *(The appliance still maintains its own TLS session to Samsung — appliance design, not ours.)*

### Under the hood

Each appliance runs an independent bridge built around three coordinated pieces over one persistent DTLS session: a `StateCache` (single source of truth for all reps), a `PollScheduler` (tiered adaptive polling — hot/warm/cold + a periodic `/device/0` sweep), and a `KeepaliveTask` (CoAP empty-CON ping for DTLS-layer liveness, with consecutive-failure detection for MQTT availability). Tier cadences are descriptor-declared and were calibrated against the empirically-measured per-firmware ceilings (`local-tools/probe_poll_rate_combined.py`): dryer ~14 req/s, oven ~8 req/s. OBSERVE registrations (RFC 7641) are kept as an opportunistic freshness accelerator — when the appliance has internet and emits notifications, the cache absorbs them and the next-poll timer is reset for that resource; when it's air-gapped, polling alone carries the UX with no other code change. Token-stable Block2 (RFC 7959) handles multi-block reads. Writes are optimistically merged into the cache the moment the device 2.04-confirms, with the scheduler deferring that resource's next poll past the fetchback-revert window. Reconnect with exponential backoff on session errors.

Authentication uses a client cert keyed to the UUID published in Samsung's own wildcard cloud TLS cert. Every Samsung Tizen/RT-OCF appliance's factory ACL grants that UUID `perm=31` (full CRUDN) on `href=*`, so a single cert chain works across the whole fleet. Setup is one Python script.

---

## Part 1 — Is your appliance compatible?

Check before anything else; if it's older firmware, this project doesn't target it.

```sh
# UDP scan for DTLS-CoAP ports
nmap -Pn -sU -p 49152-49160 "$APPLIANCE_IP"
```

Read the result:

- **`49154/udp` (or similar 4915x) open|filtered with a DTLS handshake responding** → newer firmware (Tizen RT 3.x with DAWIT 3.0). This is what the bridge talks to.
- **Only `8888/tcp` open (token-based HTTPS)** → older firmware (~2018–2022). **Not supported here.**

### Tested combinations

| Appliance class | Model family | Confirmed |
|---|---|---|
| Dryer | DV5000T (`DA_WM_TP2_20_COMMON`, `mnid=0AJT`) | All entities, ≤1s hot-tier poll (OBSERVE accelerates when online) |
| Oven | NV7000BS-class (`TP1X_DA-KS-OVEN-0107X`, `mnid=0AJT`) | All entities; hot-tier poll covers door + operational state regardless of cloud reachability |

Other appliances on the same firmware family (washers, dishwashers, AC units) almost certainly speak the same protocol — the auth path and read primitives are common. You'd write one new descriptor in `samsung_appliance/appliances/`.

---

## How the app keeps in sync with the appliance

There are two parallel paths between the appliance and the app over the local CoAP-DTLS socket:

- **Push (OBSERVE).** When the appliance can reach Samsung's cloud, it emits a CoAP OBSERVE notification on the LAN socket within ~100ms of any state change — cycle start, door open, mode flip. The notification travels over the LAN; nothing about the push itself routes via Samsung. **But** the appliance's decision to emit it at all is gated inside its cloud-publish thread. Block the appliance from the internet and the LAN OBSERVE pushes stop, even though the LAN path itself is unaffected and the appliance still answers reads + accepts writes normally.
- **Polling.** The app always polls a small tier of hot resources (operational state, door, etc.) on a sub-second cadence, a warmer tier (mode, kidslock, alarms, …) every 15–30 s, and a full `/device/0` sweep every 5 minutes. This carries the UX regardless of whether OBSERVE is firing.

In normal operation both happen at once: an OBSERVE notification arrives first, the cache absorbs it, and the next-poll timer for that resource is reset. In an air-gapped LAN the app keeps working — only the worst-case freshness changes (from ~100 ms with push to ≤1 s on hot-tier resources via polling). Reads, writes, and HA entities behave identically.

Which path is doing the work is visible in Home Assistant. The bridge publishes per-appliance diagnostic entities including **Push Active** (on while OBSERVE is firing), **Last Update Source** (`observe` / `poll` / `sweep` / `optimistic`), **Last OBSERVE Age**, **Poll Max RTT**, **Slow Polls (window)**, **Poll Errors (window)**, and **Stalest Resource Age** — all under each device's Diagnostic section.

---

## Part 2 — Auth: get the identity cert

The bridge authenticates with a **client cert** signed by `AC14K_M`, an intermediate CA that has been public for years and remains in current firmware trust stores. The cert's Subject DN carries a UUID that the on-device ACL grants full access to.

You can read the UUID yourself out of the relevant server cert:

```sh
openssl s_client -connect <samsung-host>:443 -servername <samsung-host> \
                 -showcerts < /dev/null 2>/dev/null \
  | openssl x509 -noout -subject
# subject=C=KR, O=Samsung Electronics, OU=uuid:<UUID>, CN=*.samsungiotcloud.com
```

The UUID lives in `OU=uuid:<UUID>`. The server cert is currently valid through **2035-04-09**.

This README doesn't pin the literal UUID — the setup script extracts it live each run, so it self-updates if upstream rotates.

### Why this works

- Every Samsung Tizen/RT-OCF appliance has a **factory-baked ACE** in `/oic/sec/acl` granting this UUID `perm=31` on `href=*`.
- TizenRT iotivity derives peerId from `memmem(subject_dn, "uuid:")` — RDN-agnostic. A cert with the UUID in CN authenticates the same as one with it in OU.
- We don't need the matching private key from the original keyholder — we mint our own key and have `AC14K_M` sign our leaf. Different key, same identity, same access.

### One-command setup

```sh
pip install -r requirements-bootstrap.txt
TARGET_IP=$APPLIANCE_IP python setup_cert.py --test
```

What it does:

1. Fetches the AC14K_M signing CA + private key + upstream chain (RemoteAccessCA → CECA → ROOTCA) from a public mirror.
2. Fetches the relevant server cert and extracts the current UUID from its subject DN.
3. Sanity-checks that the AC14K_M cert and key actually pair (modulus match) before signing anything.
4. Generates a fresh RSA-2048 key pair you own.
5. Builds a CSR with the UUID in OU + CN + SAN and signs it with `AC14K_M` (SHA-1, matching the on-device trust hierarchy).
6. Concatenates `leaf + AC14K_M + 3 upstream CAs` into the fullchain PEM.
7. With `--test`: opens a DTLS handshake against `$TARGET_IP:$TARGET_PORT` (default `49154`) and GETs `/oic/sec/acl` — a `2.05` reply proves the cert authenticated (anonymous peers get `4.01`).

Output in `./certs/`: `client_fullchain.pem` + `client.key`.

Neither the UUID nor the AC14K_M bundle is hardcoded in this repo — both are fetched live each run, so the script self-updates if upstream rotates. If either fetch fails, the script prints an inline workaround: supply the UUID via `UUID=<uuid>` env, or supply the AC14K_M bundle via `AC14K_M_CERT_BUNDLE=/path/to/cert.pem`. `BRAYSTORM_URL=<mirror>` points at a different bundle source.

### How durable is this?

Rotating the published UUID would require Samsung to re-issue TLS certs across their IoT cloud, push new ACLs to every device in the field, and update the on-device daemon identity — a multi-quarter change with a long backwards-compat tail. `AC14K_M` has been public for years and is still in 2026 firmware trust stores. Local access via this path is roughly as durable as cloud control of these appliances.

> **Legacy path:** earlier versions used a per-hub-UUID cert via an anonymous `/oic/sec/doxm` read escalation. That still works on the dryer-family firmware but isn't necessary — the cert minted here authenticates against every appliance and survives device resets. The old `bootstrap.py` for the legacy flow was removed when the package was renamed; see git history if you need it.

---

## Part 3 — Configure your appliances

Copy `.env.example` to `.env` and fill in.

### Layered envs

The bridge config splits into:

- **Shared keys** (one per process): MQTT broker + creds, HA discovery prefix, cert paths, timer intervals.
- **Per-appliance keys** (one block per appliance) under `APPLIANCE_<n>_*` (1-indexed).

`APPLIANCE_COUNT` tells the bridge how many indexed blocks to read. Bump it as you add appliances.

```bash
APPLIANCE_COUNT=2

# Appliance 1 — dryer
APPLIANCE_1_CLASS=dryer
APPLIANCE_1_IP=192.168.1.100
APPLIANCE_1_OCF_PORT=             # blank → descriptor default (49155 for dryer)
APPLIANCE_1_TOPIC=samsung_dryer
APPLIANCE_1_NAME=Samsung Dryer

# Appliance 2 — oven
APPLIANCE_2_CLASS=oven
APPLIANCE_2_IP=192.168.1.101
APPLIANCE_2_OCF_PORT=             # blank → descriptor default (49154 for oven)
APPLIANCE_2_TOPIC=samsung_oven
APPLIANCE_2_NAME=Samsung Oven
```

Each `APPLIANCE_<n>_CLASS` must match a descriptor key in `samsung_appliance/appliances/__init__.py::DESCRIPTORS` — currently `dryer` and `oven`.

---

## Part 4 — Run it

### Docker (the real deployment)

```sh
docker compose up -d --build
docker compose logs -f
```

Container name `smartthings-local`. Outbound-only — no ports exposed. Needs egress to each appliance's IP/port (UDP) and to your MQTT broker. The certs in `./certs/` (or whatever `APPDATA_DIR` points to via the volume mount) are read-only mounted at `/config`.

### Deploying to a remote Linux host (Unraid, etc.)

```sh
# Once: upload the cert + key onto the remote.
ssh "$SSH_HOST" mkdir -p "$APPDATA_DIR"
scp certs/client_fullchain.pem certs/client.key "$SSH_HOST:$APPDATA_DIR/"

# Each deploy: ship source + .env, rebuild container on the host.
./deploy.sh
```

Set `SSH_HOST`, `REMOTE_DIR`, `APPDATA_DIR` in `.env`. `deploy.sh` extracts those three keys via `grep` rather than `source .env`, so values containing spaces (like `APPLIANCE_1_NAME=Samsung Dryer`) don't break it.

### Bare metal (first test / debugging)

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

### Expected first-run logs

```
14:08:42  INFO   samsung_appliance        SmartThings-Local Bridge starting (2 appliances)
14:08:42  INFO   samsung_appliance          broker = <broker-ip>:1883 (user=<mqtt-user>)
14:08:42  INFO   samsung_appliance          [1] dryer @ <dryer-ip>:49155 (DTLS) → topic samsung_dryer/*
14:08:42  INFO   samsung_appliance          [2] oven  @ <oven-ip>:49154 (DTLS) → topic samsung_oven/*
14:08:42  INFO   samsung_appliance        MQTT connected → <broker-ip>:1883
14:08:43  INFO   dryer                    DTLS connected — subscribing 11 paths
14:08:44  INFO   dryer.<dryer-serial>     identified — serial=…
14:08:44  INFO   dryer.<dryer-serial>     seeded → 25 links; sensors live
14:08:44  INFO   oven                     DTLS connected — subscribing 11 paths
14:08:46  INFO   oven.<oven-serial>       identified — serial=…
14:08:46  INFO   oven.<oven-serial>       seeded → 16 links; sensors live
```

In HA: **Settings → Devices & Services → MQTT** should show both devices populated.

---

## Per-appliance notes

### Dryer

| Capability | Works? | Notes |
|---|---|---|
| Read all state | ✅ | Machine state, job state, energy (W + kWh), course, dry level, completion time, remote control, child lock, alarms |
| Wrinkle Prevent toggle | ✅ | Persists |
| Start / Pause / Stop | ✅ | Via `/operational/state/vs/0`; needs Remote Control on |
| Change course | ✅ | Via `/st/dryercourse/vs/0`; needs Remote Control on. **Not exposed by the SmartThings cloud HA integration.** |
| Power on/off | ❌ | Accepted (2.04) but reverts within seconds — hardware-mirrored |
| Child Lock / Remote Control toggle | ❌ | Same — hardware-mirrored physical buttons |

The dryer's `/operational/state/vs/0` is on the bridge's hot poll tier (1s idle / 0.5s while a cycle is active) and also accepts OBSERVE registration. When the appliance has internet it pushes notifications within ~100ms of any state change and the cache absorbs them as fast freshness; when air-gapped the hot-tier poll carries the same UX with worst-case lag of one tier interval.

### Oven

| Capability | Works? | Notes |
|---|---|---|
| Read state | ✅ | Cavity state, current/target temp, door, mode, alarms, firmware-update-available |
| Lamp (light entity) | ✅ | Binary On/Off only — High/Low/Dim values are accepted (2.04) but silently coerced back. Works regardless of Remote Control. |
| Sound, Fast preheat | ⚠️ | Wired but untested RC-gated. |
| Setpoint slider | ⚠️ | Wired but untested RC-gated. |
| Mode select | ⚠️ | Wired but untested RC-gated. |
| Stop button | ✅ |  |
| **Kitchen timer (`⏲` icon)** | ❌ | **The oven's panel kitchen timer is not exposed via CoAP at all.** Confirmed by full `/device/0` dump — `UpperTimer*` fields in `/mode/vs/0` only populate when set via the API, not from the panel. |

**The oven doesn't push OBSERVE on `/mode/vs/0` writes** (the dryer does). The bridge handles this transparently because state freshness comes from polling rather than from OBSERVE:
1. **Optimistic publish** — the moment a POST returns 2.04, the bridge merges the write body into the cache and publishes to MQTT. HA reflects the new value instantly.
2. **Scheduler reconciliation** — the PollScheduler defers polling the just-written resource for ~4s (past Samsung's fetchback-revert window), then refreshes it on its tier cadence. If the device silently coerced the value, the corrected state is republished and HA reverts.
3. **Periodic `/device/0` sweep** — every 5 minutes the scheduler's sweep tier re-fetches the whole device tree, bounding worst-case drift on any resource the per-tier polls don't cover.

---

## Reference

### Config keys

| Key | Meaning |
|---|---|
| `APPLIANCE_COUNT` | Number of `APPLIANCE_<n>_*` blocks to read (1-indexed) |
| `APPLIANCE_<n>_CLASS` | Descriptor name: `dryer`, `oven` |
| `APPLIANCE_<n>_IP` | LAN IP of the appliance |
| `APPLIANCE_<n>_OCF_PORT` | Optional override (blank → descriptor default: dryer=49155, oven=49154) |
| `APPLIANCE_<n>_TOPIC` | MQTT topic prefix (also the HA device identifier — changing it re-keys the device) |
| `APPLIANCE_<n>_NAME` | Friendly name on the HA device card |
| `MQTT_BROKER` / `MQTT_PORT` / `MQTT_USER` / `MQTT_PASS` | Broker config |
| `HA_DISCOVERY_PREFIX` | HA discovery topic root (default `homeassistant`) |
| `CERT_PATH` / `KEY_PATH` | Override cert lookup (auto-detects `/config/` then `./certs/`) |
| `HEALTH_INTERVAL_S` | Seconds between `<prefix>/bridge/health` publishes (default 60) |
| `PING_INTERVAL_S` | CoAP empty-CON ping cadence; three consecutive failures publish `availability=offline` (default 25). Tier polling cadences are descriptor-declared, not env-tunable. |
| `SSH_HOST` / `REMOTE_DIR` / `APPDATA_DIR` | Used by `deploy.sh` only |

### MQTT topics — outgoing (bridge → broker)

Per appliance, where `<prefix>` is its `APPLIANCE_<n>_TOPIC`.

| Topic | Retain | When |
|---|---|---|
| `<prefix>/availability` | ✓ | `online` after seed; `offline` on disconnect (LWT for appliance #1) |
| `<prefix>/remote_available` | ✓ | `online` iff bridge is up AND Remote Control on the appliance is on. Gates the control entities. |
| `<prefix>/state` | ✓ | JSON sensor dict; published only when sensors actually diff |
| `<prefix>/bridge/health` | ✓ | Every `HEALTH_INTERVAL_S` — connect_count, error_count, notif_count, poll_count, poll_error_count, ping_count, ping_fail_count, reachable, last_change_age_s, last_seed_age_s, session_age_s, stalest_href, stalest_age_s, serial |
| `<ha_prefix>/{sensor,binary_sensor,switch,light,number,select,button}/<prefix>/.../config` | ✓ | HA MQTT discovery, republished on every MQTT (re)connect |

### MQTT topics — incoming (bridge subscribes)

`<prefix>/cmd/#`. **The MQTT user must have READ permission on this subtree** — without it the broker silently drops the TCP connection shortly after SUBSCRIBE. Check broker logs if writes never land.

Dryer:

| Suffix | Payloads | Effect |
|---|---|---|
| `cmd/wrinkle_prevent` | `On`, `Off` | POST `/washer/vs/0` |
| `cmd/operational_state` | `Run`, `Pause`, `Ready` | POST `/operational/state/vs/0` — requires RC |
| `cmd/dryer_mode` | Course name (e.g. `Cotton`) | Translated to `Course_HH` then POST `/st/dryercourse/vs/0` — requires RC |

Oven:

| Suffix | Payloads | Effect |
|---|---|---|
| `cmd/lamp` | `On`, `Off` | RMW of `/mode/vs/0 .options[UpperLamp_*]` |
| `cmd/sound` | `On`, `Off` | RMW of `/mode/vs/0 .options[Sound_*]` |
| `cmd/fastpreheat` | `On`, `Off` | RMW of `/mode/vs/0 .options[fastpreheat_*]` |
| `cmd/setpoint` | Integer °C (30–270, step 5) | RMW of `/temperatures/vs/0 .items[0].desired` — requires RC |
| `cmd/mode` | Mode name (e.g. `Convection`, `LargeGrill`) | POST `/mode/vs/0 {modes: [<name>]}` — requires RC |
| `cmd/stop` | (button press) | POST `/operational/state/vs/0 {state: Ready}` |

### Entity counts (approximate, per appliance)

| Type | Dryer | Oven |
|---|---|---|
| `sensor` | 17 | 17 |
| `binary_sensor` | 4 | 7 |
| `switch` | 1 (wrinkle) | 2 (sound, fastpreheat) |
| `light` | — | 1 (lamp) |
| `number` | — | 1 (setpoint slider) |
| `select` | 1 (course) | 1 (mode) |
| `button` | 3 (start/pause/stop) | 1 (stop) |

Gated control entities use HA's `availability_mode: all` against `<prefix>/availability` AND `<prefix>/remote_available`. Flip Remote Control on the appliance's front panel and those entities un-grey in HA.

### Repo layout

```
main.py                              Entry point — loads config, spawns one PushBridge per appliance
setup_cert.py                        One-shot cert minting script (live-fetches AC14K_M + UUID)
samsung_appliance/                   The bridge package
  __init__.py
  config.py                          SharedConfig + ApplianceConfig dataclasses
  logger.py                          Tagged logger helpers
  bridge.py                          PushBridge — one DTLS session per appliance, descriptor-driven
  coap_dtls.py                       DTLS-CoAP session: handshake, token-stable Block2 GET, POST, OBSERVE
  sensors.py                         /device/0 link-dict indexer (shared util)
  appliances/
    __init__.py                      DESCRIPTORS registry + get_descriptor()
    base.py                          ApplianceDescriptor dataclass + HA discovery helpers
    dryer.py                         Dryer descriptor (paths, flatten, discovery, commands)
    oven.py                          Oven descriptor
Dockerfile                           Container build (python:3.11-slim + 3 deps)
docker-compose.yml                   One service: smartthings-local
deploy.sh                            tar + ssh + docker compose up --build
.env.example                         Template — copy to .env, fill in
```

`certs/` is gitignored. Drop the privileged client cert + key there; the container mounts that directory read-only at `/config`.

---

## Adding a new appliance class

The bridge is appliance-agnostic. Adding e.g. a washer is mechanical:

1. Capture the appliance's `/device/0` to see what resources/fields it exposes. The setup script's `--test` mode is a good start; for the full dump use `local-tools/probe_oven_full_fetch.py` as a template.
2. Create `samsung_appliance/appliances/washer.py` with:
   - `OBSERVE_PATHS` — list of `[seg, …]` paths to subscribe to (only `/<x>/vs/0` resources push; the OCF-standard `/<x>/0` siblings register but never fire)
   - `flatten(links) -> dict` — map link dict to the flat sensor dict that goes on MQTT
   - `build_discovery(prefix, ha_prefix, name) -> [(topic, payload), …]` — HA discovery configs
   - `command_handlers() -> {suffix: fn(payload, links)}` — MQTT commands → `(path_segs, body_dict)`
   - A module-level `WASHER = ApplianceDescriptor(name='washer', default_observe_port=…, …)`
3. Add `WASHER` to `DESCRIPTORS` in `samsung_appliance/appliances/__init__.py`.
4. Add `APPLIANCE_<n>_CLASS=washer` to `.env`, bump `APPLIANCE_COUNT`, redeploy.

For a new appliance class also declare a `poll_tiers: list[PollTier]` and (optionally) an `is_active(links) -> bool` predicate on the descriptor. Hot-tier resources are whatever needs sub-second freshness for HA UX; warm covers everything else that's not static; the `/device/0` sweep tier catches anything you forgot. The descriptor pattern handles everything else — DTLS, MQTT, HA discovery, optimistic writes, Block2 reads, OBSERVE accelerator, reconnect, liveness pings.

---

## Traps to avoid

These each looked like obvious improvements at some point. Each one broke something.

- **Don't add OBSERVE subscriptions on OCF-standard `/<x>/0` paths.** They register successfully but never push. Use the Samsung `/<x>/vs/0` siblings (which do).
- **Don't assume OBSERVE silence means the appliance is broken.** When the appliance can't reach Samsung's cloud, its OBSERVE notify dispatch goes quiet even though the local DTLS session, GETs, POSTs, and the cache continue to work normally (measured at `~14 req/s` dryer / `~8 req/s` oven with 200/200 GETs successful while firewalled — see `local-tools/probe_poll_rate_combined.py`). The polling tiers are the structural answer to this; treat OBSERVE strictly as an optional accelerator.
- **Don't touch `/oic/sec/*` (doxm, pstat, cred, acl).** The bridge doesn't, and you shouldn't from helper scripts either — those resources have wedge/brick risk on Samsung's RT-OCF security stack. The bridge surfaces are strictly `/<x>/vs/0` and `/device/0`.
- **Don't run two clients against the same appliance simultaneously.** Samsung's RT-OCF DTLS allows one active session per peer; a second handshake will get the device to drop the new socket. If HA seems to flap, check whether you've got `main.py` running locally AND the Docker container up.
- **Don't expect parity from every write surface.** Samsung's firmware accepts a lot of writes with `2.04 Changed` but only some of them stick — power, child-lock, and remote-control writes are accepted-then-reverted because they're hardware-mirrored. The bridge's optimistic-publish-then-verify pattern handles this transparently: HA briefly shows the new value, the 3s fetch-back republishes the actual value, HA reverts.

---

## Known DTLS flakiness

Samsung's RT-OCF DTLS stack occasionally closes sessions actively — usually right after a Block2 GET or in the seconds after a POST. The bridge handles this with exponential reconnect (1s → 30s) and a re-seed on each new session. From HA's perspective the entity briefly goes offline then comes back; from the bridge's perspective you'll see lines like:

```
oven.…  DTLS recv: Unexpected EOF
oven.…  reconnect in 1s
oven.…  DTLS connected — subscribing 11 paths
oven.…  seeded → 16 links; sensors live
```

If reconnects become persistent (e.g. >10 in a minute) something's actually wrong — check the appliance's Wi-Fi link first, then look for a competing DTLS client on the LAN.

---

## Contributing

Patches welcome — especially:

- New appliance descriptors (washer, dishwasher, AC, fridge, etc.) on the same Tizen RT 3.x firmware family.
- Confirmation/refutation on additional dryer or oven models. `nmap` + `/device/0` dump + `/oic/d` GET is enough to know if you're on the same firmware family.
- A proper HA custom component wrapping the bridge so there's a config flow instead of YAML/env editing.

If you submit a PR, please don't include real device UUIDs, MACs, serials, IPs, or bearer tokens — use the placeholders from `.env.example`.
