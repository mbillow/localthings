"""Cloud "Download" programs on a laundry device (issue #342).

Some course tables carry a course whose recipe isn't fixed in firmware --
selecting it runs whichever program was last pushed down from the
SmartThings cloud ("Download" / "Downloaded" in the course catalog). Three
tokens on ``/course/vs/0``'s ``x.com.samsung.da.options`` array drive it:

  ``CloudExtraCourse_<slot><slot>...``  the device's own list of downloaded
      program slots, one byte each -- the cloud counterpart of
      ``EditCourseList_`` for local courses.
  ``CloudCourse_<blob>``               the persisted default program.
  ``OneTimeCloudCourse_<blob>``        a this-run-only override.

A ``<blob>`` is an opaque fixed-width payload whose byte 2 is the slot id
it belongs to. Confirmed on two independent DA_WM_TP1_21_COMMON washers:
the issue #342 reporter's, whose ``CloudExtraCourse_0A5C286B2D0C55301A``
enumerates nine slots matching byte 2 of all nine of its programs exactly,
and the WA55A7700AV dump in ``tests/fixtures``, whose two-slot
``CloudExtraCourse_5958`` likewise matches its ``CloudCourse`` blob's byte
2. Blob width is *not* fixed across boards (20 bytes vs 16), which is one
reason nothing here ever synthesizes one.

``CloudExtraCourse_`` does not mean the same thing on every family, so
nothing keys off it directly -- see ``cloud_slots``, which is what the rest
of this module and its callers gate on. Nor is it always complete: a
WA54CG7150 lists only ``5958`` while its owner can download five programs
and the washer runs any of them (issue #780), so a loaded program is learned
whether or not its slot is listed, and ``known_slots`` is advertised plus
learned. A device can also advertise a slot whose payload has never been
observed; the store alone answers "which are usable".

What this module does and deliberately does not do
--------------------------------------------------
The device advertises *which* slots exist but never what any of them is
called, and never the full blob for a slot other than the one currently
loaded. A blob is only observable while the device happens to be sitting on
that program, so the full payload is *learned by observation* and persisted
(same rationale as learned.py's mode store), and the human-readable name is
supplied by the user in the options flow. Nothing is hardcoded: no catalog
of program ids, no table of blobs, no assumed Download course code. A
hardcoded catalog was considered and rejected -- a blob is cloud-assigned
per account/region, so one user's captured payload is not evidence about
anyone else's device.

Blobs are replayed byte-for-byte, exactly as captured, and never
decomposed or rebuilt. (Bytes 5/7/9 of the reporter's blobs do decode
cleanly to that program's temperature/rinse/spin, but the same offsets
produce nonsense against the WA55A7700AV blob, so that decode is recorded
in docs/investigations/download-cycle.md rather than shipped.)
"""

from __future__ import annotations

import threading

from .const import CONF_CLOUD_COURSES
from .registry.capabilities.common import hex_pairs, option_value

COURSE_HREF = "/course/vs/0"

EXTRA_PREFIX = "CloudExtraCourse"
DEFAULT_PREFIX = "CloudCourse"
ONESHOT_PREFIX = "OneTimeCloudCourse"
COURSE_PREFIX = "Course"

# Synthetic, integration-owned field the coordinator merges onto
# /course/vs/0's rep so the registry's exists_fn/rep_fn/options/write_fn all
# reach this store through their existing signatures -- rep_fn in particular
# receives only its own href's rep, never the resource snapshot, so a
# sibling-resource lookup isn't available to it. Namespaced away from
# Samsung's own 'x.com.samsung.da.' fields so it can never collide with one,
# and merged at read time only: it is never written to the state cache, never
# sent to the device, and never part of a diagnostics dump.
FIELD = "x.localthings.cloudCourses"

# Raw-value namespace for a cloud program in the cycle select. A slot id is
# itself two hex chars, exactly like a local course code, so the two would be
# indistinguishable (and could collide outright) as bare select values.
RAW_PREFIX = "cloud:"

