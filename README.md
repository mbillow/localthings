# Local Things

**A native Home Assistant custom integration for local control of newer-generation Samsung connected appliances.** No cloud round-trip, no MQTT broker, no separate bridge process — add a device through HA's normal *Settings → Devices & Services* flow and it talks CoAP-over-DTLS straight to the appliance on your LAN.

> ### Where things live
>
> This project split into two repos partway through development:
>
> - **[`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local)** (PyPI package) — the reusable protocol layer: DTLS session handling, CoAP wire encoding, Block2 reads, bounded retry/retransmit, inter-request rate limiting, cert-chain validation. No HA dependency; usable from any Python project.
> - **This repo** — the Home Assistant integration built on top of it: config flow, a per-device-type capability registry, the polling coordinator, and all the HA entity classes.
>
> `custom_components/localthings/manifest.json` pulls in `smartthings-local` from PyPI like any other HA integration dependency.

### What you get

- **Auto-detected device type.** Add a host + your CA credentials in the UI; the integration reads the appliance's `oneUiVersion` and picks the matching capability registry (dryer, oven, dishwasher, refrigerator) itself. No per-model descriptor to write for a new unit of a type that's already supported.
- **One-time credential setup, then it's just "add integration."** The first device you add asks for the AC14K_M CA cert + key (see Part 2). Every device after that reuses the same stored CA credentials — the config flow mints a fresh per-device leaf cert automatically and only asks for the host IP.
- **Standard HA entities**, not a hand-rolled dashboard: `sensor`, `binary_sensor`, `switch`, `number`, `select`, `button`, and `time` platforms, all backed by a single `DataUpdateCoordinator`-driven poller per device.
- **Capability registry, not a monolith.** Each appliance family's resources (power, kids lock, remote control, alarms, energy/water meters, cycle state, per-family settings) are declared once as `Capability` objects keyed on the stable OCF resource `href`, verified against live device dumps.
- **Stale-state resilience.** A failed poll doesn't blank out entities — the coordinator falls back to the last-known state rather than flapping devices to `unavailable`.
- **Your state stays on your LAN.** HA ↔ appliance is a direct DTLS session; Samsung's cloud sees nothing from this integration. *(The appliance still maintains its own connection to Samsung — that's appliance firmware behavior, not something this integration does or can prevent.)*

### Supported appliance types

| Type | Registry | Notable capabilities |
|---|---|---|
| Dryer | `by_type/dryer.py` | Power, kids lock, remote control, alarms, energy meter, operational state, door LED, sound mode, dryer settings/course, job-beginning status, diagnosis, firmware-update sensor |
| Oven | `by_type/oven.py` | Power, kids lock, remote control, alarms, cavity state, setpoint, mode, operational state, door, connectivity, firmware-update sensor |
| Dishwasher | `by_type/dishwasher.py` | Power, kids lock, remote control, alarms, energy + water meters, water filter, operational state, cycle options/settings, door LED, sound mode/volume, firmware-update sensor |
| Refrigerator | `by_type/refrigerator.py` | Power, kids lock, remote control, alarms, energy meter, water filter, status lock, door alert, icemaker (nighttime + generic per-compartment), flex zone, refrigeration mode, autofill, welcome/cabinet lighting, Sabbath mode, beverage zone, plus pattern-matched per-compartment temperature/setpoint/icemaker/door capabilities for multi-cavity fridges, firmware-update sensor |

Other Tizen RT / DAWIT-family appliances almost certainly speak the same protocol underneath (the auth path and CoAP primitives are shared across the fleet) — adding a new type is a new `by_type/<name>.py` registry file, not a protocol reverse-engineering project. See **Adding a new appliance type** below.

---

## Part 1 — Is your appliance compatible?

```sh
# UDP scan for DTLS-CoAP ports
nmap -Pn -sU -p 49152-49160 "$APPLIANCE_IP"
```

- **`49154/udp` or `49155/udp` open|filtered with a DTLS handshake responding** → newer firmware (Tizen RT 3.x, DAWIT 3.0+). This is what the integration talks to. The config flow probes both ports automatically — you don't need to know which one your device uses.
- **Only `8888/tcp` open (token-based HTTPS)** → older firmware (~2018–2022). **Not supported here.**

---

## Part 2 — One-time: get the AC14K_M CA credentials

The integration authenticates with a client cert chained to `AC14K_M`, an intermediate CA that's been public for years and remains in current firmware trust stores. Every Samsung Tizen/RT-OCF appliance's factory ACL grants the identity in that chain full CRUDN access, so the same CA can mint a working cert for any appliance on your LAN — HA does the per-device minting itself once you give it the CA.

```sh
pip install -r requirements-bootstrap.txt
python setup_cert.py
```

This fetches the AC14K_M CA cert + key + upstream chain from a public mirror and writes them to `./certs/ac14k_m.pem` and `./certs/ac14k_m.key`. Nothing device-specific happens at this step — no IP, no handshake needed. (Pass `--test` with `TARGET_IP=<appliance-ip>` set if you want to sanity-check connectivity against a real device before touching HA at all.)

If the live fetch fails, the script prints an inline workaround: supply `AC14K_M_CERT_BUNDLE=/path/to/cert.pem`, or point `BRAYSTORM_URL=<mirror>` at an alternate source.

### Why this works

- Every Samsung Tizen/RT-OCF appliance has a factory-baked ACE in `/oic/sec/acl` granting the AC14K_M-chained identity `perm=31` on `href=*`.
- TizenRT iotivity derives the peer ID via `memmem(subject_dn, "uuid:")` — RDN-agnostic, so a cert with the UUID in any RDN authenticates the same way.
- You don't need the original keyholder's private key — the config flow mints its own key and has `AC14K_M` sign the leaf. Different key, same identity, same access.

---

## Part 3 — Add the integration in Home Assistant

1. Copy `custom_components/localthings/` into your HA config's `custom_components/` directory (or install via a custom HACS repository — no `hacs.json` is checked in yet, so add this repo as a custom integration repository in HACS if you use it that way).
2. Restart HA.
3. **Settings → Devices & Services → Add Integration → Local Things.**
4. First device: paste the appliance's IP, plus the contents of `certs/ac14k_m.pem` and `certs/ac14k_m.key` from Part 2.
5. The flow fetches the current UUID from Samsung's cloud gateway, mints a leaf cert signed by your CA, probes ports `49154`/`49155`, and confirms the device answers `/device/0`. On success it creates the config entry and detects the device type automatically.
6. Every subsequent device only asks for the host IP — the stored CA credentials are reused to mint that device's leaf cert.

Entities appear under one HA device per appliance, named `Samsung Appliance (<ip>)` initially (rename freely — the config entry is keyed on the device's serial, not the name).

---

## Development

### Docker Compose dev environment

```sh
docker compose up -d
docker compose logs -f
```

Runs the official `home-assistant/home-assistant:stable` image with `network_mode: host` (required — DTLS is UDP and won't traverse Docker's bridge NAT to reach LAN appliances) and `custom_components/localthings/` bind-mounted read-only into `ha_config/custom_components/`. Bump `custom_components.localthings` to `debug` in `ha_config/configuration.yaml` for verbose protocol logging.

### Tests

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install pytest-homeassistant-custom-component homeassistant
.venv/bin/pytest tests/ -q
```

`requirements-dev.txt` pins `smartthings-local` the same way `manifest.json` does, so tests exercise the real published protocol layer rather than a vendored copy.

---

## Repo layout

```
custom_components/localthings/
  manifest.json                  Requirements (incl. the smartthings-local PyPI dep), version, domain
  __init__.py                    async_setup_entry / async_unload_entry
  config_flow.py                 UUID fetch, leaf cert minting, port probing, config entry creation
  coordinator.py                 DataUpdateCoordinator: polling, stale-state fallback, write dispatch
  const.py                       Domain, config keys, probe ports
  entity.py                      Base entity wiring capability registry -> HA entity
  sensor.py / binary_sensor.py / switch.py / number.py / select.py / button.py / time.py
                                  One module per HA platform
  strings.json / translations/   Config-flow copy + entity state translations
  ocf/
    batch.py                     /device/0 batch response parsing
    registry/
      capability.py              Capability dataclass (href, entities, transforms)
      entities.py                Per-platform entity descriptor dataclasses
      discovery.py                Binds a device's live resources to registered capabilities
      adapter.py                  Flattens bound entities into HA-ready state
      identity.py                 Reads device identity (serial, oneUiVersion) for type detection
      capabilities/               Shared + per-family Capability definitions (common, dryer, oven,
                                   dishwasher, fridge, laundry, operational)
      by_type/                    One DeviceRegistry per appliance type, composed from capabilities/
tests/                            80+ tests: registry composition, discovery, entity descriptors,
                                   golden-file regression against captured device dumps
setup_cert.py                     One-shot AC14K_M CA bundle fetcher (Part 2)
requirements-bootstrap.txt         Deps for setup_cert.py only
requirements-dev.txt               Test deps, including the smartthings-local package
docker-compose.yml / ha_config/    Local HA dev environment
```

---

## Adding a new appliance type

1. Capture the appliance's `/device/0` response to see what resources/fields it exposes — an authenticated `DtlsCoapSession` from `smartthings_local.protocol.dtls_session` GET is enough; `local-tools/probe_device.py` wraps this.
2. Reuse existing `Capability` objects from `ocf/registry/capabilities/` wherever the resource matches one already declared (most `common.py` capabilities — power, kids lock, remote control, alarms, energy/water meters — are shared verbatim across families); add new ones only for resources unique to the new type.
3. Create `ocf/registry/by_type/<name>.py` with a `DeviceRegistry(name=..., capabilities=_build([...]))`. Use `pattern_capabilities` instead of `capabilities` for any resource whose `href` isn't fixed (e.g. per-compartment fridge resources) — see `refrigerator.py` for the pattern.
4. Register it in `_REGISTRY_BY_KEY` in `ocf/registry/by_type/__init__.py`, keyed on the lowercased, space/hyphen-to-underscore-converted suffix of the device's `oneUiVersion` string (see `_type_key()` in that file for the exact transform).
5. Add golden-file coverage in `tests/` against a captured `/device/0` dump for the new type.

No config-flow or coordinator changes needed — device-type detection and entity wiring are fully driven by the registry.

---

## Known DTLS behavior

Samsung's RT-OCF DTLS stack occasionally closes sessions actively, usually right after a Block2 GET or in the seconds after a POST. Retry/retransmit bounds and inter-request pacing live in the `smartthings-local` protocol layer (tuned against measured per-firmware request-rate ceilings); reconnect-with-backoff and stale-state fallback live in this repo's `coordinator.py`. From HA's perspective a brief reconnect looks like an entity holding its last value for one poll cycle rather than going `unavailable`.

If reconnects become persistent (e.g. more than a handful per minute) something's actually wrong — check the appliance's Wi-Fi link first, then look for a competing DTLS client on the LAN (Samsung's RT-OCF DTLS allows only one active session per peer).

---

## Contributing

Patches welcome — especially:

- New `by_type/` registries for appliance families not yet covered (washer, AC, microwave, etc.) on the same Tizen RT 3.x firmware family.
- Confirmation/refutation on additional models within an already-supported type.
- Protocol-level fixes belong upstream in [`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local); HA-side fixes (entities, config flow, coordinator, registry) belong here.

If you submit a PR, please don't include real device UUIDs, MACs, serials, IPs, or CA private key material — use the placeholders from the config-flow form.
