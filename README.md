<!-- dark mode -->
<img src="custom_components/localthings/brand/dark_logo@2x.png#gh-dark-mode-only" alt="LocalThings Logo"/>
<!-- light mode -->
<img src="custom_components/localthings/brand/logo@2x.png#gh-light-mode-only" alt="LocalThings Logo"/>

<p align="center">
  <img alt="GitHub Repo stars" src="https://img.shields.io/github/stars/mbillow/localthings" />
  <img alt="GitHub watchers" src="https://img.shields.io/github/watchers/mbillow/localthings" />
</p>

<p align="center">
  <img alt="GitHub Release" src="https://img.shields.io/github/v/release/mbillow/localthings" />
  <img alt="hacs validation" src="https://img.shields.io/github/check-runs/mbillow/localthings/main?nameFilter=HACS%20validation&label=hacs%20validation" />
  <img alt="hassfest" src="https://img.shields.io/github/check-runs/mbillow/localthings/main?nameFilter=Hassfest%20validation&label=hassfest" />
  <img alt="tests" src="https://img.shields.io/github/check-runs/mbillow/localthings/main?nameFilter=Pytest&label=tests" />

</p>

# LocalThings

**A native Home Assistant custom integration for local control of newer-generation Samsung connected appliances.** No cloud round-trip. Add a device through HA's normal *Settings > Devices & Services* flow and it talks CoAP-over-DTLS straight to the appliance on your LAN.