# A blob whose first two bytes are FFFF means "no program loaded" rather than
# naming one -- WA55A7700AV reports
# OneTimeCloudCourse_FFFF010049004D004A804C0037F0AC00 while sitting on a
# perfectly ordinary local course, and its byte 2 (01) is not one of the slots
# its own CloudExtraCourse_ advertises.
_SENTINEL_PREFIX = "FFFF"

# Distinct from None, which is a real observation that carried no payload.
# Only "never observed" suppresses a candidate; absent-then-loaded is a
# genuine transition and should count.
_UNOBSERVED = object()

# Byte offset within a blob that carries its slot id.
_SLOT_BYTE = 2
_MIN_BLOB_BYTES = 4


def _hex_bytes(blob):
    if not isinstance(blob, str) or len(blob) % 2 or len(blob) < _MIN_BLOB_BYTES * 2:
        return []
    try:
        int(blob, 16)
    except ValueError:
        return []
    return hex_pairs(blob.upper())


def is_loaded(blob) -> bool:
    """True when `blob` names an actual program rather than 'none'."""
    return _slot_and_loaded(blob)[1]


def slot_of(blob) -> str | None:
    """The slot id `blob` belongs to, or None if it names no program."""
    slot, loaded = _slot_and_loaded(blob)
    return slot if loaded else None


def _slot_and_loaded(blob) -> tuple[str | None, bool]:
    """Both answers off one parse -- the public pair above needs the same
    byte split, and observe() asks for both about the same payload."""
    parts = _hex_bytes(blob)
    if not parts:
        return None, False
    if "".join(parts[:2]) == _SENTINEL_PREFIX:
        return None, False
    return parts[_SLOT_BYTE], True


def advertised_slots(rep) -> list[str]:
    """Slot ids this device says it has downloaded programs in, from its own
    CloudExtraCourse_ token. The authority on *which* programs exist -- this
    is never inferred from what has been learned so far, so "3 of 9
    discovered" is answerable."""
    raw = option_value(rep.get("x.com.samsung.da.options"), EXTRA_PREFIX)
    if not isinstance(raw, str) or len(raw) % 2:
        return []
    # Preserve the device's own order (first-seen wins) while dropping any
    # repeat, so the flow lists slots the way the appliance does.
    return list(dict.fromkeys(hex_pairs(raw.upper())))


def cloud_slots(rep, courses) -> list[str]:
    """Advertised slots that are not already selectable courses.

    `CloudExtraCourse_` does not mean the same thing on every family. On the
    washers its bytes are opaque payload slots sharing nothing with the
    device's own course list, and selecting one needs the full payload. On
    the DW5000C dishwasher all four of its bytes *are* course codes in that
    device's own list (8E/8D/8F/02 -- Plastic, Pots and pans, Baby Care, and
    one untranslated), so there it is tagging which of its ordinary courses
    came from the cloud. Those are already selectable as plain `Course_`
    writes and need nothing from this module.

    Subtracting the course list tells the two apart without having to guess
    the family: what remains is slots that cannot be selected any other way,
    which is exactly the set this module exists for.
    """
    known = {c.upper() for c in courses or ()}
    return [slot for slot in advertised_slots(rep) if slot not in known]


def known_slots(rep, courses, record) -> list[str]:
    """``cloud_slots`` in the device's order, then any learned slot it
    doesn't list (issue #780) -- every program there is to name."""
    advertised = cloud_slots(rep, courses)
    learned = (record or {}).get("slots") or {}
    return advertised + [slot for slot in learned if slot not in advertised]


def supports_cloud_courses(rep, courses) -> bool:
    """True for a device with downloaded programs it cannot otherwise run."""
    return bool(cloud_slots(rep, courses))


