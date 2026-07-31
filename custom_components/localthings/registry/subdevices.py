"""Subdevice ("composite device") support for one physical connection exposing
more than one logical indoor subdevice -- issue #177.

Two reporters, two different board families, two genuinely different
mechanisms for exposing a second indoor subdevice over one IP / one DTLS
session (see DESIGN-177.md section 1 for the full evidence trail; the two
diagnostics dumps this was built against come from the Pattern A and
Pattern B reporters, respectively -- they each filed one of the two
reports this module unifies):

Pattern A -- indexed siblings (`ARTIK051_DONGLE_FAC_18K`, that reporter's
board). `/oic/res` lists three complete parallel resource sets whose
trailing path segment is the index (`/mode/vs/0`, `/mode/vs/1`,
`/mode/vs/2`, ... on both OCF-standard and vendor hrefs), and `/device/0`'s
batch carries only the index-0 hrefs -- the sibling subdevices are
reachable only via their own `/device/<n>` collection.

Pattern B -- UUID-prefixed tree (`TP2X_FAC_BORA_21K`, that reporter's board).
`/oic/res` hides the whole appliance tree; `/device/0`'s batch instead
carries `x.com.samsung.da.subdeviceIdList` on `/subdevices/vs/0`, and that
same UUID appears as a literal href prefix in `/oic/res`
(`/<uuid>/file/list/vs/0`, ...). What's actually been confirmed live on
that reporter's unit is narrower than early issue #177 writeups suggested: a
single individual `GET /<uuid>/information/vs/0` was read by hand through
the debug panel and came back carrying a different model/serial than the
master (`TP2X_FAC_BORA_RAC_21K`, the wall-mounted subdevice, vs. the
master's `TP2X_FAC_BORA_21K`, the floor subdevice) -- real evidence a
second subdevice exists at that prefix, but not evidence that `GET
/<uuid>/device/0` (the Collection batch PR #199 built this pattern's seed
around) itself returns anything. Issue #205, the same unit on a later
version, is that assumption failing: `/<uuid>/device/0` comes back empty.
So `enumerate_subdevices` tries it first (a future board might genuinely
expose it) and falls back, when it's empty, to probing every href the
master itself answered this cycle individually under the UUID prefix --
the only thing ever actually confirmed to work for this pattern -- on the
assumption that a composite device's siblings share the master's resource
surface. See `Subdevice.flat_hrefs`.

Pattern C -- UUID prefix advertised only via `/oic/res`
(`AWM-WW-AID-26-ONEBODY` washer+dryer combo, issue #241). The board answers
`numofsubdevice='2'` on `/multidevice/vs/0` but has no `/subdevices/vs/0`
(no `subdeviceIdList`) and 4.04s `/device/1`/`/device/2`; the washer
subdevice's UUID appears nowhere except as the path prefix of the
`x.com.samsung.da.multidevice` link in `/oic/res`, and
`GET /<uuid>/device/0` answers the washer's own full Collection batch
(model `..._WF80H` vs. the master's `..._DV80H27H`) -- Pattern B's
transform with the UUID sourced from the link prefix instead of
`subdeviceIdList`.

All of these are "the same thing wearing different clothes": a logical subdevice is a
seed collection path to poll, plus an href transform between the canonical
href the registry knows (`/mode/vs/0`) and the actual on-the-wire href. The
detection signals don't overlap on either captured board (the Pattern A
reporter's has no `/subdevices/vs/0` at all; the Pattern B reporter's has
no `/device/1`), so no disambiguation logic is needed --
`enumerate_subdevices` checks both and materializes any candidate whose
seed answers with a non-empty batch.

A non-empty seed batch is necessary but not sufficient for the *candidate*
to actually be a live second subdevice, though: the Pattern A reporter's
own board also has a `/device/2` -- a third, unused SmartThings slot --
that answers with the exact same 14-href shape as the real `/device/1`
sibling, populated with three constant/echoed/shape-only reps (a region
code identical to every other subdevice's, an /information rep echoing the
*same* model string as subdevice 1, and a /temperatures items[] entry with
an id/description but no current/desired/minimum/maximum reading) and
nothing resembling live climate state. Gating on *resource* shape/hrefs
turned out to be the wrong layer -- it would need per-family domain
knowledge (which hrefs mean "in use" for a washer's second drum, a
fridge's second compartment, ...) baked into a registry field before any
of those families could use this module at all. `discover_partitioned`
instead gates at the *entity* layer, after discovery+flattening: a
candidate is only kept if it produced at least one *primary* (no
`entity_category`), non-meter bound entity whose flattened value isn't
`None` -- e.g. the Pattern A reporter's /device/2 does flatten to an
`alarm_code` value, but that entity is diagnostic-category and derived from
an empty /alarms/vs/2, so it doesn't count. This reuses the same
primary/config/diagnostic taxonomy every registry already declares (see the
adding-device-support skill's entity-taxonomy section) instead of adding a
second, parallel domain-knowledge mechanism.

The meter carve-out is issue #214, and it's the same "an unused slot still
answers *something*" problem one layer further in: that reporter's
single-split ARTIK051_KRAC_18K has a /device/1 whose operational reps are
all empty {} -- the /device/2 shape above -- but which also reports a
populated /energy/consumption/vs/1, a whole-appliance lifetime kWh counter
that materialized the slot as a phantom second air conditioner. See
`_has_live_primary_entity`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import cbor2

from .batch import parse_device0_batch

_INDEXED_HREF_RE = re.compile(r'^/device/(\d+)$')

# A UUID as the first path segment of an /oic/res link href -- Pattern C's
# discovery signal (issue #241): a subdevice tree whose UUID is advertised
# nowhere except as this prefix (no subdeviceIdList, no /device/<n>).
_UUID_PREFIX_RE = re.compile(
    r'^/([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})/')

# Speculative /device/<n> siblings probed when /oic/res doesn't reveal a
# second logical subdevice's Collection on this board (moved here from
# identity.py, issue #177 -- see enumerate_subdevices' docstring for why: the
# old read_identity fired these two extra RETRIEVEs on *every* _connect_session,
# including every reconnect, for information enumeration only needs once).
# Same bound as before: a plain, tolerated-404 RETRIEVE, not the kind of
# guess the write-contract 'don't guess' rule is about. Widen only if a real
# board ever turns out to need more than two siblings.
_SPECULATIVE_DEVICE_INDICES = (1, 2)


@dataclass(frozen=True)
class Subdevice:
    """One logical indoor subdevice reachable over a single physical
    connection.

    `kind='main'` is the subdevice this config entry actually connects to and
    always exists (see MAIN below) -- its `to_actual`/`to_canonical` are the
    identity transform, so every existing single-subdevice device keeps
    behaving exactly as it did before this module existed. `'indexed'`/
    `'prefixed'` are Pattern A/B above; `key` is the trailing index string
    ('1', '2', ...) or the full subdevice UUID, and `seed_path` is the
    Collection href (as path segments) whose batch response
    enumerates/refreshes that subdevice.

    `flat_hrefs` is non-empty only for a 'prefixed' subdevice that doesn't
    expose its own Collection at `seed_path` (issue #205 -- not even
    TP2X_FAC_BORA_21K, the board this pattern was built against, always
    does). When set, `seed_path` is meaningless (left as `()`) and this
    subdevice's state comes from GETting each of these canonical hrefs
    individually under its prefix instead of one Collection batch -- see
    enumerate_subdevices' fallback and coordinator._poll_subdevice_seed.
    """
    kind: str            # 'main' | 'indexed' | 'prefixed'
    key: str             # ''    | '1'       | '6c2dff6d-ee5c-dad1-6a5e-000000000001'
    seed_path: tuple[str, ...]
    flat_hrefs: tuple[str, ...] = ()

    def to_actual(self, canonical: str) -> str:
        """Canonical registry href (e.g. '/mode/vs/0') -> the real,
        on-the-wire href for this subdevice."""
        if self.kind == 'indexed':
            head, sep, tail = canonical.rpartition('/')
            # Only the index-0 trailing segment is ours to rewrite --
            # deliberately not a "replace any trailing digit" rule, which
            # would misread a genuine multi-instance resource (the fridge's
            # pattern-cap hrefs, e.g. '/door/vs/1') as a subdevice's. No
            # registry declares a non-zero trailing index today and no
            # fixture in the corpus contains one (verified across the whole
            # corpus), so the strict rule costs nothing.
            if tail == '0':
                return f'{head}{sep}{self.key}'
            return canonical
        if self.kind == 'prefixed':
            return f'/{self.key}{canonical}'
        return canonical

    def to_canonical(self, actual: str) -> Optional[str]:
        """Inverse of to_actual, or None when `actual` isn't this subdevice's."""
        if self.kind == 'indexed':
            head, sep, tail = actual.rpartition('/')
            if tail == self.key:
                return f'{head}{sep}0'
            return None
        if self.kind == 'prefixed':
            prefix = f'/{self.key}'
            if actual.startswith(prefix + '/'):
                return actual[len(prefix):]
            return None
        return actual

    def owns(self, actual: str) -> bool:
        """True if `actual` belongs to this subdevice's namespace. MAIN never
        "owns" anything by this definition -- it gets whatever's left after
        every other subdevice's hrefs are excluded (see canonical_view)."""
        if self.kind == 'main':
            return False
        return self.to_canonical(actual) is not None

    @property
    def key_prefix(self) -> str:
        """Prefix that guarantees a unique entity key/unique_id (see
        adapter._key). '' for MAIN -- the master's flattened state
        keys must stay byte-identical to every device this integration
        shipped before issue #177, so no golden file changes. The full
        subdevice UUID is used verbatim (non-alphanumerics stripped, not
        truncated or replaced with an ordinal) because it's device-reported
        and stable across reconnects/restarts, unlike an ordinal assigned by
        enumeration order -- and it never appears in a user-visible string
        (see DESIGN-177.md section 6): HA derives the visible entity_id from
        the device name + entity name, not from unique_id.
        """
        if self.kind == 'indexed':
            return f'subdevice{self.key}_'
        if self.kind == 'prefixed':
            slug = re.sub(r'[^a-zA-Z0-9]', '', self.key)
            return f'subdevice_{slug}_'
        return ''


