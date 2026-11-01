---
name: adding-device-support
description: >-
  Add or extend support for a Samsung OCF appliance in localthings from a
  /device/0 diagnostics dump. Use when a device-support issue lands, a device
  raises the "incomplete capability coverage" repair, a diagnostics JSON needs
  triaging, or you're mapping OCF resources to HA entities. Covers reading dumps,
  routing a device to a registry from its `/oic/d` device type first and an
  unrecognized board family second (modelNum board tokens, resource
  signatures),
  OCF-standard vs vendor hrefs, the diagnostic/config/normal entity taxonomy,
  preferring dynamic (device-reported) select options over hardcoded lists,
  ensuring every href is bound or ignored, and locking it in with a fixture +
  golden + test. Also covers multi-subdevice ("composite") appliances that
  expose several logical indoor subdevices over one IP — triaging a missing
  second subdevice, and why registry hrefs stay canonical rather than indexed.
---

# Adding device support

localthings maps a Samsung appliance's OCF resources (`/device/0` dump) to Home
Assistant entities. Each resource `href` is handled by a `Capability` that
declares the entities it produces. This skill is the workflow for turning a new
dump into coverage.

## 1. Get the dump and see the gaps

A user's diagnostics download (`config_entry-localthings-*.json`) has, under
`data`:
- `resources`: `{href: rep}` — the parsed `/device/0` snapshot. **This is the
  source of truth**, not code comments. On a multi-subdevice appliance this is
  the subdevice the config entry connects to and *only* that subdevice;
  siblings report their own (see below).
- `unbound_hrefs`: resources that bound to no capability. The
  "incomplete capability coverage" repair fires whenever this is **non-empty or
  the device type is unrecognized** (`coordinator._update_coverage_gap_issue`).

