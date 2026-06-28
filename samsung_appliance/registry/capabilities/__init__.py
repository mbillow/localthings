from . import common, laundry, operational
from ..capability import Capability


def _is_capability(v):
    return isinstance(v, Capability)


ALL = [v for mod in (common, operational, laundry)
       for v in vars(mod).values() if _is_capability(v)]
