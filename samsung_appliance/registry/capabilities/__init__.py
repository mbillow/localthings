from ..capability import Capability
from . import common


def _is_capability(v):
    return isinstance(v, Capability)


ALL = [
    *[v for v in vars(common).values() if _is_capability(v)],
]
