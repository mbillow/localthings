# Loading a config entry while the appliance is offline

Issue #295 asks for faster recovery when a powered-off appliance comes back,
instead of waiting out HA's `ConfigEntryNotReady` backoff. PR #303 tried to
get there by catching the first-refresh failure in `async_setup_entry` and
loading the entry anyway.

That doesn't work here, and the reason is worth writing down: this
integration has no static entity list. Every entity comes from discovery,
and discovery only happens inside a successful poll.

(The issue's "up to 15 minutes" is out of date, incidentally. Current HA
retries on `2 ** min(tries, 4) * 5` seconds — capped at 80s, not 900. The
backoff was never the worst part; a device card reading "Retrying setup" with
no entities behind it is.)

## What PR #303 produces today

Measured on the PR's branch — set up with `_poll_once` raising, then advance
the clock four summary intervals:

| | |
| --- | --- |
| entry state | `LOADED` |
| `coordinator.bound` | 0 |
| entities in the state machine | 0 |
| registry entries | 1 (the disabled connection-mode sensor) |
| coordinator listeners | 0 |
| `_unsub_refresh` | `None` |
| poll attempts over the next 4 intervals | **0** |

The entry loads and then never polls again. `DataUpdateCoordinator._async_refresh`
reschedules only `if not auth_failed and self._listeners and not
self.hass.is_stopping`; with no bound entities the only unconditional entity
is `LocalThingsConnectionModeSensor`, which is
`entity_registry_enabled_default = False` and so never added and never
subscribes. Nothing reloads the entry either. The device comes back online to
an entry that is permanently empty until a manual reload — strictly worse
than the backoff it replaces, which did recover on its own within 15 minutes.

## Why entities can't just be created offline

Four independent gates, all of which need live device data:

1. `bound` is only ever assigned in `_run_discovery` (`coordinator.py:1081`),
   which runs on a poll's `resources` dict.
2. All ten platforms enumerate `coordinator.bound` exactly once, at forward
   time (`sensor.py:34` and siblings). Nothing adds entities later — the
   invariant is already documented at `coordinator.py:1283-1286`.
3. `_is_included` (`entity.py:39`) returns False whenever `last_resources`
   has no rep for the href. Even a fully reconstructed `bound` filters to
   nothing while `StateCache` is empty.
4. `LocalThingsEntity` is a bare `CoordinatorEntity` with no `available`
   override, no `RestoreEntity`, and no `Store` anywhere in the component. An
   entity that did exist offline would be `unavailable` with no state.

The issue cites ESPHome, Shelly, LIFX and WLED as precedent for setup that
never fails. Those integrations can do it because each one has a *persisted
device description* to build entities from — ESPHome keeps its entity list in
`.storage`, Shelly caches device info. The pattern is portable; the mechanism
underneath it is the part PR #303 is missing.

## How the implemented version works

Three pieces, plus a gating rule.

### 1. A persisted discovery snapshot

After each successful first cycle, `_async_save_snapshot` banks the
`resources` dict that cycle handed `_run_discovery`, plus the last snapshot's
reps for any href that read missed (see section 2), along with the
pre-narrowing subdevice candidate list and the `DeviceIdentity` read from
`/oic/*`.

Storing the poll input rather than a rendered entity list is the decision
that keeps this honest. `BoundEntity` holds live
`Capability`/`SamsungEntityDescription` objects and isn't serializable, so
the alternative was a parallel format plus a re-resolution path — a second
implementation of discovery that could drift from the real one. Replaying the
input through `_run_discovery` means the same code, the same registry
resolution, and no second source of truth.

Three things ride along because `_run_discovery` reads them off `self`
rather than out of `resources`, and getting them wrong would silently resolve
a *different* registry offline than online, and register a different entity
set on every restart:

- `_identity.device_types` routes `resolve_registry`.
- `self.subdevices` is the candidate list `discover_partitioned` narrows;
  replaying against the already-narrowed list finds no siblings at all.
- `_identity.manufacturer`/`model` feed `device_info`.

It lives in `.storage` (`Store`, keyed on entry_id) rather than on the config
entry: it's device state, not configuration, and runs to tens of kilobytes.
`async_remove_entry` deletes it with the entry.

The write is not `async_delay_save`d. A deferred write outlives whatever
queued it: it lands after `async_remove_entry` has deleted the file and
recreates it orphaned. It runs once per entry load, plus once for each prune
or late-evidence decision, so there's nothing worth deferring. A write that fails is logged and
swallowed — a board reporting something the JSON encoder rejects must not
break polling.

### 2. Grow in place, shrink slowly

