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
- **Sub-second push for state changes.** Cycle starts, pauses, ends, course changes, door opens, lamp toggles — Home Assistant reflects it within ~1 second on appliances that push OBSERVE notifications, or after the 3-second post-POST verify on appliances that don't.
- **Writes that work**: dryer Start/Pause/Stop, course selection, wrinkle prevent; oven lamp (light entity), sound, fast preheat, setpoint slider, mode select, stop.
- **Optimistic publish + verify**: HA sees the new value the instant the device 2.04-confirms the write; a Block2 fetch-back 3 seconds later corrects if the device silently coerced or rejected.
- **HA Energy Dashboard ready** (dryer): live watts + cumulative kWh as `total_increasing`.
- **Bridge logs tagged per-appliance** with `<class>.<serial>` once each device's serial is read on connect — `dryer.<serial>` vs `oven.<serial>` interleaved in the same log stream, easy to grep.
- **Zero HA YAML.** Every entity is auto-discovered via MQTT discovery.
- **Your state stays on your LAN.** Bridge → broker → HA. Samsung's cloud sees nothing from HA. *(The appliance still maintains its own TLS session to Samsung — appliance design, not ours.)*

### Under the hood

Each appliance runs an independent push-mode bridge: one sustained DTLS session, CoAP OBSERVE (RFC 7641) on ~11 of the appliance's `/<x>/vs/0` resources, token-stable Block2 (RFC 7959) for the multi-block reads, optimistic state publish + Block2 fetch-back verification after every write. Reconnect with exponential backoff on session errors.

Authentication uses **Samsung's publicly-published cloud-bridge identity** (UUID `ab0b0ac4-…`), present in every Samsung Tizen/RT-OCF appliance's factory ACL with `perm=31` (full CRUDN) on `href=*`. One cert chain works across the whole fleet. Setup is one Python script.

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
| Dryer | DV5000T (`DA_WM_TP2_20_COMMON`, `mnid=0AJT`) | All entities, sub-second OBSERVE push |
| Oven | NV7000BS-class (`TP1X_DA-KS-OVEN-0107X`, `mnid=0AJT`) | All entities; OBSERVE-push lazy on options-array writes (see "Per-appliance notes" below) |

Other appliances on the same firmware family (washers, dishwashers, AC units) almost certainly speak the same protocol — the auth path and read primitives are common. You'd write one new descriptor in `samsung_appliance/appliances/`.

---

## Part 2 — Auth: get the cloud-identity cert

