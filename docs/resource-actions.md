# Reading and writing resources directly

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

The **Debug write** panel under a device's **Configure** menu is the friendlier single-write path over this same machinery — pick an href, type a payload, see the result — for when you don't need a sequence.
