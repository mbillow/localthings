# Contributing

The README's [Contributing](README.md#contributing) section says which patches are welcome and what personal data to keep out of a pull request. This file covers setting up a dev environment, running the checks, adding an appliance type, and how to write comments and commits.

## Dev environment

```sh
docker compose up -d --build
docker compose logs -f
```

The `Dockerfile` starts from the official `home-assistant/home-assistant:stable` image and installs `smartthings-local` at build time, so the container doesn't wait on Home Assistant's own pip install at startup. Rebuild with `--build` whenever the pinned `smartthings-local` version changes.

`docker-compose.yml` uses `network_mode: host`, because DTLS runs over UDP and can't reach LAN appliances through Docker's bridge network. It mounts `custom_components/localthings/` read-only into `ha_config/custom_components/`. For verbose protocol logs, set `custom_components.localthings` to `debug` in `ha_config/configuration.yaml`.

## Tests and checks

```sh
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff format --check custom_components tests
.venv/bin/ruff check custom_components tests
.venv/bin/ty check custom_components tests
.venv/bin/pytest tests/ -q
```

CI runs these four checks on Python 3.14. Use Python 3.13 or newer locally. On 3.12 or older, pip finds no compatible `pytest-homeassistant-custom-component` and the install fails. `requirements-dev.txt` installs Home Assistant, pytest, ruff and ty at matching versions, and pins `smartthings-local` the same way `manifest.json` does, so the tests run against the published protocol library.

The suite covers registry composition, discovery, entity descriptors, the coordinator and config flow, and golden-file regression against captured device dumps in `tests/fixtures/`.

## Repo layout

```
custom_components/localthings/
  manifest.json        Requirements, version and domain
  __init__.py          Config entry setup and unload
  config_flow.py       Setup, reconfigure and reauth steps, certificate minting, identity resolution
  probing.py           Discovery before authentication: which port and which API a host serves
  transport.py         The Transport interface, with CoAP over DTLS behind it
  legacy_http*.py      The 8888/HTTPS appliance family: translation, transport, TLS, token exchange
  coordinator.py       Polling and push updates, restoring state for an offline appliance, writes
  observe.py           CoAP Observe push updates on top of the coordinator
  diagnostics.py       The redacted diagnostics download
  services.py          The read_resource and write_resource actions
  services.yaml        Selectors and descriptions for those actions
  const.py             Domain, config keys and probe ports
  entity.py            Base entity that turns registry entries into Home Assistant entities
  sensor.py, binary_sensor.py, switch.py, number.py, select.py, button.py,
  time.py, fan.py, climate.py, water_heater.py
                       One module per Home Assistant platform
  catalog.py           Reads which keys and states the translation catalog has
  translations/        Config flow text and entity names and states, one file per language.
                       en.json is the source. There is no strings.json, because Home Assistant
                       doesn't read one from a custom integration.
  registry/
    registry.py        Builds the capability registry and checks for href collisions
    capability.py      The Capability dataclass: an href, its entities, and transforms
    entities.py        Entity descriptor dataclasses, one per platform
    discovery.py       Binds an appliance's live resources to capabilities
    adapter.py         Turns bound entities into Home Assistant state
    identity.py        Reads /oic/p and /oic/d: manufacturer, model, OCF device type
    redact.py          Removes account and identity data before diagnostics leave Home Assistant
    capabilities/      Shared and per-family Capability definitions
    by_type/           One DeviceRegistry per appliance type, built from capabilities/
tests/                 The test suite, with captured device dumps in fixtures/
requirements-dev.txt   Test and lint dependencies
docker-compose.yml, ha_config/
                       The local Home Assistant dev environment
```

## Adding an appliance type

1. Get the appliance's resources. Add it to Home Assistant, even if LocalThings doesn't recognize its type, then use **Download diagnostics** on the device. The download holds a redacted copy of every resource the appliance reported.
2. Reuse the `Capability` objects in `registry/capabilities/` wherever a resource matches one. Most of `common.py` is shared across families, including power, child lock, remote control, alarms, and energy and water meters. Add a capability only for a resource unique to the new type.
3. Create `registry/by_type/<name>.py` with a `DeviceRegistry(name=..., capabilities=_build([...]))`. Use `pattern_capabilities` for a resource whose href varies, such as a fridge's per-compartment resources. `refrigerator.py` shows how.
4. Register the new registry in `_REGISTRY_BY_KEY` in `registry/by_type/__init__.py`, then route appliances to it. `resolve()` tries three things in order, and each is a table row rather than new code:
   - a distinctive resource signature in `for_device_by_resources()`, for appliances that report no `/information/vs/0`. Require two independent resources so an unrelated family can't match.
   - the OCF device type from `/oic/d`, in `_OIC_TYPE_TO_KEY`. The diagnostics download shows it under `identity.device_types`. Check this first, because an appliance that names its own type needs no other routing.
   - the board token from `modelNum`, in `_BOARD_TOKEN_TO_KEY`, for example `'VSKR': 'vacuum_station'`. LocalThings upper-cases the model string and splits it on non-alphanumerics, so one row matches every spelling. `TP1X_DA-AC-RAC-01001` and `TP2X_RAC_20K` both match `RAC`. Name the specific type, never the board family. `DA-AC-` covers air conditioners, dehumidifiers and air purifiers alike, so an `AC` row would catch all three. Washers and dryers share the `DA_WM_` board, so they route on the consumer-model prefix in `description`, in `_CONSUMER_PREFIX_TO_KEY`.
5. Add the dump as a fixture and a golden file in `tests/`.

You don't need to change the config flow. The registry drives type detection and entity wiring. [`.claude/skills/adding-device-support/SKILL.md`](.claude/skills/adding-device-support/SKILL.md) is the full version of this workflow, covering the entity categories, coverage rules and fixtures.

## Code comments

This codebase works out undocumented device APIs, so a comment that records why a decision was made is worth more here than in most codebases. Examples are a calibration, a rejected write, or the issue a quirk was confirmed against. Keep the conclusion and cut the investigation. A file with a paragraph under every line is as hard to read as one with no comments.

- **Comment the why, never the what.** If a comment restates the next line, delete it. Code should read clearly enough that comments are needed only for what isn't obvious.
- **One or two sentences.** State the conclusion and the one piece of evidence that makes it credible, such as an issue number, a model name or a single confirming observation. Leave out every dump checked, every failed attempt and every discarded hypothesis. A later reader needs to trust the conclusion and know where to look to redo the work.
- **Point, don't re-derive.** Cite the issue or model once. If a sibling function already documents the reasoning, write "same reasoning as X above" instead of restating it.
- **Keep module and class docstrings short.** A few lines on purpose and any invariant that spans the module is enough.
- **Keep failed-attempt logs out of the code.** If an investigation into an unsolved problem produced negative results worth keeping, such as a reset mechanism nobody could find, put them in an issue or in `docs/`, not in a comment longer than the code below it.
- **When in doubt, cut.** If deleting a comment loses no understanding, delete it. Trim an existing comment before adding a new one.

## Commits

- **The author and committer must be the person accountable for the change.** Never use a tool, bot or AI agent identity, even when an AI coding agent drafted or applied the change. Set both the author and committer to that person's real name and email before committing. This rule is about whose name goes on the change. The identity comes from whoever is responsible for the work in each session, the same as if they had run `git commit` themselves. Don't copy a name from this file.
- **No co-author trailers for AI tools or assistants.** Don't add `Co-Authored-By` lines, or any similar attribution, crediting an AI agent, assistant or tool that helped produce the change. The commit belongs entirely to the accountable person.

## For AI coding agents

See `AGENTS.md`.
