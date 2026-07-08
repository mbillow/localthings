# Local Things

**A native Home Assistant custom integration for local control of newer-generation Samsung connected appliances.** No cloud round-trip. Add a device through HA's normal *Settings > Devices & Services* flow and it talks CoAP-over-DTLS straight to the appliance on your LAN.

> ### Where things live
>
> This project split into two repos partway through development.
>
> - **[`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local)** (PyPI package): the reusable protocol layer. DTLS session handling, CoAP wire encoding, Block2 reads, bounded retry/retransmit, inter-request rate limiting, cert-chain validation. No HA dependency; usable from any Python project.
> - **This repo**: the Home Assistant integration built on top of it. Config flow, a per-device-type capability registry, the polling coordinator, and all the HA entity classes.
>
> `custom_components/localthings/manifest.json` pulls in `smartthings-local` from PyPI like any other HA integration dependency.

### What you get

Adding a device just needs a host IP and your CA credentials in the UI. The integration reads the appliance's `oneUiVersion` and picks the matching capability registry (dryer, oven, dishwasher, refrigerator) on its own, so there's no per-model descriptor to write for a new unit of a type that's already supported.

Credential setup is one-time. The first device you add asks for the AC14K_M CA cert and key (see Part 2); every device after that reuses the same stored CA and only asks for the host IP, minting its own per-device leaf cert automatically.

Your state stays on your LAN: HA talks to the appliance over a direct DTLS session, and Samsung's cloud sees nothing from this integration. (The appliance itself still maintains its own connection to Samsung; that's firmware behavior on the device side, not something this integration controls.)

### Supported appliance types

| Type | Registry | Notable capabilities |
|---|---|---|
| Dryer | `by_type/dryer.py` | Power, kids lock, remote control, alarms, energy meter, operational state, door LED, sound mode, dryer settings/course, job-beginning status, diagnosis, firmware-update sensor |
| Oven | `by_type/oven.py` | Power, kids lock, remote control, alarms, cavity state, setpoint, mode, operational state, door, connectivity, firmware-update sensor |
| Dishwasher | `by_type/dishwasher.py` | Power, kids lock, remote control, alarms, energy + water meters, water filter, operational state, cycle options/settings, door LED, sound mode/volume, firmware-update sensor |
| Refrigerator | `by_type/refrigerator.py` | Power, kids lock, remote control, alarms, energy meter, water filter, status lock, door alert, icemaker (nighttime + generic per-compartment), flex zone, refrigeration mode, autofill, welcome/cabinet lighting, Sabbath mode, beverage zone, plus pattern-matched per-compartment temperature/setpoint/icemaker/door capabilities for multi-cavity fridges, firmware-update sensor |

Other Tizen RT / DAWIT-family appliances almost certainly speak the same protocol underneath, since the auth path and CoAP primitives are shared across the fleet. Adding a new type means writing a new `by_type/<name>.py` registry file; it doesn't require reverse-engineering the protocol again. See **Adding a new appliance type** below.

---

## Part 1: Is your appliance compatible?

```sh
# UDP scan for DTLS-CoAP ports
nmap -Pn -sU -p 49152-49160 "$APPLIANCE_IP"
```

- `49154/udp` or `49155/udp` open|filtered with a DTLS handshake responding: newer firmware (Tizen RT 3.x, DAWIT 3.0+). This is what the integration talks to. The config flow probes both ports automatically, so you don't need to know which one your device uses.
- Only `8888/tcp` open (token-based HTTPS): older firmware (roughly 2018-2022). **Not supported here.**

---

## Part 2: One-time setup, get the AC14K_M CA credentials

The config flow (Part 3) needs a **CA certificate and CA private key** to mint each device's leaf cert itself. Specifically, it needs the `AC14K_M` intermediate CA: a cert chain that's been public for years and still ships in current Samsung firmware trust stores. It's required because every Samsung Tizen/RT-OCF appliance's factory ACL grants full CRUDN access (`perm=31` on `href=*`) to whatever identity is chained to that CA, so a cert signed by it is the one thing that lets HA talk to your appliance without Samsung's cloud in the loop. HA doesn't need the *device's* original cert or key, only something `AC14K_M` has signed, and it mints that itself once you give it the CA.

This repo doesn't include the needed CA bundle. For an example of how to obtain it, including fetching the AC14K_M cert and key and verifying they pair, see the `smartthings-local` protocol project's [`setup_cert.py`](https://github.com/QuiteYellow/SmartThings-Local/blob/main/setup_cert.py). However you obtain the CA cert and key, paste their PEM contents into the HA config flow's "CA Certificate (PEM)" and "CA Private Key (PEM)" fields in Part 3. You only need to do this once, since every appliance you add afterward reuses the same stored CA.

### Why this works

- Every Samsung Tizen/RT-OCF appliance has a factory-baked ACE in `/oic/sec/acl` granting the AC14K_M-chained identity `perm=31` on `href=*`.
- TizenRT iotivity derives the peer ID via `memmem(subject_dn, "uuid:")`, which is RDN-agnostic, so a cert with the UUID in any RDN authenticates the same way.
- You don't need the original keyholder's private key. The config flow mints its own key and has `AC14K_M` sign the leaf: different key, same identity, same access.

---

## Part 3: Add the integration in Home Assistant

1. Copy `custom_components/localthings/` into your HA config's `custom_components/` directory. (Or install via a custom HACS repository: no `hacs.json` is checked in yet, so add this repo as a custom integration repository in HACS if you use it that way.)
2. Restart HA.
3. **Settings > Devices & Services > Add Integration > Local Things.**
4. First device: paste the appliance's IP, plus the contents of the CA private and public key from Part 2.
5. The flow fetches the current UUID from Samsung's cloud gateway, mints a leaf cert signed by your CA, probes ports `49154`/`49155`, and confirms the device answers `/device/0`. On success it creates the config entry and detects the device type automatically.
6. Every subsequent device only asks for the host IP; the stored CA credentials are reused to mint that device's leaf cert.

Entities appear under one HA device per appliance, named `Samsung Appliance (<ip>)` initially. Rename freely: the config entry is keyed on the device's serial, not the name.

---

## Development

### Docker Compose dev environment

```sh
docker compose up -d --build
docker compose logs -f
```

The `Dockerfile` builds on the official `home-assistant/home-assistant:stable` image and pre-installs `smartthings-local`, so the dependency is present at container start instead of depending on HA's own runtime pip-install step (which needs outbound network access at exactly the moment the integration loads, and repeats on every container recreate). Re-run with `--build` whenever the pinned `smartthings-local` version changes.

`docker-compose.yml` sets `network_mode: host`, which is required since DTLS is UDP and won't traverse Docker's bridge NAT to reach LAN appliances, and bind-mounts `custom_components/localthings/` read-only into `ha_config/custom_components/`. Bump `custom_components.localthings` to `debug` in `ha_config/configuration.yaml` for verbose protocol logging.

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
requirements-dev.txt               Test deps, including the smartthings-local package
docker-compose.yml / ha_config/    Local HA dev environment
```

---

## Adding a new appliance type

1. Capture the appliance's `/device/0` response to see what resources/fields it exposes. An authenticated `DtlsCoapSession` from `smartthings_local.protocol.dtls_session` GET is enough; `local-tools/probe_device.py` wraps this.
2. Reuse existing `Capability` objects from `ocf/registry/capabilities/` wherever the resource matches one already declared. Most `common.py` capabilities (power, kids lock, remote control, alarms, energy/water meters) are shared verbatim across families; add new ones only for resources unique to the new type.
3. Create `ocf/registry/by_type/<name>.py` with a `DeviceRegistry(name=..., capabilities=_build([...]))`. Use `pattern_capabilities` instead of `capabilities` for any resource whose `href` isn't fixed (for example per-compartment fridge resources); see `refrigerator.py` for the pattern.
4. Register it in `_REGISTRY_BY_KEY` in `ocf/registry/by_type/__init__.py`, keyed on the lowercased, space/hyphen-to-underscore-converted suffix of the device's `oneUiVersion` string (see `_type_key()` in that file for the exact transform).
5. Add golden-file coverage in `tests/` against a captured `/device/0` dump for the new type.

No config-flow or coordinator changes are needed. Device-type detection and entity wiring are fully driven by the registry.

---

## Known DTLS behavior

Samsung's RT-OCF DTLS stack occasionally closes sessions actively, usually right after a Block2 GET or in the seconds after a POST. Retry/retransmit bounds and inter-request pacing live in the `smartthings-local` protocol layer (tuned against measured per-firmware request-rate ceilings); reconnect-with-backoff and stale-state fallback live in this repo's `coordinator.py`. From HA's perspective a brief reconnect looks like an entity holding its last value for one poll cycle rather than going `unavailable`.

If reconnects become persistent (more than a handful per minute), something's actually wrong. Check the appliance's Wi-Fi link first, then look for a competing DTLS client on the LAN: Samsung's RT-OCF DTLS allows only one active session per peer.

---

## Contributing

Patches are welcome, especially:

- New `by_type/` registries for appliance families not yet covered (washer, AC, microwave, etc.) on the same Tizen RT 3.x firmware family.
- Confirmation or refutation of compatibility on additional models within an already-supported type.
- Protocol-level fixes, which belong upstream in [`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local) rather than here. HA-side fixes (entities, config flow, coordinator, registry) belong in this repo.

If you submit a PR, please don't include real device UUIDs, MACs, serials, IPs, or CA private key material. Use the placeholders from the config-flow form instead.