This integration uses the [`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local) library to handle the low-level DTLS/CoAP communication with devices.

### What you get

Adding a device normally just needs a host IP and your CA credentials in the UI. The integration reads the appliance's identity and picks the matching capability registry on its own, so there's no per-model descriptor to write for a new unit of a type that's already supported. For the uncommon case where one physical host exposes several logical OCF devices, the advanced setup path can bind endpoint selection to one exact OCF device UUID.

Credential setup is one-time. The first device you add asks for a CA certificate and key (see Part 2); every device after that reuses the same stored CA and only asks for the host IP, minting its own per-device leaf cert automatically.

Your state stays on your LAN: HA talks to the appliance over a direct DTLS session, and Samsung's cloud sees nothing from this integration. (The appliance itself still maintains its own connection to Samsung; that's firmware behavior on the device side, not something this integration controls.)

### Supported appliance types

| Type | Registry |
|---|---|
| Air conditioner | `by_type/airconditioner.py` |
| Air purifier | `by_type/air_purifier.py` |
| Dehumidifier | `by_type/dehumidifier.py` |
| Dryer | `by_type/dryer.py` |
| Oven | `by_type/oven.py` |
| Microwave | `by_type/microwave.py` |
| Gas cooktop (read-only burner status) | `by_type/cooktop.py` |
| Range hood | `by_type/range_hood.py` |
| Range | `by_type/range.py` |
| Dishwasher | `by_type/dishwasher.py` |
| Refrigerator | `by_type/refrigerator.py` |
| Washer | `by_type/washer.py` |
| Water purifier | `by_type/water_purifier.py` |
| Vacuum clean/auto-empty station | `by_type/vacuum_station.py` |
| Air dresser | `by_type/air_dresser.py` |

Each registry composes shared and family-specific `Capability` objects from `registry/capabilities/`; those modules document the individual resources/entities in more depth than a README table can stay current with.

Other Tizen RT / DAWIT-family appliances almost certainly speak the same protocol underneath, since the auth path and CoAP primitives are shared across the fleet. Adding a new type means writing a new `by_type/<name>.py` registry file; it doesn't require reverse-engineering the protocol again. See **Adding a new appliance type** below.

---

## Part 1: Is your appliance compatible?

```sh
# UDP scan for DTLS-CoAP ports
nmap -Pn -sU -p 49152-49160 "$APPLIANCE_IP"
```

- Any UDP port in `49152-49160` open|filtered with a DTLS handshake responding: newer firmware (Tizen RT 3.x, DAWIT 3.0+). This is what the integration talks to. Most devices answer on `49154`/`49155`, but some builds bind lower (e.g. `49153`). The config flow probes the whole range and auto-detects the live port, so you don't need to know which one your device uses.
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
4. First device: paste the appliance's IP, plus the contents of the CA private and public key from Part 2. Leave **Exact OCF device UUID (advanced)** blank for normal setup.
5. With the advanced field left blank, the flow sends a DTLS `ClientHello` to every port in the `49152-49160` range at once and keeps the one that answers -- a real DTLS server identifies itself in about one round trip, and the probe stops there, so nothing is left behind on the appliance. Only that port is then given a real certificate handshake: it fetches the current UUID from Samsung's cloud gateway, mints a leaf cert signed by your CA, and reads the device's identity and `/device/0`. On success it creates the config entry, already knowing the appliance's serial, model, and type. Supplying an exact OCF device UUID uses the identity-bound path below instead and never scans or falls back to the fixed port band.
6. Every subsequent device only asks for the host IP. The stored CA credentials are reused, and so is the leaf cert itself -- every appliance accepts the same one -- so adding a second appliance doesn't depend on Samsung's cloud being reachable at all. If a device rejects the reused cert (the UUID behind it does rotate), the flow mints a fresh one and retries by itself.

For the uncommon case where one IP exposes several logical OCF devices, the advanced field accepts the exact OCF device UUID (`di`). This is the UUID reported by the target's authenticated OCF `/oic/d` resource, not the SmartThings platform device ID or a Samsung cloud certificate UUID. Use this path only when that exact value is already available from authenticated OCF diagnostics; LocalThings does not derive it from SmartThings metadata. The flow runs identity-aware multicast discovery on Home Assistant's route-selected IPv4 interface, accepts only a stable exact-`di` advertisement from the submitted IP, and statelessly probes only the advertised secure ports. After authentication it reads `/oic/d` and requires the same exact UUID before it requests `/device/0` or creates an entry. It never falls back to `pi`, a serial number, the host, or the normal fixed port band on this explicit path.

This advanced field changes setup-time endpoint selection and identity verification only. It does not acquire credentials, perform ownership transfer, add reconnect-time rediscovery, or make an appliance compatible with the certificate authentication described in Part 2 if that appliance requires a different authentication method.

Entities appear under one HA device per appliance, named for the appliance's type and model. Rename freely: the device is keyed on the appliance's own OCF device ID, not its name. (Some Samsung models ship the same serial number on every unit of a model, so the serial can't tell two of them apart -- the OCF device ID can.)

---

## Part 4: Per-device settings

Each device has its own **Configure** option in Settings > Devices & Services, under **Device settings**:

- **Allow writes even when remote control is reported off** — by default, LocalThings blocks every write with a clear error whenever a device reports remote control off, rather than letting the device silently reject it. Some devices accept certain writes anyway (e.g. default detergent/softener dosing on a washer) even while reporting remote control off. Only enable this if you've confirmed writes actually work on your device with remote control off — otherwise you trade a clear error for a silent failure.
- **Estimated finish -- minimum change (minutes)** — a washer/dryer/dishwasher's `finish_time` sensor is recomputed from the device's own remaining-time estimate on every poll, which commonly drifts or gets revised by a minute or two between updates. This setting holds `finish_time` at its last reported value until a new estimate differs by at least this many minutes, cutting down on Home Assistant history/logbook noise from a value that hasn't meaningfully changed. Defaults to `3`; set it to `0` to report every computed change.
- **Remember modes the device reports but doesn't advertise** — some firmware reports a current mode it never lists as supported. Issue #327's air conditioner sits in `Quiet` while offering only `Off/Sleep/Speed/Nano/NanoSleep`, so Home Assistant showed the preset as active but refused to select it. LocalThings remembers any such mode it sees and keeps offering it afterwards, stored on the config entry so it survives a restart — the device only names the mode while it is in it, and you shouldn't have to reach for the physical remote after every reboot. Defaults to on. Turning it off offers only what the device advertises, without discarding what was already learned.

The same **Configure** menu has a **Forget remembered modes** step, which clears what has been learned for that device. Use it if a mode was learned that turns out not to be selectable — otherwise, by design, it stays forever.

---

## Part 5: Reading and writing resources directly

Two HA actions, `localthings.write_resource` and `localthings.read_resource`, talk to a device's OCF resources directly instead of through this integration's entity model. They exist for two overlapping jobs: pinning down a device-specific write contract (the reverse-engineering work `docs/investigations/` and the provenance comments throughout `registry/capabilities/` are all about), and driving a resource this integration doesn't model as an entity yet, without waiting on a release.

Both take a `device_id` (a device picker filtered to this integration) and resolve to exactly one appliance — a target that expands to more than one LocalThings device is rejected rather than silently fanned out across all of them. `href` is always canonical (e.g. `/mode/vs/0`); if the device you targeted is a subdevice — an oven's second cavity, an AC's second indoor unit — it's translated to the real on-the-wire href for you (`/mode/vs/1`, say), and the response reports both forms so there's no ambiguity about what was actually sent.

`write_resource` exists because a single write, one at a time, isn't enough to probe some boards. Issue #300's Samsung wall oven answers `2.04 Changed` to a settings write while idle and then silently reverts it — the write only sticks once a cycle is already running. Finding what actually triggers a cycle needs an *ordered sequence* of writes to different resources, with real delays between them, and a way to check afterward whether anything actually held:

```yaml
action: localthings.write_resource
data:
  device_id: abc123...
  writes:
    - href: /mode/vs/0
      payload:
        x.com.samsung.da.modes: ["Bake"]
      settle: 5
    - href: /operational/state/vs/0
      payload:
        x.com.samsung.da.state: "Run"
  verify_after: 30
```

Mind the shapes: what you write is sent verbatim, so the field names and types have to be the ones that resource actually uses. `/mode/vs/0` takes `modes` as an *array* on this board; a bare string, or the singular `mode`, is a different field the device will simply ignore. `read_resource` (below) with no `href` is the quickest way to see the real shape of everything before you write to any of it.

Each write in `writes` (1-10 of them) needs `href` and a non-empty `payload`, sent verbatim as a partial-rep POST — this bypasses the remote-control-off block and every `write_fn`/`validate_fn` a normal entity write goes through, and sends exactly the fields you give it, so it can misconfigure your appliance if you get it wrong. `settle` (0-30s, default 0) is how long to wait *after* that write before starting the next one.

By default the whole sequence holds the device session from the first write to the last, settle delays included, so a routine poll or another entity's write can't land between two steps and blur which write the appliance was reacting to. The cost is that nothing else on that device updates until the sequence ends — up to 10 × 30s if you ask for the maximum of both. Set `hold_session_lock: false` to take the session per write and release it across the waits instead, trading that certainty for a device whose entities keep updating throughout.

The response has one `results` entry per write, with `before`/`after` reps and a `changed` flag (every key/value in `payload` present and equal in the immediate readback):

```json
{
  "device_id": "abc123...",
  "results": [
    {"href": "/mode/vs/0", "actual_href": "/mode/vs/0", "code": "2.04", "raw_code": 68,
     "accepted": true, "before": {...}, "after": {...}, "changed": true},
    ...
  ],
  "verified": {
    "/mode/vs/0": {"code": "2.05", "raw_code": 69, "rep": {...}, "held": false}
  }
}
```

`verify_after` (0-60s, default 0, omit to skip) is what actually answers the "did it stick" question: after the sequence finishes, it waits that long and then re-reads every distinct href the sequence touched, reporting the result under `verified`, keyed by canonical href. `changed` tells you the write was accepted and reflected immediately; `held` tells you whether it was still there N seconds later, or whether the board quietly put it back — issue #300's exact symptom. Where an href was written more than once in a sequence, `held` compares against the *last* payload sent to it. A `held` of `null` means the re-read itself didn't come back (check `code` next to it) — unknown, deliberately not reported as a revert.

If the session drops partway through a sequence, the action raises rather than returning, and the error names how many writes completed and which — the appliance is left holding a partial sequence, so knowing where it stopped is the difference between a usable result and starting over blind.

`read_resource` is the read half, and it's deliberately not just a cache lookup:

```yaml
action: localthings.read_resource
data:
  device_id: abc123...
  href: /mode/vs/0
```

returning `{"href", "actual_href", "code", "raw_code", "rep"}` off a **live GET straight from the device**, not the cache — which can be up to a poll interval stale, exactly the staleness that would make `held` above meaningless. A sixth key, `body`, appears only when the response isn't a Property map: a Collection (`/device/0`, and the `x.com.samsung.devcol` siblings some boards expose) answers a CBOR list, which `rep` can't carry, and which would otherwise read as an accepted-but-empty resource. Omit `href` and you get `{"resources": {href: rep, ...}}`, the cached snapshot of everything this integration currently tracks on that device, with no GET at all — useful for seeing what's there before you start writing to it, without hammering the appliance.

The **Debug write** panel under a device's Configure menu (Part 4) is the friendlier single-write path over this same machinery — pick an href, type a payload, see the result — for when you don't need a sequence.

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
python3.13 -m venv .venv          # 3.13 or newer; see below
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/ -q
```

`requirements-dev.txt` already pulls in Home Assistant and pytest at matching versions, so there's nothing to install alongside it. Use Python 3.13 or newer: pip resolves the newest `pytest-homeassistant-custom-component` your interpreter supports, and on 3.12 or older nothing resolves and the install fails outright. CI runs 3.14.

A large suite covering registry composition, discovery, entity descriptors, and golden-file regression against captured device dumps. `requirements-dev.txt` pins `smartthings-local` the same way `manifest.json` does, so tests exercise the real published protocol layer rather than a vendored copy.

---

## Repo layout

```
custom_components/localthings/
  manifest.json         Requirements (incl. the smartthings-local PyPI dep), version, domain
  __init__.py            async_setup_entry / async_unload_entry
  config_flow.py          ClientHello port probe, UUID fetch, leaf cert minting, identity resolution
  coordinator.py          Polling + push update coordination, stale-state fallback, write dispatch
  observe.py              CoAP OBSERVE (push-mode) support layered on the coordinator
  diagnostics.py           Redacted diagnostics download (device state + coverage metadata)
  services.py              write_resource/read_resource actions (device resolution, href translation)
  services.yaml            Selectors/descriptions for the two services above
  const.py                 Domain, config keys, probe ports
  entity.py                Base entity wiring capability registry -> HA entity
  sensor.py / binary_sensor.py / switch.py / number.py / select.py / button.py / time.py / fan.py / climate.py / water_heater.py
                            One module per HA platform
  catalog.py               Reads the shipped translation catalog (which keys/states exist)
  translations/            Config-flow copy + entity name/state translations, one file per
                            language; en.json is the source of truth (no strings.json —
                            Home Assistant never reads one from a custom integration)
  registry/
    registry.py             Builds the global capability registry, validates href collisions
    capability.py           Capability dataclass (href, entities, transforms)
    entities.py             Per-platform entity descriptor dataclasses
    discovery.py            Binds a device's live resources to registered capabilities
    adapter.py               Flattens bound entities into HA-ready state
    identity.py              Reads /oic/p + /oic/d (manufacturer, model, OCF device type)
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
email, access tokens, hashed device IDs, MAC addresses, serial numbers, and the owner-set device name) before it's
generated, so it's safe to attach
directly to a new issue using the linked device-support template. This is the fastest way to help add or expand
support for hardware the maintainers don't have.

When a diagnostics dump alone isn't enough to pin down how a resource actually behaves — whether a write sticks,
what order things need to happen in, whether the device reverts a change on its own — the `localthings.write_resource`
and `localthings.read_resource` actions from Part 5 are the tool for probing it directly and reporting back what
you found.

---

## Adding a new appliance type

1. Get a capture of the appliance's `/device/0` response. The easiest way: add the device to HA (type detection failing is fine) and pull its Diagnostics download from Settings > Devices & Services > the device > the menu > Download diagnostics — it already contains a redacted dump of the device's resources.
2. Reuse existing `Capability` objects from `registry/capabilities/` wherever the resource matches one already declared. Most `common.py` capabilities (power, kids lock, remote control, alarms, energy/water meters) are shared verbatim across families; add new ones only for resources unique to the new type.
3. Create `registry/by_type/<name>.py` with a `DeviceRegistry(name=..., capabilities=_build([...]))`. Use `pattern_capabilities` instead of `capabilities` for any resource whose `href` isn't fixed (for example per-compartment fridge resources); see `refrigerator.py` for the pattern.
4. Register it in `_REGISTRY_BY_KEY` in `registry/by_type/__init__.py`, then route devices to it by adding the board-family token from their `modelNum` to `_BOARD_TOKEN_TO_KEY` — a single row, e.g. `'VSKR': 'vacuum_station'`. Tokens are matched whole (the model string is upper-cased and split on any run of non-alphanumerics), so one entry covers every delimiter spelling Samsung uses: `TP1X_DA-AC-RAC-01001` and `TP2X_RAC_20K` both resolve on `RAC`. Name the specific type, never the board family that contains it — `DA-AC-` prefixes RAC/WAC/DHM/AIR alike, so a bare `AC` row would swallow the dehumidifier and the air purifier. If the board is shared across types (washers and dryers both report `DA_WM_`), add the consumer-model prefix from `description` to `_CONSUMER_PREFIX_TO_KEY` instead. If the device omits `/information/vs/0` entirely (as the verified NA9300K cooktop does), add a distinctive, conservative resource-signature rule to `for_device_by_resources()`.

   `oneUiVersion` is deliberately not consulted — see `resolve()` in that file for why.
5. Add golden-file coverage in `tests/` against a captured `/device/0` dump for the new type.

No config-flow changes are needed. Device-type detection and entity wiring are fully driven by the registry.

---

## Known device behavior

Samsung's firmware occasionally drops the DTLS session briefly — this is normal appliance-side behavior, not a bug. The integration reconnects automatically, and from HA's perspective a brief reconnect looks like an entity holding its last value for one poll cycle rather than going `unavailable`. When an appliance supports it, the integration prefers push-based updates (instant, via `observe.py`) over polling, falling back to polling otherwise.

If reconnects become persistent (more than a handful per minute), something's actually wrong. Check the appliance's Wi-Fi link first, then look for a competing DTLS client on the LAN — only one active session per appliance is allowed at a time.

Deregistering a device in SmartThings causes a reset of its network settings as soon as it accesses Samsung's servers, dropping it off Wi-Fi until it's re-onboarded through the SmartThings app. As such, consider keeping devices registered even if egress-blocked, to avoid them resetting upon brief internet access.

### Restarting while an appliance is powered off

If Home Assistant restarts while an appliance is unplugged or switched off at the wall, its device and entities still load — restored from the last successful discovery, showing `unavailable` until the appliance answers again. Automations and dashboards keep referring to entities that exist, and the integration retries in the background, so the device comes back on its own within a poll cycle of being powered on. Entities read `unavailable` rather than their last known values on purpose: the integration can't verify what a disconnected appliance is doing, and recorded history is kept by the recorder either way.

This only applies to an appliance the integration has reached at least once. A brand-new device that has never answered has nothing to restore from, so setting it up still requires it to be reachable.

### Multi-subdevice ("2-in-1") air conditioner systems

Some Samsung installs run more than one indoor subdevice off a single outdoor unit, all reachable over the *one* IP/DTLS session your config entry connects to (a floor-standing + wall-mounted 2-in-1 is a common shape). The integration discovers any sibling subdevices automatically, once, right after the first successful poll — there's nothing to configure. Each discovered subdevice gets its own HA device (linked to the main one via "via device") and its own `climate` card, so it lands in its own room in the dashboard instead of being invisible or mixed into the master's state.

Two on-the-wire shapes are supported, both keyed off what the appliance itself reports:

- **Indexed siblings** — the device answers a `/device/1`, `/device/2`, ... collection alongside its own `/device/0`, mirroring every resource at that index.
- **UUID-prefixed tree** — the device reports a sibling's id in `x.com.samsung.da.subdeviceIdList`, and that id doubles as a literal href prefix for the sibling's own resource tree.

A candidate that answers but never produces any real, user-facing state (an unused slot some installs report alongside a genuine second subdevice) is silently skipped rather than turned into a phantom entity — check diagnostics' `subdevices`/`subdevices_skipped` blocks if a subdevice you expect to see isn't showing up, and file an issue with that diagnostics download attached.

---

## Contributing

Patches are welcome, especially:

- New `by_type/` registries for appliance families not yet covered (AC, microwave, etc.) on the same Tizen RT 3.x firmware family.
- Confirmation or refutation of compatibility on additional models within an already-supported type.
- Protocol-level fixes, which belong upstream in [`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local) rather than here. HA-side fixes (entities, config flow, coordinator, registry) belong in this repo.

If you submit a PR, please don't include real device UUIDs, MACs, serials, IPs, or CA private key material. Use the placeholders from the config-flow form instead.
