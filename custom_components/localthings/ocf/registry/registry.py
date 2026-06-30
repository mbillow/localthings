"""Global CAPABILITIES registry: href -> list[Capability].

Built from the full list of Capability objects in the capabilities package.
Consumed by discover() at connection time to bind device resources to entities.

Raises ValueError at import if any href group contains an unfiltered cap
alongside other caps (i.e., a cap with neither rt_filter nor match_fn set
in a group with multiple caps).
"""
from .capabilities import ALL
from .capability import Capability


def _build() -> dict[str, list[Capability]]:
    """Build the registry, raising ValueError for invalid duplicate hrefs.

    Duplicates are allowed only when every cap in the group has at least one
    of rt_filter or match_fn set. An unfiltered cap sharing an href with
    another cap would be ambiguous.

    Skips capabilities with href=None (pattern capabilities, handled elsewhere).
    """
    out: dict[str, list[Capability]] = {}
    for cap in ALL:
        if cap.href is None:
            continue
        if cap.href not in out:
            out[cap.href] = [cap]
        else:
            out[cap.href].append(cap)

    # Validate: every group with >1 cap must have all caps filtered
    for href, caps in out.items():
        if len(caps) > 1:
            unfiltered = [c for c in caps
                          if c.rt_filter is None and c.match_fn is None]
            if unfiltered:
                raise ValueError(
                    f"duplicate capability href {href!r} has unfiltered cap(s); "
                    f"each cap sharing an href must set rt_filter or match_fn"
                )

    return out


CAPABILITIES: dict[str, list[Capability]] = _build()