The bridge authenticates with a **client cert** signed by `AC14K_M` (Samsung's leaked diagnostic intermediate CA — used inside Samsung tooling and still trusted by current firmware). The cert's Subject DN contains the cloud-bridge UUID Samsung publishes on its wildcard cloud TLS cert at `*.samsungiotcloud.com`.

You can verify the UUID yourself with one OpenSSL command:

```sh
openssl s_client -connect connect-v2.samsungiotcloud.com:443 \
                 -servername connect-v2.samsungiotcloud.com \
                 -showcerts < /dev/null 2>/dev/null \
  | openssl x509 -noout -subject
# subject=C=KR, O=Samsung Electronics, OU=uuid:<UUID>, CN=*.samsungiotcloud.com
```

The UUID lives in `OU=uuid:<UUID>`. Samsung's cert is valid through **2035-04-09**.

This README deliberately doesn't pin the literal UUID — the setup script extracts it live each run, so it self-updates if Samsung ever rotates.

### Why this works

- Every Samsung Tizen/RT-OCF appliance has a **factory-baked ACE** in `/oic/sec/acl` granting this UUID `perm=31` on `href=*`. It's the identity Samsung's own cloud-bridge daemon uses when forwarding cloud-issued commands to the on-device OCF stack.
- TizenRT iotivity derives peerId from `memmem(subject_dn, "uuid:")` — RDN-agnostic. A cert with the UUID in CN authenticates the same as one with it in OU.
- We don't have Samsung's matching private key (HSM-bound on their cloud) but we don't need it — we mint our own key and have `AC14K_M` sign our leaf. Different key, same identity, same access.

### One-command setup

You need `AC14K_M.pem`, its key, and the three upstream chain certs (`cert_1.pem`…`cert_4.pem`). These are published in [cicciovo/homebridge-samsung-airconditioner](https://github.com/cicciovo/homebridge-samsung-airconditioner). Drop them into `./certs/`.

```sh
AC14K_M_CERT=./certs/ac14k_m.pem \
AC14K_M_KEY=./certs/ac14k_m.key \
CHAIN_DIR=./certs/ \
OUT_DIR=./certs/ \
TARGET_IP=$APPLIANCE_IP TARGET_PORT=49154 \
python local-tools/setup_samsung_cloud_cert.py --test
```

What it does:

1. **Live-fetches** Samsung's wildcard cloud cert and extracts the current cloud-bridge UUID.
2. Generates a fresh RSA-2048 key pair you own.
3. Builds a CSR with the UUID in OU + CN + SAN, signs it with `AC14K_M` (SHA-1).
4. Concatenates `leaf + AC14K_M + 3 upstream CAs` into `fullchain.pem`.
5. With `--test`: opens a DTLS handshake against `$TARGET_IP:$TARGET_PORT` and GETs `/oic/sec/acl` — a `2.05` reply proves the cert authenticated as the cloud-identity peer (anonymous peers get `4.01` on that resource).

Output: `ab0b0ac4_fullchain.pem` + `ab0b0ac4.key` (filename matches the UUID prefix as a convention; the actual UUID is whatever was published live). Drop them in `./certs/`.

The UUID is **not hardcoded** anywhere in the script or this README. If the live fetch fails (restricted network), `UUID=<uuid> python setup_samsung_cloud_cert.py …` lets you supply it manually; the docstring documents the openssl-extract one-liner.

### How durable is this?

Rotating the cloud-bridge UUID is roughly equivalent to Samsung re-issuing TLS certs across their entire IoT cloud AND pushing new ACLs to every device in the field AND updating the on-device cloud-bridge daemon's identity — a multi-quarter project with a months-long backwards-compat window. The `AC14K_M` signing CA has been publicly leaked for years and still appears in 2026 firmware trust stores. Our access is roughly as durable as SmartThings cloud control of these appliances.

> **Legacy path:** earlier versions of this project used a per-hub-UUID cert via an anonymous `/oic/sec/doxm` read escalation. That still works on the dryer-family firmware but isn't necessary — the ab0b0ac4 cert is one identity that authenticates against every appliance, factory ACL, and survives device resets. `bootstrap.py` in the repo automates the legacy path if you'd rather; otherwise ignore it.

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
scp certs/ab0b0ac4_fullchain.pem certs/ab0b0ac4.key "$SSH_HOST:$APPDATA_DIR/"

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

The dryer pushes OBSERVE notifications on every state-changing write within ~100ms. State propagation is sub-second.

### Oven

| Capability | Works? | Notes |
|---|---|---|
| Read state | ✅ | Cavity state, current/target temp, door, mode, alarms, firmware-update-available |
| Lamp (light entity) | ✅ | Binary On/Off only — High/Low/Dim values are accepted (2.04) but silently coerced back. Works regardless of Remote Control. |
| Sound, Fast preheat | ⚠️ | Wired but untested write-side; RC-gated as a safety. |
| Setpoint slider | ⚠️ | Wired but untested mid-cook behaviour. RC-gated. |
| Mode select | ⚠️ | Wired but untested mid-cook behaviour. RC-gated. |
| Stop button | ⚠️ | Wired but untested. **Not** RC-gated (the SmartThings app stops without Remote Control on, so we don't gate either). |
| Power on/off as a switch | ❌ | Not exposed as a writeable entity — cold-start panel is a physical action. Read-only sensor only. |
| **Kitchen timer (`⏲` icon)** | ❌ | **The oven's panel kitchen timer is not exposed via CoAP at all.** Confirmed by full `/device/0` dump — `UpperTimer*` fields in `/mode/vs/0` only populate when set via the API, not from the panel. |

**The oven doesn't push OBSERVE on `/mode/vs/0` writes** (the dryer does). The bridge defends with:
1. **Optimistic publish** — the moment a POST returns 2.04, the bridge merges the write body into the local state and publishes to MQTT. HA reflects the new value instantly.
2. **Fetch-back verification** — 3 seconds later, the bridge does a token-stable Block2 GET of the just-written resource. If the device's actual state differs from optimistic (silently coerced), the corrected state is republished and HA reverts.
3. **Periodic heartbeat** — every `HEARTBEAT_INTERVAL_S` (default 600s), the bridge re-fetches `/device/0` and refreshes ALL resources (including observed ones), bounding worst-case drift.

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
| `HEARTBEAT_INTERVAL_S` | Seconds between full `/device/0` re-seeds; `0` disables (default 600) |
| `SSH_HOST` / `REMOTE_DIR` / `APPDATA_DIR` | Used by `deploy.sh` only |

### MQTT topics — outgoing (bridge → broker)

Per appliance, where `<prefix>` is its `APPLIANCE_<n>_TOPIC`.

| Topic | Retain | When |
|---|---|---|
| `<prefix>/availability` | ✓ | `online` after seed; `offline` on disconnect (LWT for appliance #1) |
| `<prefix>/remote_available` | ✓ | `online` iff bridge is up AND Remote Control on the appliance is on. Gates the control entities. |
| `<prefix>/state` | ✓ | JSON sensor dict; published only when sensors actually diff |
| `<prefix>/bridge/health` | ✓ | Every `HEALTH_INTERVAL_S` — connect_count, error_count, notif_count, last_change_age_s, session_age_s, serial |
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
local-tools/                         Research/probes — gitignored
  setup_samsung_cloud_cert.py        One-shot cert minting script
  probe_oven_*.py                    DTLS probes for the oven (lamp, OBSERVE, full /device/0 fetch)
  comparisons/                       Per-appliance /device/0 dumps + diff
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

The descriptor pattern handles everything else — DTLS, MQTT, HA discovery, optimistic+verify writes, Block2 reads, OBSERVE notifications, reconnect, periodic heartbeat.

---

## Traps to avoid

These each looked like obvious improvements at some point. Each one broke something.

- **Don't add OBSERVE subscriptions on OCF-standard `/<x>/0` paths.** They register successfully but never push. Use the Samsung `/<x>/vs/0` siblings (which do).
- **Don't half-block the cloud.** Either let the appliance reach Samsung normally (rock-solid local session, sub-second push) or fully block it (the local session tears down every ~30s; bridge reconnects). Don't sinkhole DNS while letting IPs resolve to unreachable hosts — the appliance holds a stable local session but stops emitting OBSERVE pushes entirely. Worst of both worlds.
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