def loaded_slot(rep) -> str | None:
    """The slot whose payload the appliance currently holds -- the one-time
    override when one is set, else the saved default.

    Deliberately not gated on the course being Download: the guided setup
    flow watches this before the Download course has been confirmed, and
    during that walk a change here *is* the signal that the user selected a
    different program. A stale token can't produce a false positive because
    the flow waits for a change from its own baseline, not for a value.
    """
    options = rep.get("x.com.samsung.da.options")
    return slot_of(option_value(options, ONESHOT_PREFIX)) or slot_of(
        option_value(options, DEFAULT_PREFIX)
    )


def _coerce(stored) -> tuple[str | None, dict[str, dict[str, str]]]:
    """Restore the persisted record, dropping anything not the shape this
    module writes -- it round-trips through the config entry as plain JSON
    and a hand-edited .storage file must not be able to crash setup (same
    posture as learned._coerce)."""
    if not isinstance(stored, dict):
        return None, {}
    download = stored.get("download_course")
    if not isinstance(download, str) or not download:
        download = None
    slots: dict[str, dict[str, str]] = {}
    raw_slots = stored.get("slots")
    if isinstance(raw_slots, dict):
        for slot, record in raw_slots.items():
            if not isinstance(slot, str) or not isinstance(record, dict):
                continue
            blob = record.get("blob")
            if not is_loaded(blob) or slot_of(blob) != slot.upper():
                continue
            name = record.get("name")
            slots[slot.upper()] = {
                "blob": blob.upper(),
                "name": name if isinstance(name, str) and name.strip() else "",
            }
    return download, slots


def persist(hass, entry, record: dict) -> None:
    """Write `record` onto the entry. Runs on the event loop, which
    async_update_entry requires."""
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_CLOUD_COURSES: record})