MAIN = Subdevice(kind='main', key='', seed_path=('device', '0'))


def canonical_view(
    subdevice: Subdevice, resources: dict[str, dict], subdevices: list['Subdevice'],
) -> dict[str, dict]:
    """Rewrite `resources` (real, on-the-wire hrefs) into `subdevice`'s own
    canonical namespace -- what discover()/exists_fn/rep_fn/is_legacy_board
    and friends are written against.

    For MAIN this is the snapshot *minus* every href owned by one of the
    other subdevices in `subdevices` -- otherwise a sibling's own `/mode/vs/1`
    would leak into the master's view under the same canonical key
    ('/mode/vs/0') that the master's actual `/mode/vs/0` also maps to,
    silently mixing two subdevices' state together. For an indexed/prefixed
    subdevice it's the reverse: only the hrefs that subdevice owns, rewritten
    back through `to_canonical`.

    `subdevices` may or may not include MAIN itself -- MAIN.owns() is always
    False, so including it is harmless.
    """
    if subdevice.kind == 'main':
        owned_elsewhere = {
            href for href in resources
            if any(su.owns(href) for su in subdevices)
        }
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
    href in its own `/device/<n>` batch (confirmed against the Pattern A
    reporter's dump). A prefixed subdevice's batch entries may or may not
    already carry the `/<id>` prefix (unconfirmed which -- the Pattern B
    reporter's board was never probed live before the subdevice id was
    known), so it's added when missing.
    """
    if subdevice.kind != 'prefixed':
        return batch
    prefix = f'/{subdevice.key}'
    return {
        (href if href.startswith(prefix + '/') else f'{prefix}{href}'): rep
        for href, rep in batch.items()
    }


def _iter_oic_res_hrefs(oic_res):
    """Flatten /oic/res's raw shape into a flat iterable of link dicts.

    Both captured dumps group links by `di` (`[{'di': ..., 'links': [...]}]`
    -- see identity.py's read_identity/_get_links), so that's the shape
    handled here. Tolerant of a flat link-list too (nothing in the OCF spec
    rules it out, and _get_links' own posture already treats any list-shaped
    body as possible) and of anything else by yielding nothing.
    """
    for entry in (oic_res or []):
        if not isinstance(entry, dict):
            continue
        if 'links' in entry:
            for link in entry.get('links') or []:
                if isinstance(link, dict):
                    yield link
        elif 'href' in entry:
            yield entry


def _seed_href(path_segs: tuple[str, ...]) -> str:
    """('device', '1') -> '/device/1' -- the leading-slash href form
    `probe_log` and diagnostics report, built from the path-segment form
    `sess.get` takes."""
    return '/' + '/'.join(path_segs)


def _get_raw(sess, path_segs: tuple[str, ...]):
    """GET `path_segs` and CBOR-decode the payload, or None on any
    missing/malformed response (a 4.04, a timeout, an empty payload) --
    shared tolerated-absence posture for both callers below, which differ
    only in which body shape they accept."""
    try:
        code, pl = sess.get(list(path_segs), timeout=10.0)
        if code == 0x45 and pl:
            return cbor2.loads(pl)
    except Exception:
        pass
    return None


def _get_batch(sess, path_segs: tuple[str, ...]) -> dict[str, dict]:
    """GET a Samsung Collection resource and parse it the same way
    /device/0 itself is parsed (parse_device0_batch): a [devcol-rep,
    {href, rep}, ...] CBOR list, not a bare Property map."""
    body = _get_raw(sess, path_segs)
    return parse_device0_batch(body) if isinstance(body, list) else {}


def _get_property(sess, path_segs: tuple[str, ...]) -> dict:
    """GET a plain OCF Property-map resource (a bare dict, not a Collection
    batch). Used for `/multidevice/vs/0` (issue #177 follow-up): listed in
    `/oic/res` on the Pattern A reporter's board but absent from
    `/device/0`'s batch, so it needs its own RETRIEVE, and it answers a
    single Property map, not a [devcol-rep, ...] list."""
    body = _get_raw(sess, path_segs)
    return body if isinstance(body, dict) else {}


def enumerate_subdevices(
    sess,
    resources: dict[str, dict],
    oic_res_links,
    probe_log: Optional[Callable[[str, bool], None]] = None,
) -> tuple[list['Subdevice'], dict[str, dict]]:
    """Discover every sibling indoor subdevice reachable over `sess`'s
    connection.

    Runs once, at first discovery, in an executor, under the coordinator's
    session lock -- every GET here is a plain RETRIEVE (the write-contract
    'don't guess' rule doesn't apply to reading an extra resource to find
    out whether it's there). Returns the *candidate* subdevices and the resources
    already fetched while probing them (already normalized to real hrefs),
    so the coordinator's first discovery poll doesn't need to re-poll them.

    `probe_log(seed_href, found)` fires for every seed attempted, whether or
    not it answered -- so diagnostics (see diagnostics.py's subdevice_probes)
    can tell "checked, nothing there" apart from "never checked", the same
    posture the speculative-probe code this replaces used to document in
    identity.py.

    Every candidate whose seed answers with a non-empty batch is returned
    here -- this function has no way to tell a real sibling from an unused
    SmartThings slot that merely answers the same shape (the Pattern A
    reporter's `/device/2`); that requires discovering+flattening the
    candidate's own entities first, which is `discover_partitioned`'s job,
    not this one's.
    See this module's docstring.
    """
    subdevices: list[Subdevice] = []
    fetched: dict[str, dict] = {}

    def _probed(seed_href: str, batch: dict) -> None:
        if probe_log is not None:
            probe_log(seed_href, bool(batch))

    def _probe_prefixed(sub_id: str) -> None:
        """Materialize one UUID-prefixed subdevice candidate -- shared by
        Pattern B (ids from subdeviceIdList) and Pattern C (ids from
        /oic/res link prefixes) below, which differ only in where the UUID
        came from."""
        seed = (sub_id, 'device', '0')
        batch = _get_batch(sess, seed)
        _probed(_seed_href(seed), batch)
        if batch:
            subdevice = Subdevice(kind='prefixed', key=sub_id, seed_path=seed)
            fetched.update(normalize_seed_batch(subdevice, batch))
            subdevices.append(subdevice)
            return
        # Fallback (issue #205): TP2X_FAC_BORA_21K itself -- the board this
        # pattern was built against -- turns out not to always expose its own
        # `/<uuid>/device/0` Collection either, so "every prefixed subdevice
        # has one" doesn't hold even on the reference hardware. With no
        # Collection to seed from and no per-UUID entry in `/oic/res` to
        # enumerate hrefs from, the only signal left is that a composite
        # device's siblings are the same physical board family as the
        # subdevice this config entry already talks to -- so probe every
        # href the master itself answered this cycle, individually, under
        # this UUID's prefix, and keep whichever ones answer. Each is a
        # plain tolerated-404 RETRIEVE, same posture as every other probe in
        # this function.
        #
        # Known gap, not yet guarded against: a firmware that answers *any*
        # request under an unrecognized prefix (echoing the master's own
        # state back rather than 4.04ing) would pass every one of these
        # probes and, if the echoed state also clears discover_partitioned's
        # liveness gate, materialize a phantom duplicate of the master
        # rather than a real sibling. Every board seen so far genuinely
        # 4.04s on paths it doesn't own (issue #205's own unit answered only
        # 1 of 31 probes), so this hasn't been built -- the one place it
        # could hook in later is comparing a candidate's confirmed reps
        # against the master's own values for those same canonical hrefs.
        flat_hrefs = []
        first = True
        for href in sorted(resources):
            if not first:
                sess.pace()
            first = False
            actual = f'/{sub_id}{href}'
            rep = _get_property(sess, tuple(actual.strip('/').split('/')))
            _probed(actual, bool(rep))
            if rep:
                flat_hrefs.append(href)
                fetched[actual] = rep
        if not flat_hrefs:
            return
        subdevices.append(Subdevice(
            kind='prefixed', key=sub_id, seed_path=(),
            flat_hrefs=tuple(flat_hrefs),
        ))

    # --- Pattern B: UUID-prefixed tree (TP2X_FAC_BORA_21K) ------------------
    raw_ids = (resources.get('/subdevices/vs/0') or {}).get(
        'x.com.samsung.da.subdeviceIdList')
    # Tolerate anything but a list of strings -- this field is redaction-prone
    # (it matches the 'deviceid' substring rule in redact.py) and the existing
    # airconditioner_fac_bora fixture carries the literal string
    # '**REDACTED**'/'REDACTED' there. That must yield zero subdevices, not a
    # crash -- issue #177 is additive, it must never break an already-working
    # single-climate-entity device.
    ids = raw_ids if isinstance(raw_ids, list) else []
    listed = sorted(i for i in ids if isinstance(i, str) and i)
    for sub_id in listed:
        _probe_prefixed(sub_id)

    # --- Pattern C: UUID prefix advertised only via /oic/res ----------------
    # (AWM-WW-AID-26-ONEBODY washer+dryer combo, issue #241.) A third
    # multidevice shape: the board answers numofsubdevice='2' on
    # /multidevice/vs/0, but carries no /subdevices/vs/0 (no subdeviceIdList
    # -- Pattern B's signal) and 4.04s /device/1 and /device/2 (Pattern A's).
    # The only trace of the sibling is a UUID-prefixed link in /oic/res
    # itself: the x.com.samsung.da.multidevice link,
    # '/<uuid>/multidevice/vs/0' on the reporting board. Its washer tree
    # answers a full Collection at /<uuid>/device/0, exactly Pattern B's
    # transform -- so treat every UUID path prefix seen in /oic/res as a
    # prefixed-subdevice candidate (minus ones subdeviceIdList already
    # named). Probing is the same tolerated-404 RETRIEVE as everything else
    # here, and discover_partitioned's entity-level liveness gate still
    # decides materialization, so a board that advertises a UUID link
    # without a live sibling behind it contributes nothing.
    linked = sorted({
        m.group(1)
        for link in _iter_oic_res_hrefs(oic_res_links)
        for m in [_UUID_PREFIX_RE.match(link.get('href', ''))]
        if m
    } - set(listed))
    for sub_id in linked:
        _probe_prefixed(sub_id)

    # --- Pattern A: indexed siblings (ARTIK051_DONGLE_FAC_18K) --------------
    indices = sorted({
        int(m.group(1))
        for link in _iter_oic_res_hrefs(oic_res_links)
        for m in [_INDEXED_HREF_RE.match(link.get('href', ''))]
        if m and int(m.group(1)) >= 1
    })
    if not indices:
        # A board that hides its whole tree from /oic/res (Pattern B's
        # reporter board does this too, but it has no /device/<n> to find
        # regardless) gives us nothing to enumerate from -- fall back to the
        # bounded speculative probe this replaces from identity.py.
        indices = list(_SPECULATIVE_DEVICE_INDICES)
    for n in indices:
        seed = ('device', str(n))
        batch = _get_batch(sess, seed)
        _probed(_seed_href(seed), batch)
        if not batch:
            continue
        subdevice = Subdevice(kind='indexed', key=str(n), seed_path=seed)
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
    multidevice_seed = ('multidevice', 'vs', '0')
    multidevice = _get_property(sess, multidevice_seed)
    _probed(_seed_href(multidevice_seed), multidevice)
    if multidevice:
        fetched['/multidevice/vs/0'] = multidevice

    return subdevices, fetched


@dataclass(frozen=True)
class SkippedSubdevice:
    """A candidate `enumerate_subdevices` found whose seed answered, but that
    `discover_partitioned`'s entity-level liveness gate rejected -- an
    unused SmartThings slot (the Pattern A reporter's `/device/2`), not a
    real second subdevice. Kept around (rather than silently dropped) so a
    caller can log/report what was skipped and why."""
    subdevice: Subdevice
    hrefs: tuple[str, ...]


# Sensor kinds whose value is a running total the *appliance* keeps rather
# than a reading of the subdevice's own hardware -- excluded from the
# liveness gate below (issue #214). HA's own running-total state classes
# cover most of them; the consumption device classes catch the rest, since a
# descriptor may deliberately declare no state_class (common.ENERGY_METER's
# monthly totals reset at each billing boundary, so they aren't
# `total_increasing`).
_METER_STATE_CLASSES = frozenset({'total', 'total_increasing'})
_METER_DEVICE_CLASSES = frozenset({'energy', 'water', 'gas'})


def _is_meter(desc) -> bool:
    """True for a cumulative consumption/counter descriptor -- see the two
    constants above. Only SensorDesc carries either attribute; everything
    else answers False through the getattr defaults."""
    return (
        getattr(desc, 'state_class', None) in _METER_STATE_CLASSES
        or getattr(desc, 'device_class', None) in _METER_DEVICE_CLASSES
    )


def _has_live_primary_entity(bound, state: dict) -> bool:
    """True if flattening `bound` (one candidate subdevice's BoundEntity
    list) produced at least one non-`None` value for a *primary* entity --
    `entity_category` unset, HA's own "the user acts on or watches this"
    tier (see the adding-device-support skill's entity-taxonomy section) --
    that isn't a cumulative meter (`_is_meter`).

    This is the materialization gate itself (see this module's docstring).
    Two exclusions, both for the same reason -- the question this answers is
    "is a physical subdevice installed at this slot?", and neither kind of
    value can speak to it:

    - **Non-primary entities.** The Pattern A reporter's `/device/2` does
      flatten to one non-`None` value (`alarm_code`), but that entity is
      `diagnostic`-category and derived from an empty `/alarms/vs/2` -- a
      config/diagnostic entity reading "something" proves nothing about
      whether hardware is there.
    - **Cumulative meters** (issue #214). An unused slot on the issue #214
      reporter's ARTIK051_KRAC_18K reports `/energy/consumption/vs/1` with a
      populated `cumulativePower` while every operational rep on it
      (`/power/1`, `/mode/1`, `/mode/vs/1`, `/temperature/current/1`,
      `/temperature/desired/1`, `/airflow/1`, `/humidity/1`) is empty `{}` --
      i.e. exactly the Pattern A `/device/2` shape plus a lifetime kWh
      counter. That counter got the slot materialized as a phantom second
      air conditioner. A single-split AC has one compressor and one energy
      meter, so a whole-appliance total showing up under a second index is
      the appliance's own bookkeeping, not evidence of a second indoor unit.
      A genuinely installed subdevice reports its own operational state too
      (the Pattern A reporter's real `/device/1` reports power, mode, both
      temperatures and airflow), and that state is what still passes this
      gate.
    """
    from .adapter import _key  # see discover_partitioned's deferred-import note
    return any(
        not b.desc.entity_category and not _is_meter(b.desc)
        and state.get(_key(b)) is not None
        for b in bound
    )


def discover_partitioned(
    resources: dict[str, dict],
    subdevices: list['Subdevice'],
    resolve_registry: Callable[..., object],
    fallback_capabilities: dict,
    log: Optional[Callable[[str], None]] = None,
    tier_log: Optional[Callable[[str, str], None]] = None,
    oic_device_types: Sequence[str] = (),
):
    """Bind every href in `resources` (the merged, real-href snapshot -- main
    plus every enumerated subdevice's seed) to entities, partitioned by which
    subdevice owns it.

    Main pass runs over hrefs owned by no subdevice -- otherwise every
    `/mode/vs/1` would land in `unbound_hrefs` too (nothing in the main
    device's registry claims that literal href) and raise a spurious
    coverage-gap repair. Then one pass per *candidate* subdevice over its own
    canonical view, resolving that subdevice's own device type from its own
    `/information/vs/0` when it reports one (e.g. the Pattern B reporter's
    wall subdevice reports `TP2X_FAC_BORA_RAC_21K` -> the 'RAC' board token ->
    airconditioner), falling back to the master's registry otherwise --
    every AC family shares the same resource surface, and a sibling that
    fails to answer its own identity resource is still the same appliance
    type as the subdevice this config entry was set up against.

    Each candidate is discovered and flattened *twice*: once silently to
    evaluate `_has_live_primary_entity` (this module's materialization
    gate -- see its docstring and this module's own), and, only if that
    passes, a second time with `log`/`tier_log` wired so its coverage gaps
    and poll tiers actually count. A candidate that fails the gate
    contributes nothing at all -- no bound entities, no unbound-href
    report, no hot/warm href -- as if it had never answered its seed.
    Discovering an unused slot's small, fixed resource set twice at
    first-discovery time only is a non-issue; getting a phantom subdevice
    silently counted into unbound_hrefs or hot/warm tiers is not.

    `oic_device_types` (from the master's own `/oic/d`, see
    registry/identity.py) is passed only to the *master's* resolution --
    subdevices have no `/oic/d` of their own read today (they resolve from
    their own `/information/vs/0` or fall back to the master's whole
    registry, as documented above), and blindly applying the master's OCF
    device type to every subdevice's own model-based resolution would be
    wrong the moment a composite appliance ever pairs two genuinely
    different device types under one connection.

    Returns `(bound, device_type_name, materialized, skipped)`:
    - `bound`: the concatenated BoundEntity list (main + every materialized
      subdevice).
    - `device_type_name`: the *master's* resolved device type (used for
      logging/device naming; each subdevice's own resolved type only affects
      which capabilities bind its hrefs, not this).
    - `materialized`: the subset of `subdevices` that passed the gate, in the
      same order -- what the caller should keep as its live subdevice roster
      going forward (poll seeds, canonical_resources, device_info_for, ...).
    - `skipped`: `SkippedSubdevice` entries for every candidate that didn't.
    """
    # Deferred import: discovery.py imports Subdevice/MAIN from this module at
    # module scope, so importing discover() back here at module scope would
    # be a circular import. By the time this function actually runs both
    # modules are fully loaded. adapter.py imports discovery.py, so the same
    # applies to flatten()/_key().
    from .adapter import flatten
    from .discovery import discover

    # Same computation canonical_view does for MAIN (snapshot minus every
    # other subdevice's owned hrefs) -- reuse it rather than re-deriving
    # owned_elsewhere here too.
    main_view = canonical_view(MAIN, resources, subdevices)

    reg = resolve_registry(main_view, device_types=oic_device_types)
    caps, pats = (
        (reg.capabilities, reg.pattern_capabilities) if reg is not None
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
            (su_reg.capabilities, su_reg.pattern_capabilities) if su_reg is not None
            else (fallback_capabilities, [])
        )
        probe_bound = discover(view, su_caps, su_pats, subdevice=su)
        probe_state = flatten(probe_bound, resources)
        if _has_live_primary_entity(probe_bound, probe_state):
            materialized.append(su)
            bound = bound + discover(
                view, su_caps, su_pats, log=log, tier_log=tier_log, subdevice=su,
            )
        else:
            skipped.append(SkippedSubdevice(
                subdevice=su,
                hrefs=tuple(sorted({b.href for b in probe_bound})),
            ))

    return bound, device_type_name, materialized, skipped
