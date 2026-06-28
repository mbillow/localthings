"""Global CAPABILITIES registry: href -> Capability.

Built from the full list of Capability objects in the capabilities package.
Consumed by discover() at connection time to bind device resources to entities.

Note: each href must appear in at most one Capability across all modules.
If two capabilities share an href, the later one in the scan order wins —
this is a bug, not a feature. Add all entities for a given href to the same
Capability object instead.
"""
from .capabilities import ALL

CAPABILITIES: dict = {cap.href: cap for cap in ALL}
