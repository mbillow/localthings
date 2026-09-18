"""Subdevice ("composite device") support for one physical connection
exposing more than one logical indoor subdevice -- issue #177.

Three discovery patterns, unified by the same shape: a logical subdevice is
a seed collection path to poll, plus an href transform between the
canonical href the registry knows (e.g. `/mode/vs/0`) and the actual
on-the-wire href.

- **Pattern A -- indexed siblings** (`ARTIK051_DONGLE_FAC_18K`). `/oic/res`
  lists parallel resource sets by trailing index (`/mode/vs/0`,
  `/mode/vs/1`, ...); each sibling has its own `/device/<n>` Collection.
- **Pattern B -- UUID-prefixed tree** (`TP2X_FAC_BORA_21K`). `/oic/res`
  hides the tree; `/subdevices/vs/0`'s `subdeviceIdList` gives the UUID.
  `GET /<uuid>/device/0` is tried first; when it comes back empty (issue
  #205 -- not even the reference board always exposes it), this falls back
  to probing every href the master answered this cycle individually under
  the UUID prefix (see `Subdevice.flat_hrefs`).
- **Pattern C -- UUID prefix via `/oic/res` only** (`AWM-WW-AID-26-ONEBODY`
  washer+dryer combo, issue #241). No `subdeviceIdList`, `/device/<n>`
  404s; the sibling's UUID only appears as a link prefix in `/oic/res`
  (e.g. the `x.com.samsung.da.multidevice` link) -- treated as Pattern B's
  transform with the UUID sourced from there instead.

A non-empty seed batch is necessary but not sufficient for a candidate to
be a real second subdevice: an unused SmartThings slot (e.g. the Pattern A
reporter's own `/device/2`) answers the same shape with constant/echoed
reps and no live state. Gating on resource shape would need per-family
domain knowledge, so `discover_partitioned` instead gates at the *entity*
layer: a candidate is only materialized if it produces at least one live,
non-`None`, primary (no `entity_category`), non-meter bound entity. The
meter exclusion (issue #214) covers a second failure mode: an unused slot
reporting a populated whole-appliance energy counter, which is the
appliance's own bookkeeping, not evidence of a second indoor unit -- see
`_has_live_primary_entity`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import cbor2

from .batch import parse_device0_batch
from .by_type._base import DeviceRegistry

_INDEXED_HREF_RE = re.compile(r"^/device/(\d+)$")

# A UUID as the first path segment of an /oic/res link href -- Pattern C's
# discovery signal (issue #241).
_UUID_PREFIX_RE = re.compile(r"^/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})/")

# Speculative /device/<n> siblings probed when /oic/res doesn't reveal a
# second subdevice's Collection (moved here from identity.py, issue #177,
# since the old read_identity fired these on every _connect_session
# including reconnects, when enumeration only needs to run once). A plain
# tolerated-404 RETRIEVE, not the kind of guess the write-contract
# 'don't guess' rule is about. Widen only if a board needs more siblings.
_SPECULATIVE_DEVICE_INDICES = (1, 2)


@dataclass(frozen=True)
class Subdevice:
    """One logical indoor subdevice reachable over a single physical
    connection.

    `kind='main'` is the subdevice this config entry actually connects to
    and always exists (see MAIN below) -- its `to_actual`/`to_canonical`
    are the identity transform, so a single-subdevice device behaves
    exactly as before this module existed. `'indexed'`/`'prefixed'` are
    Pattern A/B above; `key` is the trailing index string ('1', '2', ...)
    or the full subdevice UUID, and `seed_path` is the Collection href (as
    path segments) whose batch enumerates/refreshes that subdevice.

    `flat_hrefs` is non-empty only for a 'prefixed' subdevice with no
    Collection at `seed_path` (issue #205). When set, `seed_path` is
    meaningless (left as `()`) and this subdevice's state comes from
    GETting each of these canonical hrefs individually under its prefix
    instead -- see enumerate_subdevices' fallback and
    coordinator._poll_subdevice_seed.
    """

    kind: str  # 'main' | 'indexed' | 'prefixed'
    key: str  # ''    | '1'       | '6c2dff6d-ee5c-dad1-6a5e-000000000001'
    seed_path: tuple[str, ...]
    flat_hrefs: tuple[str, ...] = ()

    def to_actual(self, canonical: str) -> str:
        """Canonical registry href (e.g. '/mode/vs/0') -> the real,
        on-the-wire href for this subdevice."""
        if self.kind == "indexed":
            head, sep, tail = canonical.rpartition("/")
            # Only the index-0 trailing segment is ours to rewrite -- not a
            # "replace any trailing digit" rule, which would misread a
            # genuine multi-instance resource (e.g. the fridge's
            # '/door/vs/1') as a subdevice's. No registry declares a
            # non-zero trailing index today.
            if tail == "0":
                return f"{head}{sep}{self.key}"
            return canonical
        if self.kind == "prefixed":
            return f"/{self.key}{canonical}"
        return canonical

    def to_canonical(self, actual: str) -> str | None:
        """Inverse of to_actual, or None when `actual` isn't this subdevice's."""
        if self.kind == "indexed":
            head, sep, tail = actual.rpartition("/")
            if tail == self.key:
                return f"{head}{sep}0"
            return None
        if self.kind == "prefixed":
            prefix = f"/{self.key}"
            if actual.startswith(prefix + "/"):
                return actual[len(prefix) :]
            return None
        return actual

    def owns(self, actual: str) -> bool:
        """True if `actual` belongs to this subdevice's namespace. MAIN
        never "owns" anything by this definition -- it gets whatever's
        left after every other subdevice's hrefs are excluded (see
        canonical_view)."""
        if self.kind == "main":
            return False
        return self.to_canonical(actual) is not None

    @property
    def key_prefix(self) -> str:
        """Prefix guaranteeing a unique entity key/unique_id (see
        adapter._key). '' for MAIN, so the master's flattened state keys
        stay byte-identical to every device shipped before issue #177. The
        full subdevice UUID is used verbatim (non-alphanumerics stripped)
        rather than an enumeration-order ordinal, since it's device-reported
        and stable across reconnects; it never appears in a user-visible
        string, since HA derives entity_id from device+entity name, not
        unique_id.
        """
        if self.kind == "indexed":
            return f"subdevice{self.key}_"
        if self.kind == "prefixed":
            slug = re.sub(r"[^a-zA-Z0-9]", "", self.key)
            return f"subdevice_{slug}_"
        return ""


MAIN = Subdevice(kind="main", key="", seed_path=("device", "0"))


def canonical_view(
    subdevice: Subdevice,
    resources: dict[str, dict],
    subdevices: list[Subdevice],
) -> dict[str, dict]:
    """Rewrite `resources` (real, on-the-wire hrefs) into `subdevice`'s own
    canonical namespace -- what discover()/exists_fn/rep_fn/is_legacy_board
    and friends are written against.

    For MAIN this is the snapshot minus every href owned by one of the
    other subdevices in `subdevices` -- otherwise a sibling's own
    `/mode/vs/1` would leak into the master's view under the canonical key
    ('/mode/vs/0') the master's own resource also maps to. For an
    indexed/prefixed subdevice it's the reverse: only the hrefs that
    subdevice owns, rewritten back through `to_canonical`.

    `subdevices` may or may not include MAIN itself -- MAIN.owns() is
    always False, so including it is harmless.
    """
    if subdevice.kind == "main":
        owned_elsewhere = {href for href in resources if any(su.owns(href) for su in subdevices)}
        return {h: r for h, r in resources.items() if h not in owned_elsewhere}
    return {
        canon: resources[actual]
        for actual in resources
        if (canon := subdevice.to_canonical(actual)) is not None
    }


def normalize_seed_batch(subdevice: Subdevice, batch: dict[str, dict]) -> dict[str, dict]:
    """Real, on-the-wire hrefs from one subdevice's seed-collection batch,
    normalized so every href actually carries this subdevice's prefix/index.

    Indexed subdevices need no change -- the device echoes the real `/x/<n>`
    href in its own `/device/<n>` batch. A prefixed subdevice's batch
    entries may or may not already carry the `/<id>` prefix (unconfirmed),
    so it's added when missing.
    """
    if subdevice.kind != "prefixed":
        return batch
    prefix = f"/{subdevice.key}"
    return {
        (href if href.startswith(prefix + "/") else f"{prefix}{href}"): rep
        for href, rep in batch.items()
    }


def _iter_oic_res_hrefs(oic_res):
    """Flatten /oic/res's raw shape into a flat iterable of link dicts.

    Both captured dumps group links by `di` (`[{'di': ..., 'links': [...]}]`
    -- see identity.py's read_identity/_get_links), so that's the shape
    handled here. Tolerant of a flat link-list too, and of anything else by
    yielding nothing.
    """
    for entry in oic_res or []:
        if not isinstance(entry, dict):
            continue
        if "links" in entry:
            for link in entry.get("links") or []:
                if isinstance(link, dict):
                    yield link
        elif "href" in entry:
            yield entry


def _seed_href(path_segs: tuple[str, ...]) -> str:
    """('device', '1') -> '/device/1' -- the leading-slash href form
    `probe_log` and diagnostics report, built from the path-segment form
    `sess.get` takes."""
    return "/" + "/".join(path_segs)


def _get_raw(sess, path_segs: tuple[str, ...], timeout: float = 10.0):
    """GET `path_segs` and CBOR-decode the payload, or None on any
    missing/malformed response (a 4.04, a timeout, an empty payload) --
    shared tolerated-absence posture for both callers below."""
    try:
        code, pl = sess.get(list(path_segs), timeout=timeout)
        if code == 0x45 and pl:
            return cbor2.loads(pl)
    except Exception:
        pass
    return None


def _get_batch(
    sess,
    path_segs: tuple[str, ...],
    timeout: float = 10.0,
) -> dict[str, dict]:
    """GET a Samsung Collection resource and parse it the same way
    /device/0 itself is parsed (parse_device0_batch): a [devcol-rep,
    {href, rep}, ...] CBOR list, not a bare Property map."""
    body = _get_raw(sess, path_segs, timeout)
    return parse_device0_batch(body) if isinstance(body, list) else {}


def _get_property(
    sess,
    path_segs: tuple[str, ...],
    timeout: float = 10.0,
) -> dict:
    """GET a plain OCF Property-map resource (a bare dict, not a Collection
    batch). Used for `/multidevice/vs/0` (issue #177 follow-up): listed in
    `/oic/res` on the Pattern A reporter's board but absent from
    `/device/0`'s batch, so it needs its own RETRIEVE, and it answers a
    single Property map, not a [devcol-rep, ...] list."""
    body = _get_raw(sess, path_segs, timeout)
    return body if isinstance(body, dict) else {}


def enumerate_subdevices(
    sess,
    resources: dict[str, dict],
    oic_res_links,
    probe_log: Callable[[str, bool], None] | None = None,
    *,
    preferred_hrefs: Sequence[str] = (),
    time_budget: float | None = None,
    collection_timeout: float = 10.0,
    property_timeout: float = 10.0,
) -> tuple[list[Subdevice], dict[str, dict]]:
    """Discover every sibling indoor subdevice reachable over `sess`'s
    connection.

    Runs once, at first discovery, in an executor, under the coordinator's
    session lock -- every GET here is a plain RETRIEVE. Returns the
    *candidate* subdevices and the resources already fetched while probing
    them (normalized to real hrefs), so the coordinator's first discovery
    poll doesn't need to re-poll them.

    `probe_log(seed_href, found)` fires for every seed attempted, whether
    or not it answered, so diagnostics can tell "checked, nothing there"
    apart from "never checked".

    `preferred_hrefs` only changes the order of the flat Property fallback;
    it never filters the device's resource surface. When `time_budget` is
    supplied, probes are bounded by one shared monotonic deadline and this
    returns every candidate/resource confirmed before it. This makes first
    setup finite even when firmware silently drops unknown paths instead of
    returning 4.04.

    Every candidate whose seed answers with a non-empty batch is returned
    here -- this function can't tell a real sibling from an unused
    SmartThings slot that answers the same shape; that requires
    discovering+flattening the candidate's own entities first, which is
    `discover_partitioned`'s job. See this module's docstring.
    """
    subdevices: list[Subdevice] = []
    fetched: dict[str, dict] = {}
    # Case-insensitive -- the same UUID can reach here once from
    # subdeviceIdList and once from an /oic/res link prefix with different
    # casing, and probing it twice would materialize the same physical
    # subdevice as two Subdevice candidates.
    probed_ids: set[str] = set()
    deadline = time.monotonic() + max(0.0, time_budget) if time_budget is not None else None
    budget_exhausted = False

    def _next_timeout(maximum: float) -> float | None:
        """Clamp one probe to the remaining enumeration wall-clock budget."""
        nonlocal budget_exhausted
        if deadline is None:
            return maximum
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            budget_exhausted = True
            return None
        return min(maximum, remaining)

    def _flat_probe_hrefs():
        """Preferred live-state hrefs first, then every remaining master href."""
        seen = set()
        for href in preferred_hrefs:
            if href in resources and href not in seen:
                seen.add(href)
                yield href
        for href in sorted(resources):
            if href not in seen:
                yield href

    def _probed(seed_href: str, batch: dict) -> None:
        if probe_log is not None:
            probe_log(seed_href, bool(batch))

    def _probe_prefixed(sub_id: str) -> None:
        """Materialize one UUID-prefixed subdevice candidate -- shared by
        Pattern B (ids from subdeviceIdList) and Pattern C (ids from
        /oic/res link prefixes) below, which differ only in where the UUID
        came from."""
        if sub_id.lower() in probed_ids:
            return
        probed_ids.add(sub_id.lower())

        priority_hrefs = tuple(dict.fromkeys(href for href in preferred_hrefs if href in resources))
        ordered_hrefs = tuple(_flat_probe_hrefs())
        flat_hrefs = []
        attempted_hrefs: set[str] = set()

        def _probe_flat(href: str) -> bool:
            """Probe one prefixed Property; return whether it was attempted."""
            timeout = _next_timeout(property_timeout)
            if timeout is None:
                return False
            if attempted_hrefs:
                sess.pace()
            attempted_hrefs.add(href)
            actual = f"/{sub_id}{href}"
            rep = _get_property(sess, tuple(actual.strip("/").split("/")), timeout)
            _probed(actual, rep)
            if rep:
                flat_hrefs.append(href)
                fetched[actual] = rep
            return True

        # A live identity leaf proves this UUID's Property namespace routes.
        # If its highest-priority operational leaf also answers, read the rest
        # of the registry-selected hot/warm and primary leaves before spending
        # most of the shared deadline on a Collection that some firmware does
        # not expose. Composite entities need those sibling resources even
        # though they do not bind entities of their own (power, target
        # temperature, wind, ...). If the Collection works below, its batch
        # remains authoritative and these preflight reads are discarded.
        if priority_hrefs and priority_hrefs[0] == "/information/vs/0":
            _probe_flat(priority_hrefs[0])
            if flat_hrefs and len(priority_hrefs) > 1:
                _probe_flat(priority_hrefs[1])
                if len(flat_hrefs) == 2:
                    for href in priority_hrefs[2:]:
                        if not _probe_flat(href):
                            break

        seed = (sub_id, "device", "0")
        timeout = _next_timeout(collection_timeout)
        batch = {}
        if timeout is not None:
            if attempted_hrefs:
                sess.pace()
            batch = _get_batch(sess, seed, timeout)
            _probed(_seed_href(seed), batch)
        if batch:
            subdevice = Subdevice(kind="prefixed", key=sub_id, seed_path=seed)
            for href in flat_hrefs:
                fetched.pop(f"/{sub_id}{href}", None)
            fetched.update(normalize_seed_batch(subdevice, batch))
            subdevices.append(subdevice)
            return
        # Fallback (issue #205): even the reference TP2X_FAC_BORA_21K board
        # doesn't always expose its own `/<uuid>/device/0` Collection. With
        # no Collection to seed from and no per-UUID entry in /oic/res to
        # enumerate hrefs from, the only signal left is that a composite
        # device's siblings are the same physical board family as the
        # subdevice this config entry already talks to -- so probe every
        # href the master itself answered this cycle, individually, under
        # this UUID's prefix, and keep whichever ones answer before the
        # optional enumeration deadline. Each is a plain tolerated-404
        # RETRIEVE, same posture as every other probe in this function.
        #
        # Known gap: a firmware that echoes the master's own state back
        # under an unrecognized prefix, rather than 4.04ing, would pass
        # every probe here and could materialize a phantom duplicate. Every
        # board seen so far genuinely 4.04s on paths it doesn't own (issue
        # #205's unit answered only 1 of 31 probes), so this hasn't been
        # guarded against -- the fix would compare a candidate's confirmed
        # reps against the master's own values for the same hrefs.
        for href in ordered_hrefs:
            if href in attempted_hrefs:
                continue
            if not _probe_flat(href):
                break
        if not flat_hrefs:
            return
        subdevices.append(
            Subdevice(
                kind="prefixed",
                key=sub_id,
                seed_path=(),
                flat_hrefs=tuple(flat_hrefs),
            )
        )

    # --- Pattern B: UUID-prefixed tree (TP2X_FAC_BORA_21K) ------------------
    raw_ids = (resources.get("/subdevices/vs/0") or {}).get("x.com.samsung.da.subdeviceIdList")
    # Tolerate anything but a list of strings -- this field is
    # redaction-prone (matches redact.py's 'deviceid' rule) and a shipped
    # fixture carries the literal string 'REDACTED' there. That must yield
    # zero subdevices, not a crash -- issue #177 is additive and must never
    # break an already-working single-climate-entity device.
    ids = raw_ids if isinstance(raw_ids, list) else []
    listed = sorted(i for i in ids if isinstance(i, str) and i)
    for sub_id in listed:
        _probe_prefixed(sub_id)
        if budget_exhausted:
            break

    # --- Pattern C: UUID prefix advertised only via /oic/res ----------------
    # (AWM-WW-AID-26-ONEBODY washer+dryer combo, issue #241.) No
    # /subdevices/vs/0 and /device/<n> 404s; the only trace of the sibling
    # is a UUID-prefixed link in /oic/res (the x.com.samsung.da.multidevice
    # link). Its own tree answers a full Collection at /<uuid>/device/0,
    # exactly Pattern B's transform, so a UUID prefix attached to that
    # resource type is treated as a candidate. Other UUID-prefixed links are
    # not evidence of a sibling: some single-unit AC boards advertise only
    # per-prefix file-transfer resources, and probing those prefixes against
    # every master href needlessly burns the setup timeout budget.
    # _probe_prefixed's probed_ids
    # guard (not a set difference against `listed`) is what keeps an id
    # already named by subdeviceIdList from being probed twice, since the
    # two sources can disagree on case.
    linked = sorted(
        {
            m.group(1)
            for link in _iter_oic_res_hrefs(oic_res_links)
            for m in [_UUID_PREFIX_RE.match(link.get("href", ""))]
            if m and "x.com.samsung.da.multidevice" in (link.get("rt") or ())
        }
    )
    for sub_id in linked:
        _probe_prefixed(sub_id)
        if budget_exhausted:
            break

    # --- Pattern A: indexed siblings (ARTIK051_DONGLE_FAC_18K) --------------
    indices = sorted(
        {
            int(m.group(1))
            for link in _iter_oic_res_hrefs(oic_res_links)
            for m in [_INDEXED_HREF_RE.match(link.get("href", ""))]
            if m and int(m.group(1)) >= 1
        }
    )
    if not indices:
        # A board that hides its whole tree from /oic/res gives us nothing
        # to enumerate from -- fall back to the bounded speculative probe
        # this replaces from identity.py.
        indices = list(_SPECULATIVE_DEVICE_INDICES)
    for n in indices:
        timeout = _next_timeout(collection_timeout)
        if timeout is None:
            break
        seed = ("device", str(n))
        batch = _get_batch(sess, seed, timeout)
        _probed(_seed_href(seed), batch)
        if not batch:
            continue
        subdevice = Subdevice(kind="indexed", key=str(n), seed_path=seed)
        fetched.update(batch)  # already real /x/<n> hrefs, no normalization needed
        subdevices.append(subdevice)

    # /multidevice/vs/0 (issue #177 follow-up): the Pattern A reporter's
    # board lists it in /oic/res but it never appears in /device/0's batch,
    # so it needs its own RETRIEVE. It's a plain corroborating count
    # (x.com.samsung.da.numofsubdevice), confirmed read-only (a write
    # attempt returned CoAP 4.00) -- captured for diagnostics only, folded
    # into the merged resources dict like any other href (see
    # airconditioner._AC_IGNORED, which is what keeps it from surfacing as
    # an unbound-href gap). NOT a gate: discover_partitioned's entity-level
    # liveness check decides materialization correctly without it, and only
    # this one board family is known to expose it at all. Whether it agrees
    # with the number of subdevices actually materialized is the
    # coordinator's call to log (it owns the logger; this module doesn't),
    # not this function's.
    multidevice_seed = ("multidevice", "vs", "0")
    timeout = _next_timeout(property_timeout)
    if timeout is not None:
        multidevice = _get_property(sess, multidevice_seed, timeout)
        _probed(_seed_href(multidevice_seed), multidevice)
        if multidevice:
            fetched["/multidevice/vs/0"] = multidevice

    return subdevices, fetched


@dataclass(frozen=True)
class SkippedSubdevice:
    """A candidate `enumerate_subdevices` found whose seed answered, but
    that `discover_partitioned`'s entity-level liveness gate rejected -- an
    unused SmartThings slot, not a real second subdevice. Kept around so a
    caller can log/report what was skipped and why."""

    subdevice: Subdevice
    hrefs: tuple[str, ...]


# Sensor kinds whose value is a running total the appliance keeps rather
# than a reading of the subdevice's own hardware -- excluded from the
# liveness gate below (issue #214). HA's running-total state classes cover
# most of them; the consumption device classes catch the rest (a descriptor
# may deliberately declare no state_class, e.g. common.ENERGY_METER's
# monthly totals that reset at each billing boundary).
_METER_STATE_CLASSES = frozenset({"total", "total_increasing"})
_METER_DEVICE_CLASSES = frozenset({"energy", "water", "gas"})


def _is_meter(desc) -> bool:
    """True for a cumulative consumption/counter descriptor -- see the two
    constants above. Only SensorDesc carries either attribute; everything
    else answers False through the getattr defaults."""
    return (
        getattr(desc, "state_class", None) in _METER_STATE_CLASSES
        or getattr(desc, "device_class", None) in _METER_DEVICE_CLASSES
    )


def _has_live_primary_entity(bound, state: dict) -> bool:
    """True if flattening `bound` (one candidate subdevice's BoundEntity
    list) produced at least one non-`None` value for a primary entity
    (`entity_category` unset) that isn't a cumulative meter (`_is_meter`).

    This is the materialization gate itself (see this module's docstring).
    Two exclusions, both because the question this answers is "is a
    physical subdevice installed at this slot?", and neither kind of value
    can speak to it:

    - **Non-primary entities.** An unused slot can still flatten to a
      diagnostic-category value derived from an empty resource (e.g. a
      formatted `alarm_code` off an empty `/alarms/vs/2`) -- that proves
      nothing about whether hardware is there.
    - **Cumulative meters** (issue #214). An unused slot has been seen
      reporting a populated whole-appliance `cumulativePower` while every
      operational rep on it is empty `{}`. A single-split AC has one
      compressor and one energy meter, so a whole-appliance total showing
      up under a second index is the appliance's own bookkeeping, not
      evidence of a second indoor unit. A genuinely installed subdevice
      reports its own operational state too, and that is what still passes
      this gate.
    """
    from .adapter import _key  # see discover_partitioned's deferred-import note

    return any(
        not b.desc.entity_category and not _is_meter(b.desc) and state.get(_key(b)) is not None
        for b in bound
    )


def discover_partitioned(
    resources: dict[str, dict],
    subdevices: list[Subdevice],
    resolve_registry: Callable[..., DeviceRegistry | None],
    fallback_capabilities: dict,
    log: Callable[[str], None] | None = None,
    tier_log: Callable[[str, str], None] | None = None,
    oic_device_types: Sequence[str] = (),
):
    """Bind every href in `resources` (the merged, real-href snapshot --
    main plus every enumerated subdevice's seed) to entities, partitioned
    by which subdevice owns it.

    Main pass runs over hrefs owned by no subdevice -- otherwise every
    `/mode/vs/1` would land in `unbound_hrefs` too and raise a spurious
    coverage-gap repair. Then one pass per candidate subdevice over its own
    canonical view, resolving that subdevice's own device type from its own
    `/information/vs/0` when it reports one, falling back to the master's
    registry otherwise -- a sibling that fails to answer its own identity
    resource is still treated as the same appliance type as the master.

    Each candidate is discovered and flattened twice: once silently to
    evaluate `_has_live_primary_entity`, and, only if that passes, a second
    time with `log`/`tier_log` wired so its coverage gaps and poll tiers
    actually count. A candidate that fails the gate contributes nothing at
    all, as if it had never answered its seed.

    `oic_device_types` (from the master's own `/oic/d`) is passed only to
    the master's resolution -- subdevices resolve from their own
    `/information/vs/0` or fall back to the master's whole registry, and
    blindly applying the master's OCF device type to every subdevice would
    be wrong the moment a composite appliance pairs two different device
    types under one connection.

    Returns `(bound, device_type_name, materialized, skipped)`:
    - `bound`: the concatenated BoundEntity list (main + every materialized
      subdevice).
    - `device_type_name`: the master's resolved device type (used for
      logging/device naming; each subdevice's own resolved type only
      affects which capabilities bind its hrefs).
    - `materialized`: the subset of `subdevices` that passed the gate, in
      the same order -- what the caller should keep as its live subdevice
      roster going forward (poll seeds, canonical_resources,
      device_info_for, ...).
    - `skipped`: `SkippedSubdevice` entries for every candidate that didn't.
    """
    # Deferred import: discovery.py imports Subdevice/MAIN from this module
    # at module scope, so importing discover() back here at module scope
    # would be circular. By the time this function runs both modules are
    # fully loaded; adapter.py imports discovery.py, so the same applies to
    # flatten()/_key().
    from .adapter import flatten
    from .discovery import discover

    # Same computation canonical_view does for MAIN -- reuse it rather than
    # re-deriving owned_elsewhere here too.
    main_view = canonical_view(MAIN, resources, subdevices)

    reg = resolve_registry(main_view, device_types=oic_device_types)
    caps, pats = (
        (reg.capabilities, reg.pattern_capabilities)
        if reg is not None
        else (fallback_capabilities, [])
    )
    # MAIN is never gated -- the config entry's own physical connection
    # always materializes regardless of what its entities' values are.
    bound = discover(main_view, caps, pats, log=log, tier_log=tier_log, subdevice=MAIN)
    device_type_name = reg.name if reg is not None else None

    materialized: list[Subdevice] = []
    skipped: list[SkippedSubdevice] = []

    for su in subdevices:
        view = canonical_view(su, resources, subdevices)
        su_reg = resolve_registry(view) or reg
        su_caps, su_pats = (
            (su_reg.capabilities, su_reg.pattern_capabilities)
            if su_reg is not None
            else (fallback_capabilities, [])
        )
        probe_bound = discover(view, su_caps, su_pats, subdevice=su)
        probe_state = flatten(probe_bound, resources)
        if _has_live_primary_entity(probe_bound, probe_state):
            materialized.append(su)
            bound = bound + discover(
                view,
                su_caps,
                su_pats,
                log=log,
                tier_log=tier_log,
                subdevice=su,
            )
        else:
            skipped.append(
                SkippedSubdevice(
                    subdevice=su,
                    hrefs=tuple(sorted({b.href for b in probe_bound})),
                )
            )

    return bound, device_type_name, materialized, skipped
