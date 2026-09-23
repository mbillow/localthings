# Development

## Docker Compose dev environment

```sh
docker compose up -d --build
docker compose logs -f
```

The `Dockerfile` builds on the official `home-assistant/home-assistant:stable` image and pre-installs `smartthings-local`, so the dependency is present at container start instead of depending on HA's own runtime pip-install step. Re-run with `--build` whenever the pinned `smartthings-local` version changes.

`docker-compose.yml` sets `network_mode: host`, which is required since DTLS is UDP and won't traverse Docker's bridge NAT to reach LAN appliances, and bind-mounts `custom_components/localthings/` read-only into `ha_config/custom_components/`. Bump `custom_components.localthings` to `debug` in `ha_config/configuration.yaml` for verbose protocol logging.

## Tests

```sh
python3.13 -m venv .venv          # 3.13 or newer; see below
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/ -q
```

`requirements-dev.txt` already pulls in Home Assistant and pytest at matching versions, so there's nothing to install alongside it. Use Python 3.13 or newer: pip resolves the newest `pytest-homeassistant-custom-component` your interpreter supports, and on 3.12 or older nothing resolves and the install fails outright. CI runs 3.14.

A large suite covering registry composition, discovery, entity descriptors, and golden-file regression against captured device dumps. `requirements-dev.txt` pins `smartthings-local` the same way `manifest.json` does, so tests exercise the real published protocol layer rather than a vendored copy.

## Repo layout

```
custom_components/localthings/
  manifest.json         Requirements (incl. the smartthings-local PyPI dep), version, domain
  __init__.py            async_setup_entry / async_unload_entry
  config_flow.py          Setup, reconfigure and reauth steps, leaf cert minting, identity resolution
  probing.py              Pre-authentication discovery: which port and which API family a host serves
  transport.py            The Transport interface, and CoAP over DTLS behind it
  legacy_http*.py         The 8888/HTTPS appliance family: envelope translation, transport, TLS, token exchange
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

## Adding a new appliance type

1. Get a capture of the appliance's `/device/0` response. The easiest way: add the device to HA (type detection failing is fine) and pull its Diagnostics download from Settings > Devices & Services > the device > the menu > Download diagnostics — it already contains a redacted dump of the device's resources.
2. Reuse existing `Capability` objects from `registry/capabilities/` wherever the resource matches one already declared. Most `common.py` capabilities (power, kids lock, remote control, alarms, energy/water meters) are shared verbatim across families; add new ones only for resources unique to the new type.
3. Create `registry/by_type/<name>.py` with a `DeviceRegistry(name=..., capabilities=_build([...]))`. Use `pattern_capabilities` instead of `capabilities` for any resource whose `href` isn't fixed (for example per-compartment fridge resources); see `refrigerator.py` for the pattern.
4. Register it in `_REGISTRY_BY_KEY` in `registry/by_type/__init__.py`, then route devices to it by adding the board-family token from their `modelNum` to `_BOARD_TOKEN_TO_KEY` — a single row, e.g. `'VSKR': 'vacuum_station'`. Tokens are matched whole (the model string is upper-cased and split on any run of non-alphanumerics), so one entry covers every delimiter spelling Samsung uses: `TP1X_DA-AC-RAC-01001` and `TP2X_RAC_20K` both resolve on `RAC`. Name the specific type, never the board family that contains it — `DA-AC-` prefixes RAC/WAC/DHM/AIR alike, so a bare `AC` row would swallow the dehumidifier and the air purifier. If the board is shared across types (washers and dryers both report `DA_WM_`), add the consumer-model prefix from `description` to `_CONSUMER_PREFIX_TO_KEY` instead. If the device omits `/information/vs/0` entirely (as the verified NA9300K cooktop does), add a distinctive, conservative resource-signature rule to `for_device_by_resources()`.

   `oneUiVersion` is deliberately not consulted — see `resolve()` in that file for why.
5. Add golden-file coverage in `tests/` against a captured `/device/0` dump for the new type.

[`.claude/skills/adding-device-support/SKILL.md`](../.claude/skills/adding-device-support/SKILL.md) is the full version of this workflow: routing, the entity taxonomy, coverage rules, and fixtures.

No config-flow changes are needed. Device-type detection and entity wiring are fully driven by the registry.
