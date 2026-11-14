"""Which bound entities the platforms register, and which have anything to show.

One read can't tell a lost resource from a short answer: a sibling's seed
comes back without some hrefs, a flat-board read times out, a vocabulary
arrives partial. So registration grows in place and never shrinks on a read
(#807). A discovery adds what it finds to what is registered; an entity it
didn't find stays registered, unavailable until its resource answers. The
snapshot lets an href go only once its owner has answered
_PRUNE_AFTER_CYCLES cycles without it (coordinator._track_unconfirmed), and
that takes effect at the next start, where HA shows the entity as no longer
provided. Don't judge a single read with heuristics or reload the entry over
a difference: both have looped (#807) and orphaned entities (#833).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .registry.adapter import _key
from .registry.batch import is_stub_rep
from .registry.discovery import BoundEntity
from .registry.entities import PLATFORM_OF

if TYPE_CHECKING:
    from .coordinator import LocalThingsCoordinator

EntityId = tuple[str, str, str]


def bound_id(bound: BoundEntity) -> EntityId:
    """One registered entity's identity: platform, subdevice and unique_id
    suffix. The same key can be bound on two platforms (see cleanup.py), so
    the platform is part of it."""
    return (PLATFORM_OF[type(bound.desc)], bound.subdevice.key, _key(bound))


def is_included(
    bound: BoundEntity, coordinator: LocalThingsCoordinator, *, live: bool = False
) -> bool:
    """Return False if the entity should not be registered for this device.

    Explicit exists_fn takes priority. Otherwise, if the entity has a
    field, require that field to be present in the resource rep so that
    optional fields on shared resources don't create phantom entities.

    A stub rep (is_stub_rep) is included anyway so it can be populated by
    sub-polls. A genuinely empty {} rep is included too by this default
    gate: whether empty means "not populated yet" or "permanently
    unsupported" needs per-field domain knowledge this generic gate
    doesn't have. Only a capability whose author
    has verified a field is genuinely never populated opts into stricter
    gating with its own exists_fn (see common.ENERGY_METER, issue #127).

    `bound.href` is already the actual href (issue #177); `exists_fn` gets
    `bound`'s own subdevice's canonical view instead of the raw snapshot,
    same rule as everywhere else a whole-resources-dict scan happens --
    this is a free function, so it can't use self._resources.

    Setup uses discovery resources so an empty live cache during an offline
    load (issue #295) doesn't filter rehydrated entities away. Runtime
    presence checks use `live=True` so the saved snapshot can't freeze them.
    """
    # live reads the one rep: last_resources copies the whole cache, and this
    # runs on every update for each dynamic-presence entity.
    rep = (
        coordinator.cached_resource(bound.href)
        if live
        else coordinator.discovery_resources.get(bound.href)
    )
    if rep is None:
        return False
    if bound.desc.exists_fn is not None:
        canonical = coordinator.canonical_resources if live else coordinator.discovery_canonical
        return bound.desc.exists_fn(rep, canonical(bound.subdevice))
    if bound.desc.field:
        if not rep or is_stub_rep(rep):
            return True
        return bound.desc.field in rep
    return True  # rep_fn or no-field entities (ButtonDesc) are always included


@dataclass
class Registration:
    """The registration decisions for one entry's bound entities.

    `static`: ordinary entities a discovery judged registrable; one its
    exists_fn excluded stays out until the next discovery. `late`: the
    current decision for each late_evidence entity. `retained`: entities
    registered from the snapshot that live discovery didn't bind.
    `generation` moves on every change, so platforms only rescan then.
    """

    static: frozenset[EntityId] = frozenset()
    late: dict[EntityId, bool] = field(default_factory=dict)
    retained: frozenset[EntityId] = frozenset()
    generation: int = 0

    def judge(
        self,
        bounds: Iterable[BoundEntity],
        late_bounds: Iterable[BoundEntity],
        included: Callable[[BoundEntity], bool],
    ) -> None:
        """Decide every entity of one discovery from its evidence."""
        decided = {bound_id(b) for b in bounds if included(b)}
        late = {bound_id(b) for b in late_bounds}
        self.static = frozenset(decided - late)
        self.late = {ident: ident in decided for ident in late}
        self.retained = frozenset()
        self.generation += 1

    def extend(
        self,
        bounds: Iterable[BoundEntity],
        late_bounds: Iterable[BoundEntity],
        included: Callable[[BoundEntity], bool],
    ) -> None:
        """Decide entities a later discovery added, leaving the rest as
        they are -- a sub-unit that came back (coordinator._recover_dormant)."""
        bounds = list(bounds)
        ids = {bound_id(b) for b in bounds}
        late = {bound_id(b) for b in late_bounds} & ids
        decided = {bound_id(b) for b in bounds if included(b)}
        self.static |= decided - late
        self.late.update({ident: ident in decided for ident in late})
        self.retained -= ids
        self.generation += 1

    def retain(self, retained: Iterable[BoundEntity], previous: Registration) -> None:
        """Keep entities `previous` registered that this discovery didn't
        bind, with the decision they had."""
        ids = frozenset(bound_id(b) for b in retained)
        self.retained = ids
        self.static |= previous.static & ids
        self.late.update({i: d for i, d in previous.late.items() if i in ids})
        self.generation += 1

    def decide_late(self, ident: EntityId, included: bool) -> bool:
        """Record live evidence for a late_evidence entity; True if it changed."""
        if self.late.get(ident) == included:
            return False
        self.late[ident] = included
        self.generation += 1
        return True

    def registrable(self, bound: BoundEntity, live: Callable[[], bool]) -> bool:
        """Whether a platform should register `bound` now. A dynamic_presence
        entity the discovery excluded is added once `live()` shows it."""
        ident = bound_id(bound)
        if ident in self.late:
            return self.late[ident]
        if ident in self.static:
            return True
        return bound.desc.dynamic_presence and live()

    def provides(self, bound: BoundEntity, live: Callable[[], bool]) -> bool:
        """Whether a registered entity has anything to show: one this run's
        data stopped backing stays registered but unavailable."""
        ident = bound_id(bound)
        if self.late.get(ident) is False:
            return False
        if bound.desc.dynamic_presence or ident in self.retained:
            return live()
        return ident in self.static or ident in self.late
