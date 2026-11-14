"""Samsung OCF TVs share the Smart Monitor's Tizen resource surface (#755)."""

from dataclasses import replace

from .monitor import REGISTRY as MONITOR_REGISTRY

REGISTRY = replace(MONITOR_REGISTRY, name="tv")