The snapshot is a claim about a device we haven't talked to yet. The first
version reloaded the entry whenever the first live discovery disagreed with
it. One read can't tell a lost resource from a short answer, though, and
that design looped: a two-unit AC whose sub-unit alternated between its full
set and three resources reloaded every 30 s, closing the session under each
subscribe burst (#807). An EHS heat pump whose seed came back short was
reconciled down to the smaller set and lost its cycle sensors (#833).

The first live discovery now runs on the live read alone and is added to
what the snapshot registered (`_adopt_live_discovery`):

- An entity the live read binds that wasn't registered is added in place.
  Every platform registers through `entity.async_add_bound_entities`, which
  adds any entity in `coordinator.bound` that `coordinator.registrable()`
  allows, at setup and on each update after.
- An entity registered from the snapshot that the live read didn't bind is
  retained in `bound`, unavailable until its own resource answers
  (`coordinator.provides()`).
- The snapshot keeps the union. An href that never answers is let go only
  once its owner — the main unit, or the sub-unit that owns it — has answered
  `_PRUNE_AFTER_CYCLES` cycles (about two hours) without it, and then only
  from the snapshot: HA shows its entities as no longer provided after the
  next restart. A cycle counts for an owner only if at least half of its
  bound hrefs answered, the bar `read_all` sets for discovery, so a board in
  network standby (handshake fine, most reads timing out) never ages
  anything out. A sub-unit that stays silent keeps its entities
  indefinitely, and a `dynamic_presence` entity's href is never pruned.
- A `late_evidence` gate (the buzzer's vocabulary) follows live data the same
  way: an entity it now includes is added, one it now excludes goes
  unavailable, and the deciding rep is banked for the next start.

The one reload left is a device type that differs from the snapshot's, since
nothing registered for the old type belongs to the appliance now answering.
It happens at most once per entry per run.

### 3. Keep polling with no listeners

`async_setup_entry` holds one listener for the entry's lifetime:

```python
entry.async_on_unload(coordinator.async_add_listener(lambda: None))
```

Registered *before* the first refresh, so scheduling survives a refresh that
fails. This alone fixes the measured "never polls again" bug, and covers a
rehydrated set whose entities are all registry-disabled. Removing the last
listener unschedules the timer, and HA runs `async_on_unload` callbacks when
setup raises, so the setup-retry path doesn't leak a polling coordinator.

### Gating rule: only load offline when there's a snapshot

An entry that has never successfully polled has nothing to restore and keeps
raising `ConfigEntryNotReady`. This is what answers the objection in the PR
thread — with a snapshot we *do* have metadata to build a device from, and
without one HA's backoff is still the right behavior. It also leaves room for
the #168-style flows that need to interact with the device during setup: a
device that never completed setup still blocks.

It also means `async_remove_config_entry_device` is no longer reachable with
an empty `coordinator.subdevices`, so an offline load can't offer to delete a
real-but-unreachable subdevice.

### The coverage-gap Repair stays live-only

`_run_discovery(..., from_snapshot=True)` skips `_update_coverage_gap_issue`.
The Repair points the user at a diagnostics download, which is empty until
the appliance answers, and a device name that drifts between the snapshot and
the live poll would churn the issue for no reason.

Not a de-duplication measure — HA already handles that. `async_create_issue`
is keyed on `(domain, issue_id)`, `dataclasses.replace` in
`async_get_or_create` leaves `dismissed_version` alone, and the registry
reloads non-persistent issues with their dismissal intact, so one row per
entry survives restarts and an "Ignore" sticks.

### What a cycle costs while the appliance stays dark (issue #269)

An appliance switched off at the wall isn't a one-cycle blip: it fails the
same way every 30s for hours, and both halves of that failure were being paid
twice.

`_poll_once` opens the session itself when there isn't one, so a switched-off
appliance fails *in the handshake* — 12s (`DtlsCoapSession.HANDSHAKE_TIMEOUT_S`)
with nothing to show for it. The poll path then treated that like any other
poll failure and ran its reconnect: close the session, pause
`_RECONNECT_PAUSE_S`, poll again. There is no session to close and no
association for the device to clean up, so the "reconnect" was the identical
handshake five seconds later — 29s of the 30s interval spent proving the
appliance is off, twice over, and the same again on every `SETUP_RETRY`
attempt for an entry with no snapshot to load from. `_handshake_failed` marks
that case in `_poll_once` so the poll path can skip the retry; a session that
opened and *then* broke still reconnects within the cycle.

The log was the half the reporters actually saw: `poll failed after
reconnect` at ERROR every cycle, plus a `reconnect_is_frequent` WARNING once
three piled up, for a state this integration is specifically built to sit
through. Issue #269's reporter read that repetition as the integration having
failed. It's one ERROR per outage now, DEBUG for the cycles after it, and one
INFO when the device answers again — HA's own coordinator already logs the
transition into and out of a failed update.

## What this still won't do

Entities will be present and `unavailable` — not showing their last values.
Gate 4 means last-known values require either `RestoreEntity` per platform or
persisting `StateCache`, and both mean asserting state the integration cannot
verify: a washer unplugged for a week would read "Running". HA's convention
is that unreachable means unavailable, and the recorder keeps the history
either way, so long-term statistics and history graphs are unaffected by this
choice.

Worth being explicit about, because it is the gap between what PR #303
promises in the thread ("load their previously recorded states") and what any
correct version can deliver.

## Rejected: zeroconf

The issue's other suggestion — wire zeroconf so the device's own boot
announcement triggers a retry, which is the genuinely idiomatic HA answer —
is a non-starter as things stand: there is no `zeroconf` or `dhcp` key in
`manifest.json` and the config flow is user-driven only, so HA has no
discovery signal for this integration to hang a retry on. It would first need
a confirmed mDNS service on the appliance. Worth revisiting if one turns up;
it would make recovery near-instant instead of within one poll interval.

## Rejected: the cheap version

Keeping `ConfigEntryNotReady` and adding a probe that calls
`async_schedule_reload` on first success would have fixed the recovery *time*
in about twenty lines, with no persistence and no reconcile. It was rejected
because it leaves the device reading as broken for as long as the appliance
is off, which is the half of issue #295 that actually bites — an appliance
switched off at the wall is offline for days, not seconds, and a whole
integration that looks failed for that entire window is the complaint.