Multi-subdevice appliances (one IP, one DTLS session, several logical indoor
subdevices — issue #177) add four more, all absent/empty on an ordinary device:
- `subdevices`: one entry per materialized sibling — `kind`/`key`/`seed_path`,
  its own `model`, its bound hrefs, and its own `resources`.
- `subdevices_skipped`: candidates whose seed answered but that produced no
  live primary state, with the reps the gate actually judged. An unused
  SmartThings slot lands here, not in `subdevices`.
- `subdevice_probes`: `{seed_href: found}` for every seed attempted — tells
  "checked, nothing there" apart from "never checked".
- `multidevice`: `/multidevice/vs/0`'s rep if the board answers it. Its
  `numofsubdevice` is a corroborating count, not a gate.

Goal: make `unbound_hrefs` empty by **binding** the useful resources and
**ignoring** the noise — and surface every genuinely useful sensor/select/switch
along the way.

## 2. Compute coverage without Home Assistant

The `registry/` package is HA-free, so you can drive discovery directly (HA
isn't importable standalone because `localthings/__init__.py` pulls it in — stub
the package to skip that):

```python
import sys, types, json, importlib
cc = types.ModuleType('custom_components'); cc.__path__=['custom_components']; sys.modules['custom_components']=cc
lt = types.ModuleType('custom_components.localthings'); lt.__path__=['custom_components/localthings']; sys.modules['custom_components.localthings']=lt
by_type   = importlib.import_module('custom_components.localthings.registry.by_type')
discovery = importlib.import_module('custom_components.localthings.registry.discovery')
adapter   = importlib.import_module('custom_components.localthings.registry.adapter')

data = json.load(open('dump.json'))['data']
resources = data['resources']
# identity.device_types is /oic/d's `rt` -- resolve()'s primary signal (see
# §3). Absent on dumps predating that field; () falls through to model-based
# detection exactly like a device that reports nothing there.
device_types = tuple((data.get('identity') or {}).get('device_types') or ())
reg = by_type.resolve(resources, device_types=device_types)   # the same entry point the coordinator uses
unbound = []
bound = discovery.discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
state = adapter.flatten(bound, resources)   # {entity_key: value}
print('registry:', reg.name, 'unbound:', sorted(unbound))
print('state_keys:', sorted(state))
```

`discover()` binds caps (applies `rt_filter`/`match_fn`); `flatten()` applies
`exists_fn` and produces the final entity values. Use the same routine to
regenerate a golden.

**A sibling subdevice's block runs through this unchanged.** `subdevices[i].resources`
(and `subdevices_skipped[i].resources`) are keyed by *canonical* hrefs —
`/mode/vs/0`, never the `/mode/vs/1` or `/<uuid>/mode/vs/0` that subdevice
actually answers on — precisely so you can paste one into `resources` above and
read the result exactly like the master's. No de-indexing by hand.

## 3. Route the device to a registry — add a row, never a branch

If detection returns `None`, the device falls back to common capabilities and
loses roughly **half** its entities (measured across the fixture corpus: 843 of
1510 bound entities survive). So routing is the first thing to fix, and
`registry/by_type/__init__.py` is deliberately kept boring.

`resolve(resources, device_types=())` is the only entry point — the
coordinator, the config flow's probe and the golden-regression harness all
call it, so the order can't drift between what ships and what the tests
assert. Four stages, most-specific evidence first:

1. **`for_device_by_oic_type(device_types)`** — the primary path whenever a
   dump has it. `device_types` is `/oic/d`'s `rt`, looked up against
   `_OIC_TYPE_TO_KEY`. The device naming its own type beats parsing board
   part numbers, so this always wins when it hits. **Always check this first
   when triaging a new dump** — see "Adding an /oic/d device type" below.
2. **`for_device_by_subtype(deviceSubType)`** — the 8888 bridge's
   equivalent, since it serves no `/oic/d`. Each record names its own type in
   `Information.deviceSubType` (`Washer`, `Dryer`, `WallOven`, ...), which
   lands in `/information/vs/0` and is looked up against
   `_DEVICE_SUBTYPE_TO_KEY`. A new 8888 appliance needs a row here, not a
   board token.
3. **`for_device_by_model(model_num, description)`** — the fallback for
   everything `/oic/d` doesn't resolve. Both fields come from
   `/information/vs/0`. Board-family tokens are matched against `modelNum`
   first, then `description`, then the fuzzy two-letter consumer-model prefix.
4. **`for_device_by_resources(resources)`** — for boards that report no
   `/information/vs/0` at all. Needs a *distinctive* signature.

**`oneUiVersion` is not consulted.** It looks like the obvious signal — the
device naming its own type, `'7.0 Dishwasher'` — and it used to be stage one.
But only a minority of hardware reports it, every device that does is already
typed by its modelNum board token (`TestOneUiVersionIsNotConsulted` checks that
against the whole corpus), and no device-support issue was ever fixed by adding
a mapping for it. Don't reintroduce it as a detection stage; it stays in
diagnostics as a firmware-generation marker (`'7.0 Air conditioner'` means
Tizen Lite), which is useful when triaging.

### Adding a board family

Almost always a one-line addition to `_BOARD_TOKEN_TO_KEY`:

```python
'VSKR': 'vacuum_station',   # issue #131 -- stick-vacuum clean station
```

Matching is on **whole tokens** of the model string, split on any run of
non-alphanumerics and upper-cased. That is what keeps this a table, and it
carries rules:

- **Never add a delimiter spelling.** `'_RAC_'` and `'-RAC-'` are the same
  entry, `RAC`. If you find yourself adding a second row for punctuation, the
  tokenizer already handled it.
- **Name the specific type, never the board family.** `DA-AC-` prefixes
  RAC/WAC/DHM/AIR alike — a bare `'AC'` row would swallow the dehumidifier
  and the air purifier. Same for `DA`, `KS`, `WM`, `TP1X`, `ARTIK051`.
  `TestBoardTokenTable` asserts these stay out.
- **Never add a token that can co-occur with another.** `_board_family_key`
  returns the first hit, which is only safe while no real model string
  contains two tokens naming different types.
  `TestBoardTokenAmbiguity` checks that invariant against every fixture, so a
  new dump exercises it automatically — if it fails, the answer is a narrower
  token, not a reordering.
- **Two-letter tokens are a last resort.** `'CT'` (legacy gas cooktop) is the
  only one, and it is loose enough to collide by accident.

Reach past the table only when the evidence isn't a board token:

- **Consumer-model prefix** (`_CONSUMER_PREFIX_TO_KEY`) — for washers, dryers
  and dishwashers, whose `modelNum` is the shared `DA_WM_` laundry board and
  whose real type is only in `description`'s trailing model code
  (`..._WA8000T`). Deliberately split on `_` only: widening it to `-` would
  read the dishwasher's `ADW-WW-RTL-24-AILITE` board segment as a `WW`
  washer. Consulted last because a two-letter prefix is the weakest evidence
  here — `WAC` (window AC) starts with `WA` (top-load washer).
- **Resource signature** (`for_device_by_resources`) — only when
  `/information/vs/0` is absent entirely. Require **two** independent shapes
  (e.g. `/oven/vs/0` present *and* a `MicroWave*` entry in `supportedModes`),
  never one, or an unrelated family's `/mode/vs/0` will match.

### Adding an /oic/d device type

`/oic/d`'s `rt` (OCF's own device-type declaration) is the *primary*
detection path (`for_device_by_oic_type`, stage 1 above) — it sits outside
the `/device/0` dump, read separately by `registry/identity.read_identity`,
which fetches three endpoints in one shot:
- **`/oic/d`** — the device type itself: `n` (device name) and `rt`, a list
  carrying the generic `oic.wk.d` base type every OCF device has alongside a
  concrete one (`oic.d.airconditioner`) or a SmartThings vendor extension
  (`x.com.st.d.stickcleaner`, for categories with no `oic.d.*` equivalent —
  same prefix convention as `x.com.samsung.da.*` resource fields elsewhere).
