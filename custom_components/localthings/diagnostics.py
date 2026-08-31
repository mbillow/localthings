"""Diagnostics support for Local Things.

Downloadable from Settings > Devices & Services > this integration's
device > the menu > Download diagnostics. This is what the Repairs issue
(raised in coordinator.py when capability coverage is incomplete) points
users at: a redacted snapshot of the device's raw /device/0 state, plus
enough version/coverage metadata to reproduce and diagnose the gap.
"""

from __future__ import annotations

from importlib.metadata import version as pkg_version
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from . import cloudcourse
from .const import AUTH_CERTIFICATE, CONF_AUTH_TYPE, DOMAIN
from .coordinator import LocalThingsCoordinator
from .registry.capabilities.laundry import cycle_options
from .registry.encode import json_safe
from .registry.redact import redact_resources
from .registry.subdevices import MAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    integration = await async_get_integration(hass, DOMAIN)

    # importlib.metadata.version() reads the installed package's metadata off
    # disk (listdir + open + read_text), which trips HA's event-loop blocking
    # detector when called inline here. Offload it to the executor.
    stl_version = await hass.async_add_executor_job(pkg_version, "smartthings-local")
    cloud_courses = coordinator.cloud_courses.snapshot()

    # /oic/p, /oic/d, and /oic/res sit outside the /device/0 batch captured
    # below, so they'd otherwise never reach an issue report. /oic/d's `rt`
    # is OCF's device-type declaration; /oic/res is OCF's discovery
    # endpoint, relevant to the "Composite Device" model (issue #177). See
    # registry/identity.py.
    identity = coordinator._identity

    def _seed_diag(su) -> dict:
        # A flat-mode subdevice (issue #205: no working /<uuid>/device/0
        # Collection, state comes from individually-polled hrefs instead)
        # has no meaningful seed_path; report flat_hrefs in its place.
        return {
            "seed_path": ("/" + "/".join(su.seed_path)) if su.seed_path else None,
            "flat_hrefs": list(su.flat_hrefs),
        }

    def _subdevice_diag(su) -> dict:
        # `model` reads modelNum off the already-redacted `resources` rather
        # than redacting /information/vs/0 again -- modelNum never matches
        # redact.py's substring rules, so the value is the same either way.
        matching = [b for b in coordinator.bound if b.subdevice == su]
        res = redact_resources(coordinator.device_resources(su))
        return {
            "kind": su.kind,
            "key": su.key,
            **_seed_diag(su),
            "bound_entity_count": len(matching),
            "hrefs": sorted({b.href for b in matching}),
            "model": res.get("/information/vs/0", {}).get("x.com.samsung.da.modelNum", ""),
            # Keyed by this subdevice's canonical hrefs ('/mode/vs/0'), not
            # the real ones it answers on ('/mode/vs/1', '/<uuid>/mode/vs/0')
            # -- the form the registry is written against, so a sibling's
            # block reads exactly like the master's `resources` below.
            "resources": res,
        }

    # json_safe wraps the finished dump, after redaction rather than before:
    # redact.py matches on key names and must see the real structure, and a
    # value it blanks never reaches the encoder at all. See registry/encode.py
    # for why a dump can contain something JSON has no form for.
    return json_safe(
        {
            "auth_type": entry.data.get(CONF_AUTH_TYPE, AUTH_CERTIFICATE),
            "device_type": coordinator.device_type_name or "unknown",
            "one_ui_version": coordinator.one_ui_version,
            "identity": {
                "manufacturer": identity.manufacturer,
                "model": identity.model,
                "device_types": list(identity.device_types),
                "resources": redact_resources(identity.raw),
            }
            if identity is not None
            else None,
            "unbound_hrefs": sorted(coordinator._unbound_hrefs),
            # This subdevice's own resources, and only this subdevice's. On a
            # composite device (issue #177) `last_resources` is the union
            # across every live subdevice keyed by real hrefs, so reporting it
            # raw here would mix a sibling's /mode/vs/1 with the master's
            # /mode/vs/0 under no attribution. Each sibling reports its own
            # resources in `subdevices` below instead. For a device with no
            # subdevices, this is byte-identical to `last_resources`.
            "resources": redact_resources(coordinator.device_resources(MAIN)),
            # Sibling indoor subdevices discovered on this connection (issue
            # #177). subdeviceIdList (the UUID a prefixed subdevice's key comes
            # from) is deliberately NOT redacted here, unlike elsewhere in
            # `resources` -- it's an appliance-internal pairing id, not account
            # data, and reporting it is what makes this block actionable.
            "subdevices": [_subdevice_diag(su) for su in coordinator.subdevices],
            # Candidates that answered their seed but that discover_partitioned's
            # liveness gate rejected -- an unused SmartThings slot, not a real
            # second subdevice. Reported alongside subdevices above so a report
            # shows what was found and why it didn't become an entity.
            "subdevices_skipped": [
                {
                    "kind": skip.subdevice.kind,
                    "key": skip.subdevice.key,
                    **_seed_diag(skip.subdevice),
                    "hrefs": list(skip.hrefs),
                    # The reps the liveness gate actually judged -- the one
                    # thing a reader needs to second-guess a skip, and they
                    # exist nowhere else in this dump: a rejected candidate is
                    # never polled again or entered into the state cache.
                    "resources": redact_resources(
                        {
                            canon: rep
                            for href, rep in coordinator._skipped_subdevice_resources.items()
                            if (canon := skip.subdevice.to_canonical(href)) is not None
                        }
                    ),
                }
                for skip in coordinator._skipped_subdevices
            ],
            # What each enumeration probe returned ({} vs a batch), keyed by the
            # seed href attempted -- lets a report distinguish "checked, nothing
            # there" from "never checked".
            "subdevice_probes": dict(sorted(coordinator._subdevice_probes.items())),
            # /multidevice/vs/0's rep ({} when the board doesn't answer it).
            # Reported on its own, not inside `resources`, since it's metadata
            # about the connection rather than one subdevice's state, and
            # nothing polls it after discovery so it would go stale in there.
            "multidevice": redact_resources(coordinator._multidevice),
            # Modes this unit reported itself in but never advertised (issue
            # #327). Reported separately from `resources` on purpose: the dump
            # above stays exactly what the device said, so a triager can still
            # see the gap these codes were inferred from. Keyed by actual href,
            # like the store itself.
            "learned_modes": {
                "enabled": coordinator.learning_enabled,
                "codes": coordinator.learned_snapshot(),
            },
            # Cloud "Download" programs discovered on this device (issue #342),
            # reported separately from `resources` for the same reason as
            # learned_modes above -- the dump there stays exactly what the device
            # said, and this is what the integration made of it.
            #
            # Reported in full, names included. Half of what can go wrong with
            # this feature is a configuration question -- which programs got
            # named, which Download course was confirmed, whether a payload was
            # ever captured for a slot the device advertises -- and none of that
            # is answerable from the payloads alone. The names are the user's own
            # words, so this is the one place they appear; they reach a dump only
            # because its owner chose to download and share it.
            "cloud_courses": {
                "advertised_slots": cloudcourse.advertised_slots(coordinator.cloud_course_rep()),
                "cloud_slots": cloudcourse.cloud_slots(
                    coordinator.cloud_course_rep(),
                    cycle_options(coordinator.device_resources(MAIN)),
                ),
                **cloud_courses,
            },
            "integration_version": integration.version,
            "smartthings_local_version": stl_version,
            "observe_mode": coordinator.observe_mode,
            "observe_subscribed_hrefs": sorted(coordinator._observe.subscribed_hrefs),
            "observe_fallback_hrefs": sorted(coordinator._observe.fallback_hrefs),
            "observe_last_mode_change": coordinator._observe.last_mode_change_wall,
            "observe_href_freshness_s": {
                href: coordinator._cache.freshness_s(href)
                for href in coordinator._observe.subscribed_hrefs
            },
        }
    )