class CloudCourses:
    """Per-device store of discovered cloud programs.

    Mutated from whichever thread applied the update (the DTLS reader for an
    OBSERVE notify, an executor thread for a poll -- see ObserveManager.apply),
    so every access takes the lock; persistence is the caller's job, on the
    event loop.
    """

    def __init__(self, stored_record=None) -> None:
        self._lock = threading.Lock()
        download, slots = _coerce(stored_record)
        self._download_course = download
        self._slots = slots
        # Course codes seen at the moment a one-time override was *loaded* --
        # candidates for "which course means Download on this board", pending
        # user confirmation (see download_candidates).
        self._candidates: dict[str, int] = {}
        # Last one-time payload seen, so a load can be told from a poll that
        # merely re-reports one. _UNOBSERVED until the first rep arrives.
        self._last_oneshot: object = _UNOBSERVED

    # -- learning ---------------------------------------------------------

    def observe(self, rep: dict) -> bool:
        """Learn from one applied /course/vs/0 rep; True if anything changed.

        Two facts are learnable here. A blob is recorded against the slot its
        own byte 2 names, so a program only has to be sitting loaded once --
        on either token -- to be replayable forever after.

        The Download course code is only ever taken as a *candidate*, and
        only at the moment the one-time payload actually *changes* to a
        loaded value. That instant is the one the device is known to accept a
        program on, so the course selected then is real evidence. Counting
        every poll instead would rank by dwell time: tokens in this array are
        replaced by prefix and never evicted, so a stale OneTimeCloudCourse_
        outlives its run and sits there through however many polls the
        appliance spends on some ordinary course afterwards -- which is
        exactly the course that would then be suggested. A candidate is still
        never applied without confirmation in the options flow, but a
        confident wrong suggestion is most of the way to a wrong write, and a
        wrong write here starts a real wash cycle.
        """
        options = rep.get("x.com.samsung.da.options")
        if not options:
            return False
        oneshot = option_value(options, ONESHOT_PREFIX)
        with self._lock:
            # Compared once, at the end, against where this pass started --
            # not set per assignment. The two tokens can name the same slot
            # with different payloads (a downloaded program with its settings
            # tweaked for one run is exactly that shape), and a per-assignment
            # flag would then report a change on every single poll forever:
            # each pass writes the default's payload and then the one-shot's
            # over it, so neither is ever "already stored". Every one of those
            # reports rewrites the config entry, which on the SD-card installs
            # this integration runs on is the one cost here that really bites.
            # The end state is stable (the one-shot is written last and wins),
            # so comparing start to end settles after the first pass.
            before = {slot: record["blob"] for slot, record in self._slots.items()}
            for prefix in (DEFAULT_PREFIX, ONESHOT_PREFIX):
                blob = option_value(options, prefix)
                slot = slot_of(blob)
                # Unadvertised slots count too: the advertised list can be a
                # fixed subset of what the owner can download (issue #780).
                if slot is None:
                    continue
                record = self._slots.get(slot)
                if record is None:
                    self._slots[slot] = {"blob": blob.upper(), "name": ""}
                else:
                    record["blob"] = blob.upper()
            changed = before != {slot: rec["blob"] for slot, rec in self._slots.items()}

            course = option_value(options, COURSE_PREFIX)
            # Only a transition we actually watched happen counts. On the
            # first observation there is nothing to compare against, so a
            # payload sitting there is equally consistent with "just loaded"
            # and "left over from last week" -- and on a board that doesn't
            # clear the token when leaving Download, believing the former
            # proposes whatever ordinary course the appliance happens to be
            # on. Accepting that prefill would start a real wash cycle.
            # (The two dumps in the corpus taken off the Download course both
            # show the appliance clearing it to the FFFF sentinel, so this
            # may never fire in practice -- which is not a reason to rely on
            # it.) Restores don't persist _last_oneshot, so every restart
            # re-enters this first-observation state deliberately.
            first_ever = self._last_oneshot is _UNOBSERVED
            if course and is_loaded(oneshot) and not first_ever and oneshot != self._last_oneshot:
                self._candidates[course] = self._candidates.get(course, 0) + 1
            self._last_oneshot = oneshot
        return changed

    # -- reads ------------------------------------------------------------

    def download_candidates(self) -> list[str]:
        """Course codes seen at the moment a one-time program was loaded,
        most-observed first -- what the options flow offers as the likely
        Download course. Never used for a write on its own."""
        with self._lock:
            ranked = sorted(self._candidates.items(), key=lambda kv: (-kv[1], kv[0]))
        return [code for code, _ in ranked]

    def named(self) -> dict[str, str]:
        """Slots that are both learned and named -- the only ones offerable
        as a cycle option. An unnamed slot has no label that isn't either
        invented or an opaque hex id, so it stays out of the UI until the
        user supplies one."""
        with self._lock:
            return {slot: record["name"] for slot, record in self._slots.items() if record["name"]}

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "download_course": self._download_course,
                "slots": {slot: dict(record) for slot, record in self._slots.items()},
            }

    def view(self) -> dict:
        """What the registry sees under FIELD: only what a write or a label
        can actually be built from, so a descriptor never has to re-apply
        this module's rules."""
        with self._lock:
            if not self._download_course:
                return {}
            return {
                "download_course": self._download_course,
                "programs": {
                    slot: {"blob": record["blob"], "name": record["name"]}
                    for slot, record in self._slots.items()
                    if self._is_usable(record)
                },
            }

    # -- writes -----------------------------------------------------------

    def set_download_course(self, code: str | None) -> None:
        with self._lock:
            self._download_course = code or None

    def set_name(self, slot: str, name: str) -> None:
        with self._lock:
            record = self._slots.get(slot.upper())
            if record is not None:
                record["name"] = name.strip()

    @staticmethod
    def _is_usable(record) -> bool:
        """A slot is offerable once it has a name. The device supplies the
        payload; only the user can supply the label, so this is the whole
        rule and it is stated once."""
        return bool(record["name"])


def undiscovered(rep: dict, record: dict, courses) -> list[str]:
    """Known slots that aren't yet usable -- unlearned or unnamed. What the
    Repairs issue counts, and what the options flow asks the user to walk the
    appliance through. Counts against known_slots, not every advertised byte:
    a slot that is already a selectable course is nothing to set up."""
    programs = record.get("slots") or {}
    return [s for s in known_slots(rep, courses, record) if not (programs.get(s) or {}).get("name")]