- **`/oic/p`** — platform identity: `mnmn`/`mnmo` (manufacturer/model).
- **`/oic/res`** — resource discovery, used for subdevice enumeration (§11),
  not device typing.

None of the three appear in `resources`; find them in diagnostics' `identity`
block (`identity.device_types`, `identity.manufacturer`, `identity.model`),
or read live with `read_identity(sess, serial)` if you're driving a device
directly.

**Whenever you triage a dump, check `identity.device_types` before touching
`_BOARD_TOKEN_TO_KEY` at all** — the whole point of this stage running first
is that a real `/oic/d` type makes board-token routing unnecessary. Two
outcomes:
- The type is already a key in `_OIC_TYPE_TO_KEY` (`registry/by_type/__init__.py`)
  → detection already works; an unbound-hrefs gap on this device is a
  capability-coverage problem (§§4–9), not a routing one.
- The type is **not yet in the table** → add a row. This is now the
  integration's primary detection method, and it only stays that way if new
  types get folded in as real dumps surface them — same discipline that
  keeps `_BOARD_TOKEN_TO_KEY` current:

```python
'oic.d.dishwasher': 'dishwasher',
'x.com.st.d.steamcloset': 'air_dresser',
```

- A string not yet seen in a dump is still fine to add on the strength of the
  OCF Smart Home Device Specification's Table 9-1 alone, as long as it has the
  exact same `oic.d.<category>` shape as an already-confirmed entry — that
  shape is low-risk ahead of a dump because, unlike a board-token entry,
  there's no tokenizing or delimiter-spelling judgment call involved.
- **Only add a row once there's a real registry key on the right** (a key in
  `_REGISTRY_BY_KEY`). A type naming a product this integration has no
  registry for stays unmapped rather than getting coerced onto the
  nearest-sounding one — `oic.d.robotcleaner` names an actual robot vacuum,
  a different product from the clean/auto-empty *station* `vacuum_station`
  covers (no vacuum-body capabilities at all; see that registry's own module
  docstring), so it's deliberately absent even though the string is known.
- Falls through to `for_device_by_model`/`for_device_by_resources` when
  `device_types` is empty or maps to nothing — most hardware still doesn't
  populate `/oic/d` usefully, so those two stages stay load-bearing for
  everything this one doesn't catch.
- On a multi-subdevice appliance, `device_types` only ever comes from the
  *master's* `/oic/d` (`discover_partitioned`'s `oic_device_types` param) —
  subdevices have no `/oic/d` of their own read today and keep resolving from
  their own `/information/vs/0`, falling back to the master's whole registry
  otherwise (§11).

### Sharing a registry vs adding one

Route a new family to an **existing** registry when its resource surface
matches (most AC board families do — verify by checking the dump binds with
zero unbound hrefs). Add a **new** registry only when the resources genuinely
differ: `vacuum_station` earned one because it shares no hrefs with anything
modelled; `microwave` split from `oven` over a distinct mode vocabulary,
setpoint bounds, and a `powerLevel` field.

## 4. OCF-standard vs vendor hrefs (`/x/0` vs `/x/vs/0`)

Samsung appliances run RT-OCF and often expose the **same state twice**:
- `/x/vs/0` — **vendor** resource, `x.com.samsung.da.*` fields.
- `/x/0` — **standard OCF** resource type (`oic.r.*`) with OCF's fixed field
  names. Confirmable against the OCF spec: `/power/0` `{value: bool}` is
  `oic.r.switch.binary`; `/operational/state/0` is `oic.r.operational.state`.

