# LocalThings

**A native Home Assistant custom integration for local control of newer-generation Samsung connected appliances.** No cloud round-trip. Add a device through HA's normal *Settings > Devices & Services* flow and it talks CoAP-over-DTLS straight to the appliance on your LAN.

This integration uses the [`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local) library to handle the low-level DTLS/CoAP communication with devices.

### What you get

Adding a device just needs a host IP and your CA credentials in the UI. The integration reads the appliance's identity and picks the matching capability registry on its own, so there's no per-model descriptor to write for a new unit of a type that's already supported.

Credential setup is one-time. The first device you add asks for a CA certificate and key (see Part 2); every device after that reuses the same stored CA and only asks for the host IP, minting its own per-device leaf cert automatically.

Your state stays on your LAN: HA talks to the appliance over a direct DTLS session, and Samsung's cloud sees nothing from this integration. (The appliance itself still maintains its own connection to Samsung; that's firmware behavior on the device side, not something this integration controls.)

### Supported appliance types

| Type | Registry |
|---|---|
| Air conditioner | `by_type/airconditioner.py` |
| Air purifier | `by_type/air_purifier.py` |
| Dryer | `by_type/dryer.py` |
| Oven | `by_type/oven.py` |
| Cooktop (read-only burner status) | `by_type/cooktop.py` |
| Range hood | `by_type/range_hood.py` |
| Range | `by_type/range.py` |
| Dishwasher | `by_type/dishwasher.py` |
| Refrigerator | `by_type/refrigerator.py` |
| Washer | `by_type/washer.py` |

Each registry composes shared and family-specific `Capability` objects from `registry/capabilities/`; those modules document the individual resources/entities in more depth than a README table can stay current with.

Other Tizen RT / DAWIT-family appliances almost certainly speak the same protocol underneath, since the auth path and CoAP primitives are shared across the fleet. Adding a new type means writing a new `by_type/<name>.py` registry file; it doesn't require reverse-engineering the protocol again. See **Adding a new appliance type** below.

---

## Part 1: Is your appliance compatible?

```sh
# UDP scan for DTLS-CoAP ports
nmap -Pn -sU -p 49152-49160 "$APPLIANCE_IP"
```

- Any UDP port in `49152-49160` open|filtered with a DTLS handshake responding: newer firmware (Tizen RT 3.x, DAWIT 3.0+). This is what the integration talks to. Most devices answer on `49154`/`49155`, but some builds bind lower (e.g. `49153`). The config flow sweeps the whole range and auto-detects the live port, so you don't need to know which one your device uses.
- Only `8888/tcp` open (token-based HTTPS): older firmware (roughly 2018-2022). **Not supported here.**

---

## Part 2: One-time setup, get the AC14K_M CA credentials

The config flow (Part 3) needs a **CA certificate and CA private key** to mint each device's leaf cert itself. Specifically, it needs the `AC14K_M` intermediate CA — a cert chain that's been public for years and still ships in current Samsung firmware trust stores. Every Samsung Tizen/RT-OCF appliance trusts identities chained to that CA with full access by default, so a cert signed by it is what lets HA talk to your appliance without Samsung's cloud in the loop. HA doesn't need the *device's* original cert or key, only something `AC14K_M` has signed, and it mints that itself once you give it the CA.

This repo doesn't include the needed CA bundle. For an example of how to obtain it, including fetching the AC14K_M cert and key and verifying they pair, see the `smartthings-local` protocol project's [`setup_cert.py`](https://github.com/QuiteYellow/SmartThings-Local/blob/main/setup_cert.py). However you obtain the CA cert and key, paste their PEM contents into the HA config flow's "CA Certificate (PEM)" and "CA Private Key (PEM)" fields in Part 3. You only need to do this once, since every appliance you add afterward reuses the same stored CA.

---

## Part 3: Add the integration in Home Assistant

1. Copy `custom_components/localthings/` into your HA config's `custom_components/` directory. (Or add this repo as a custom repository in HACS — `Integration` category — and install it from there.)
2. Restart HA.
3. **Settings > Devices & Services > Add Integration > LocalThings.**
4. First device: paste the appliance's IP, plus the contents of the CA private and public key from Part 2.
5. The flow fetches the current UUID from Samsung's cloud gateway, mints a leaf cert signed by your CA, sweeps the `49152-49160` range to find the live DTLS port, and confirms the device answers `/device/0`. On success it creates the config entry and detects the device type automatically.
6. Every subsequent device only asks for the host IP; the stored CA credentials are reused to mint that device's leaf cert.

Entities appear under one HA device per appliance, named `Samsung Appliance (<ip>)` initially. Rename freely: the config entry is keyed on the device's serial, not the name.

---

## Development

### Docker Compose dev environment

```sh
docker compose up -d --build
docker compose logs -f
```

The `Dockerfile` builds on the official `home-assistant/home-assistant:stable` image and pre-installs `smartthings-local`, so the dependency is present at container start instead of depending on HA's own runtime pip-install step. Re-run with `--build` whenever the pinned `smartthings-local` version changes.

`docker-compose.yml` sets `network_mode: host`, which is required since DTLS is UDP and won't traverse Docker's bridge NAT to reach LAN appliances, and bind-mounts `custom_components/localthings/` read-only into `ha_config/custom_components/`. Bump `custom_components.localthings` to `debug` in `ha_config/configuration.yaml` for verbose protocol logging.

### Tests

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install pytest-homeassistant-custom-component homeassistant
.venv/bin/pytest tests/ -q
```

A large suite covering registry composition, discovery, entity descriptors, and golden-file regression against captured device dumps. `requirements-dev.txt` pins `smartthings-local` the same way `manifest.json` does, so tests exercise the real published protocol layer rather than a vendored copy.

---

## Repo layout

```
custom_components/localthings/
  manifest.json         Requirements (incl. the smartthings-local PyPI dep), version, domain
  __init__.py            async_setup_entry / async_unload_entry
  config_flow.py          UUID fetch, leaf cert minting, port probing, config entry creation
  coordinator.py          Polling + push update coordination, stale-state fallback, write dispatch
  observe.py              CoAP OBSERVE (push-mode) support layered on the coordinator
  diagnostics.py           Redacted diagnostics download (device state + coverage metadata)
  const.py                 Domain, config keys, probe ports
  entity.py                Base entity wiring capability registry -> HA entity
  sensor.py / binary_sensor.py / switch.py / number.py / select.py / button.py / time.py / fan.py / climate.py
                            One module per HA platform
  strings.json / translations/   Config-flow copy + entity state translations
  registry/
    registry.py             Builds the global capability registry, validates href collisions
    capability.py           Capability dataclass (href, entities, transforms)
    entities.py             Per-platform entity descriptor dataclasses
    discovery.py            Binds a device's live resources to registered capabilities
    adapter.py               Flattens bound entities into HA-ready state
    identity.py              Reads device identity for type detection
    redact.py                 Strips account/identity data before diagnostics leave HA
    capabilities/             Shared + per-family Capability definitions (common, airconditioner,
                               cooktop, range_hood, dryer, oven, dishwasher, fridge, washer,
                               laundry, operational, ignored)
    by_type/                  One DeviceRegistry per appliance type, composed from capabilities/
tests/                    Registry composition, discovery, entity descriptors, coordinator/observe
                            behavior, and golden-file regression against captured device dumps
requirements-dev.txt        Test deps, including the smartthings-local package
docker-compose.yml / ha_config/   Local HA dev environment
```

---

## Reporting a capability gap

If your appliance's type isn't recognized, or it exposes resources this integration doesn't model yet, a Repairs
issue appears under Settings > System > Repairs pointing you at Settings > Devices & Services > this device >
the menu > Download diagnostics. That download is already redacted of account/network identifiers (Bixby login
email, access tokens, device IDs, MAC addresses, serial numbers) before it's generated, so it's safe to attach
directly to a new issue using the linked device-support template. This is the fastest way to help add or expand
support for hardware the maintainers don't have.

---

## Adding a new appliance type

1. Get a capture of the appliance's `/device/0` response. The easiest way: add the device to HA (type detection failing is fine) and pull its Diagnostics download from Settings > Devices & Services > the device > the menu > Download diagnostics — it already contains a redacted dump of the device's resources.
2. Reuse existing `Capability` objects from `registry/capabilities/` wherever the resource matches one already declared. Most `common.py` capabilities (power, kids lock, remote control, alarms, energy/water meters) are shared verbatim across families; add new ones only for resources unique to the new type.
3. Create `registry/by_type/<name>.py` with a `DeviceRegistry(name=..., capabilities=_build([...]))`. Use `pattern_capabilities` instead of `capabilities` for any resource whose `href` isn't fixed (for example per-compartment fridge resources); see `refrigerator.py` for the pattern.
4. Register it in `_REGISTRY_BY_KEY` in `registry/by_type/__init__.py`, keyed on the lowercased, space/hyphen-to-underscore-converted suffix of the device's `oneUiVersion` string (see `_type_key()` in that file for the exact transform). If the device never reports `oneUiVersion`, add its consumer-model prefix to `_CONSUMER_PREFIX_TO_KEY` so `for_device_by_model()` can route it. If it also omits `/information/vs/0` (as the verified NA9300K cooktop does), add a distinctive, conservative resource-signature rule to `for_device_by_resources()`.
5. Add golden-file coverage in `tests/` against a captured `/device/0` dump for the new type.

No config-flow changes are needed. Device-type detection and entity wiring are fully driven by the registry.

---

## Known device behavior

Samsung's firmware occasionally drops the DTLS session briefly — this is normal appliance-side behavior, not a bug. The integration reconnects automatically, and from HA's perspective a brief reconnect looks like an entity holding its last value for one poll cycle rather than going `unavailable`. When an appliance supports it, the integration prefers push-based updates (instant, via `observe.py`) over polling, falling back to polling otherwise.

If reconnects become persistent (more than a handful per minute), something's actually wrong. Check the appliance's Wi-Fi link first, then look for a competing DTLS client on the LAN — only one active session per appliance is allowed at a time.

---

## Contributing

Patches are welcome, especially:

- New `by_type/` registries for appliance families not yet covered (AC, microwave, etc.) on the same Tizen RT 3.x firmware family.
- Confirmation or refutation of compatibility on additional models within an already-supported type.
- Protocol-level fixes, which belong upstream in [`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local) rather than here. HA-side fixes (entities, config flow, coordinator, registry) belong in this repo.

If you submit a PR, please don't include real device UUIDs, MACs, serials, IPs, or CA private key material. Use the placeholders from the config-flow form instead.