Newer firmware advertises both as Samsung migrates onto the OCF standard. **There
is no single "always prefer vs / always prefer non-vs" rule** — decide per
resource from the populated dump:
- **Both populated, same state** (power, kids-lock, remote): prefer the
  OCF-standard `/x/0`; fall back to `/x/vs/0` when `/x/0` is absent. Encode with
  a `match_fn` presence check — see `common.POWER_GENERIC` / `POWER_VS_FALLBACK`.
- **Only vendor populated** (`/energy/consumption/0` is often empty `{}`): use
  `/x/vs/0`.
- **Vendor is a superset** (`/operational/state/vs/0` adds fields the OCF one
  lacks): build on the vendor resource, ignore the OCF subset.

Course/cycle is **not** an OCF question — there's no standard course resource, so
`/course/vs/0` (and the `/st/*course/vs/0` re-encoding) are both vendor.

## 5. Entity taxonomy — the judgement call

For each field worth exposing, decide the entity kind and category
(`entity_category` on the descriptor):
- **Normal / primary** (no `entity_category`): the things a user acts on or
  watches — power switch, machine state, the cycle select, energy sensors.
- **`config`**: user-tunable settings — sound mode, door LED, wash temperature,
  buzzer. Shown under the device's Configuration section.
- **`diagnostic`**: read-only status/troubleshooting — alarms, diagnosis, job
  beginning status, last-operation source.

Also set `poll_tier` (`hot`/`warm`/`cold`) on the capability for how often it's
sub-polled between summary polls. Pick descriptor types from `entities.py`
(`SensorDesc`, `SelectDesc`, `SwitchDesc`, `NumberDesc`, `BinarySensorDesc`,
`TimeDesc`, `ButtonDesc`) — the class selects the HA platform.

**Educated guesses are fine — flag them, don't hide them.** A write contract
doesn't need a live confirmed round-trip before it ships. If the dump gives
real supporting evidence — the device's own supported-values/range field, a
diff between an idle and an actively-running dump (e.g. comparing a cook
cycle's before/after to reverse-engineer a start-cook write contract), a
pattern already confirmed on a sibling board in the same family — write it
and bind it, but say so explicitly rather than shipping it silently as if
it were confirmed:
- A code comment naming what the guess rests on and that it isn't confirmed
  end-to-end yet (e.g. "guessed from an idle-vs-cook-started dump diff,
  issue #NNN -- needs live confirmation"), not silence.
- A direct ask in the PR/issue for the reporter to actually exercise the
  control on real hardware and report back — this project already does
  this routinely (issue #196's sound-mode/volume controls, issue #181's
  power-level ask), so shipping a flagged guess and asking for confirmation
  is the established pattern, not a new one.

Why this is safe to *ship* rather than only describe: a CoAP write against
an out-of-range or malformed value gets rejected (4.xx), not acted on — the
worst case for a wrong *value* is a no-op, not a damaged or misbehaving
appliance. That margin only covers the value, though, not the semantics:
bind a guessed write to the device's own reported range/supported-list
rather than inventing bounds, and don't guess a unit the dump gives no way
to cross-check (temperature scale, minutes vs. seconds) — being
syntactically valid but semantically backwards is exactly the case a
rejection won't catch.

The same unit caveat applies on the **read** side, but with a sharper
failure mode: a guessed `unit`, `device_class`, or `state_class` on a
`SensorDesc` silently mislabels the entity in HA forever (every reading,
every graph, every long-term statistic), with no 4.xx to catch it. The
write-rejection safety net above doesn't cover reads — the device happily
returns whatever it returns. So the read-side equivalent of "bind a write
to the device's own reported range/supported-list" is: leave `unit`/
`device_class`/`state_class` unset when the dump gives no field that
nominates one (no `supportedGrades`, no second dump to compare against,
no family member whose same field is already mapped). Match an
already-bound descriptor on a sibling family when the underlying field
and value shape are identical; otherwise expose the reading without an
HA-level interpretation and let a future reporter or dump confirm it.
See `air_monitor.AIR_QUALITY`'s docstring for the worked example
(three dust keys, no `device_class`, no `unit`).

Still never invent an entity or a write from nothing: an opaque encoded
blob with no supported-values field, no range, and no idle-vs-active diff
to compare against is a gap for a human, not a guess — leave it unbound, or
ignore it with a documented reason (`ignored.py`'s rule).

Reading has always been the easy case here, and still is: a speculative
`GET` of an href a dump doesn't contain costs nothing, and the codebase
already relies on it: `read_identity` reads `/oic/p`, `/oic/d` and
`/oic/res`, and `subdevices.enumerate_subdevices` probes `/device/<n>`,
`/<uuid>/device/0` and `/multidevice/vs/0` on every device — and, when a
prefixed candidate's own `/<uuid>/device/0` doesn't answer (issue #205: not
guaranteed even on the board this pattern was built against), every href
the master itself answered this cycle, individually under that UUID's
prefix (see §11). A RETRIEVE is non-mutating and a 4.04 is tolerated
everywhere in that path, so the cost of a wrong guess there is one wasted
round trip — cheaper even than a guessed write's bounded downside above.

## 6. Select options: read them from the device, don't hardcode

A `SelectDesc`'s `options` should come from the device's own advertised list
whenever the resource carries one, not from a Python tuple typed in from a
single dump. Two dynamic forms already exist in the repo and should be
reached for first:
- `options_field='x.com.samsung.da.supportedModes'` (or whatever the
  resource's own supported-values field is called) — reads the live rep on
  the capability's own href. See `laundry.py`'s `buzzer_sound`/
  `finish_sound` (`options_field='supportedBuzzerSound'`/
  `'supportedFinishSound'`).
- `options=<callable>` — for option lists that live on a **different**
  resource than the select's own href (e.g. a course table keyed off a
  sibling href). See `laundry.cycle_select`'s `options=cycle_options`.

A static `options=(...)` tuple is a coverage gap waiting to happen: the next
dump from a different board generation will report modes/values the tuple
doesn't have, and both the HA options list *and* `write_fn`'s validation (if
it checks the same tuple) will silently reject values the device itself
advertises as supported. That's exactly what happened with `oven._OVEN_MODES`
in issue #138 — a hardcoded list rejected `AirFryer`/`Dehydrate`/
`SelfClean`/etc. even though the device's own `supportedModes` field listed
them. Reach for a static tuple only when the dump genuinely has no
supported-values field to read (e.g. the NV7000BS-class oven dump
`_OVEN_MODES` was inferred before any live oven dump existed — see that
module's docstring), and treat it as an interim best-guess rather than a
permanent design choice: migrate it to `options_field`/a callable the moment
a dump with a real supported-values list surfaces, instead of just adding
the new values to the static tuple.

## 7. Names and enum labels live in translations, never in Python

Descriptors have **no `name` field**. Every entity is named from the shipped
catalog, keyed by `translation_key` — which defaults to the descriptor's own
`key`. So adding `SensorDesc(key='filter_status', ...)` obliges you to add:

```json
"entity": { "sensor": { "filter_status": { "name": "Filter status" } } }
```

to `translations/en.json`. Skip it and the entity ships nameless;
`tests/test_translations.py` fails the build instead.

- **Sentence case** ("Filter status", not "Filter Status"), per HA's style
  guide — capitalize only proper nouns and Samsung feature names ("AI Energy
  Mode", "Storm Wash+").
- Set `translation_key` explicitly only to **share** one catalog entry across
  descriptors, or to point at a differently-named one. Two descriptors on the
  same platform with the same `key` already share an entry — intended for
  `common.py`'s OCF/vendor fallback pairs, a silent mislabel otherwise.
- Prefer HA's own vocabulary where it fits: a `device_class` gives you
  translated states for free (`binary_sensor` door/running, `sensor`
  timestamp/enum), so don't restate them.

Selects whose options are raw device codes (course/cycle, code-valued
settings) additionally need those codes labelled:
- `options`/`options_field` supply the **raw** codes; the catalog maps them.
- Add labels under `entity.select.<translation_key>.state.<code>`, code
  **lowercased** (e.g. `"16": "Cotton"`). `select.py` derives which values it
  normalizes from the catalog itself, so there is no Python list to keep in
  sync — a code with no entry simply renders as the raw code, which is the cue
  to identify and name it.

`translations/en.json` is the only place any of this lives: there is no
`strings.json` (Home Assistant doesn't read one from a custom integration) and
no `[%key:...%]` resolution (that's Core build tooling). Every other language
must mirror `en.json` key for key — also enforced by
`tests/test_translations.py`.

### Look for English that Python builds

The catalog only covers what it owns. Anything Python puts in front of the
user is untranslated unless it goes through the catalog, so check a new
device for:

- **`{instance_name}` values.** Entities with `use_instance_name=True` get
  their compartment or ice-maker name from the href/key (`Door Cvroom`) or
  from a device name field (`CUBED_ICE` → `Cubed Ice`). Each one needs an
  entry under `selector.instance_name.options.<slug>`, where the slug is the
  derived name lowercased with spaces as underscores (`door_cvroom`). hassfest
  rejects an unknown top-level section, which is why this table sits under
  `selector`. `test_every_fixture_instance_name_has_a_catalog_entry` fails on
  a new fixture's name until it's added (issue #533).

  Write the value as what a person would call the part, not a tidied-up href:
  `door_cvroom` is "Flex zone door", `door_cooler` is "Fridge door" (Samsung's
  "cooler" is the fridge compartment). Read it inside every template it
  fills ("Flex zone door open", "Top compartment storage mode") before
  settling on it. Name what the part is, not what the href says it is: the
  RR40M7165WW fridge reports `/door/onedoorfreezer`, so single-door units get
  a neutral "Main door".

  Instance templates start with `{instance_name}` in every language
  (`test_instance_name_templates_lead_with_the_placeholder`), so a value is a
  capitalized noun phrase with no article. Where a template's adjective has
  to agree with the noun (Spanish "abierta", Italian "aperto"), pick that
  language's values so they share one gender, or give the template a
  separator (`{instance_name} — otwarte`).
- **Other `translation_placeholders` values.** A number or an href is fine as
  is; a word or phrase needs the same treatment as above.
- **Fallback labels.** A `display_fn` or similar that turns a raw value into
  English prose is a missing catalog entry. A device-supplied name (a
  personal course, an open-ended vendor string) is fine to show as is.

When you add an entry, fill in all languages with a real translation, reusing
the wording that language already uses for the same thing elsewhere in its
file (e.g. its "Freezer temperature" for "Freezer").

**Don't write a test that just re-asserts a translation string.** Adding
labels is a data change, not a logic change, and `tests/test_translations.py`
already holds the invariants that matter for data (every descriptor has a
catalog entry, every language mirrors English key-for-key, no unresolved
`[%key:...%]`). A test that loads the catalog and asserts
`catalog["select"]["foo"]["state"]["16"] == "Cotton"` right after you just
wrote that exact line into `en.json` doesn't exercise any code path — it
re-states the JSON file in Python, passes by construction, and only ever
fails when someone *correctly* edits the label later (a wording fix, a
translator's improvement). It's not a regression test, because there's no
`select.py`/`adapter.py` logic between "the JSON says X" and "the test reads
X" for it to catch drift in. If a code/label mapping is worth locking in,
test it through the code that actually consumes it instead — a write
contract (`desc.write_fn(...)` returns the right raw code), a read contract
(`flatten()` produces the right raw value from a fixture rep), or a routing
decision — never a bare literal-string comparison against the catalog you
just edited.

## 8. Coverage discipline: bound or ignored

Every href in the dump must resolve, or the repair fires. If a resource isn't
worth an entity, add it to `capabilities/ignored.py` (a no-entity `Capability`)
with a one-line reason. Add there only when it's **irrelevant plumbing**
(network/OTA/account housekeeping) or a **duplicate of state exposed via a
friendlier href**.

- **Global vs per-registry ignore:** `ignored.IGNORED` is folded into every
  registry. A global ignore **collides** (via `_build`) with any real capability
  that binds the same href in some family — e.g. `/course/vs/0` can't be globally
  ignored because washers bind it. When only one family should ignore an href
  that another binds, scope the ignore to that family's registry.

- **Registry hrefs are always canonical — never index or prefix one.** On a
  multi-subdevice appliance, `unbound_hrefs` reports the *real* href a gap was
  seen on, so a sibling's gap shows up as `/foo/vs/1` or
  `/<uuid>/foo/vs/0`. Do **not** write `Capability(href='/foo/vs/1')` for it.
  Binding runs against each subdevice's canonical view, so an indexed or
  prefixed href in a registry matches nothing on any device and fails
  silently — no error, no entity, and the gap stays open. Fix it on the
  `/foo/vs/0` form and every subdevice gets it at once.
  (`registry/subdevices.py` owns the canonical ⇄ actual translation; nothing
  under `capabilities/` or `by_type/` should ever mention a subdevice index.)

## 9. Reuse before writing new code

Check `common.py` (generic OCF: power, energy, alarms, water) and `laundry.py`
(shared washer/dryer/dishwasher: buzzer, job status, `cycle_select` + course
machinery) before adding a capability. Cross-family reuse is normal — the dryer
registry uses `fridge.FIRMWARE_UPDATE`; all three laundry families share
`laundry.cycle_select`. If two families hand-roll the same helper, hoist it to a
shared module rather than copying.

## 10. Lock it in

If the dump's diagnostics `identity` block carries a `/oic/d` device type,
confirm (or add, per "Adding an /oic/d device type" in §3) the matching
`_OIC_TYPE_TO_KEY` row before considering this device done — routing this
device by board token today doesn't mean the next report of the same
appliance family gets the faster, more reliable `/oic/d` path unless the
table actually has the row.

1. Add a **scrubbed** fixture `tests/fixtures/<type>_device.json`
   (`{"device0": [ {devcol rep}, {href, rep}, ... ]}`) — replace serials, MACs,
   and other PII with placeholders.

   A multi-subdevice dump (issue #177) may carry three more top-level keys, all
   optional and defaulted for every other fixture — load them with
   `conftest._load_device_full` rather than `_load_device`:
   - `oic_res`: the raw `/oic/res` link array, which is what enumeration reads
     to find `/device/<n>` siblings.
   - `seeds`: `{seed_href: raw_batch_list}` — each sibling's own collection
     response, in the same `[devcol rep, {href, rep}, ...]` shape as `device0`.
   - `probes`: `{href: rep}` for plain Property-map resources belonging to no
     batch (e.g. a hand-read `/multidevice/vs/0`).

   Add a `seeds_note` saying which parts are verbatim captures and which were
   constructed. A fixture that quietly mixes the two is worse than no fixture:
   the whole point of the corpus is that it records what hardware actually did.
2. Generate `tests/fixtures/golden/<type>.json` (`{"state_keys": [...]}`) with
   the harness in §2. A multi-subdevice fixture's golden carries a sibling's
   keys under a prefix (`subdevice1_climate`, `subdevice_<uuid>_climate`)
   alongside the unprefixed master keys — that's the entity-ID namespacing,
   not a bug.
   The master's keys are unprefixed *by design* and must never gain one:
   that's what keeps every pre-#177 device's `unique_id` stable.
3. Add the type to `test_golden_regression.py` and write a
   `test_<type>_capabilities.py` asserting **zero unbound hrefs** and that the
   expected entities exist (and any misleading ones are gated).
4. Run `pytest tests/ -q` — and re-run the golden tests for **other** device
   types after any change to `common.py`/`laundry.py`, since they share those.

The new fixture is picked up automatically by the corpus-wide checks (the
`all_device_fixtures` conftest fixture), including
`TestBoardTokenAmbiguity` — so a model string that collides with an existing
board token fails the build rather than silently mistyping someone's
appliance.

**Don't put a reporter's name or GitHub username in code.** Fixture data
gets serials/MACs/other device PII scrubbed per point 1 above — the same
rule applies to the *prose* you write while fixing the issue: comments,
docstrings, `seeds_note`, and test/function names should say "the
reporter," "issue #NNN's reporter," or (when a module already distinguishes
multiple reporters, like `subdevices.py`'s Pattern A/Pattern B) "the
Pattern A reporter," never a real name or handle. That prose ships in the
package and lives in git history indefinitely — unlike an issue thread or a
release-notes thank-you (both fine places to credit someone by name), it's
not somewhere a person would expect to stay named forever. If you're fixing
an issue and about to write `<username>'s board`/`<username>'s dump` in a
comment, stop and swap in a generic reference instead.

## 11. Triage: "one of my subdevices is missing"

For an appliance that exposes several logical indoor subdevices over one IP —
a 2-in-1 air conditioner, plausibly a multi-drum washer (#19). Work down
the dump in this order; each step rules out a different cause.

1. **`subdevice_probes`** — did we even look? Every seed attempted appears
   here with what it returned. An absent seed means enumeration never tried
   that path; a `false` means it tried and got nothing. On a UUID-prefixed
   board whose `/<uuid>/device/0` reads `false` (issue #205 — this isn't
   rare, not even on the board the pattern was built against), the report
   also carries one probe per href the master itself answered that cycle,
   individually under that prefix (`subdevices.enumerate_subdevices`'s flat
   fallback) — a `true` there is real, confirmed-live evidence for that one
   href, not a guess.
2. **`subdevices`**/**`flat_hrefs`** — for a *materialized* subdevice found
   this way, `flat_hrefs` lists exactly which hrefs it's actually being
   polled on (individually, no Collection endpoint to batch through) —
   compare against the master's own hrefs to see what's still unconfirmed
   for that sibling.
3. **`subdevices_skipped`** — did we find it and reject it? A candidate lands
   here when its seed(s) answered but it produced no *primary*
   (non-diagnostic), non-meter entity with a populated value. Its
   `resources` block holds the exact reps the gate judged, so you can check
   the call yourself. If every power/mode/temperature rep is `{}`, the
   subdevice is an unused slot and the skip is correct — a populated
   `/energy/consumption/vs/<n>` alongside them doesn't change that (issue
   #214: an appliance's lifetime kWh counter shows up under an unused
   slot's index too, and materializing on it produced a phantom duplicate
   air conditioner, so cumulative meters are excluded from the gate). If
   the *operational* reps are populated, the gate is wrong — that's a bug
   worth a fixture. A flat-fallback candidate whose
   only confirmed href is `/information/vs/0` (never bound to any entity —
   only ever read for device-type resolution) will *always* land here until
   more of its hrefs are confirmed live; that's the gate working as
   intended, not a bug to chase.
4. **`multidevice.numofsubdevice`** — the board's own count, where it
   reports one. `coordinator._run_discovery` compares it against
   `len(materialized) + 1` (materialized subdevices plus the master) and
   only warns on disagreement — `subdevices_skipped` entries don't count
   toward either side, since they never materialized. A strong hint, not
   proof; only one board family is known to expose it.
5. **Which pattern is this board?** `identity.resources['/oic/res']` listing
   `/device/1`, `/device/2` means indexed siblings. `resources['/subdevices/
   vs/0']` carrying a `subdeviceIdList` means a UUID-prefixed tree, and that
   same UUID usually shows up as an href prefix in `/oic/res` too — enumerate
   whether or not `/<uuid>/device/0` itself answers, per §5's fallback.
   Neither present, on a device the owner insists has two subdevices, is the
   interesting case — that's a third mechanism and needs a new dump, not a
   code guess.

Two things that are *not* the fix: adding a capability for an indexed href
(see §8), and loosening the liveness gate to "any populated entity" — a
rejected slot routinely reports a non-`None` *diagnostic* value off an empty
resource (and, on some boards, a populated appliance-level meter), which is
exactly what the primary-entity and meter filters exist to ignore.

### The mirror image: "I have one subdevice too many"

Same dump, read the other way (issue #214). A duplicate device in HA is
either a candidate that shouldn't have materialized — check `subdevices`
for one whose `resources` are all `{}` except a meter/`/information`, which
is the unused-slot shape from step 3 — or a **leftover registry entry** from
a release that did materialize it. Those two look identical in the HA UI and
are told apart by the dump: a leftover shows `subdevices: []` (or no entry
for that key) while the device is still listed in HA.

Nothing prunes a leftover automatically — subdevice enumeration is one-shot
and a real sibling can miss a poll, so auto-removal would throw away a live
subdevice's name/area/automations on a transient miss. The integration
implements `async_remove_config_entry_device`
(`custom_components/localthings/__init__.py`) instead, which is what puts a
working "Delete device" button on anything this entry no longer provides;
devices it *does* provide refuse removal, since HA would just recreate them.
Tell the reporter to delete the stale device, don't add a pruning pass.

## Key files
- `registry/identity.py` — `read_identity`, `DeviceIdentity.device_types`
  (`/oic/d`'s `rt`), the primary device-type signal's source.
- `registry/by_type/__init__.py` — `resolve()`, `for_device_by_oic_type` and
  `_OIC_TYPE_TO_KEY`, `for_device_by_model` and `_BOARD_TOKEN_TO_KEY`/
  `_CONSUMER_PREFIX_TO_KEY`, `for_device_by_resources`.
- `registry/subdevices.py` — `Subdevice`, enumeration, canonical ⇄ actual href
  translation, and the materialization gate for multi-subdevice appliances.
- `registry/discovery.py` — `discover()`, unbound reporting, pattern caps.
- `registry/capability.py`, `registry/entities.py` — the `Capability` and
  descriptor shapes (`rt_filter`, `match_fn`, `exists_fn`, `rep_fn`, `write_fn`).
- `registry/capabilities/{common,laundry,fridge,...}.py` — capability defs.
- `registry/capabilities/ignored.py` — the ignore list + its philosophy.
- `registry/by_type/*.py` — per-device-type registries (what to include).
- `registry/registry.py` — the global unknown-device fallback + collision check.
- `tests/test_golden_regression.py`, `tests/fixtures/` — regression harness.
