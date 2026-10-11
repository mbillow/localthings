"""Devices with no /device/0 Collection, polled one href at a time.

Samsung's AV boards (oic.d.networkaudio so far) serve plain property
resources instead of the devcol batch. The config flow and the coordinator
both read them through here, so they agree on which hrefs to GET.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import TYPE_CHECKING

from .capabilities.av import ONBOARDING_HREFS
from .subdevices import _iter_oic_res_hrefs

if TYPE_CHECKING:
    import logging

# Reps answer in milliseconds; the timeout only matters for advertised hrefs
# that never answer (the soundbar's Alexa sub-resources).
FLAT_TIMEOUT_S = 2.5
# The first full read must outlast those (a soundbar's Alexa sign-in
# sub-resources): an href it misses never gets entities.
FLAT_ENUMERATION_BUDGET_S = 60.0

# Never read: the onboarding resources (tokens, a paired phone, nearby
# networks), /oic/sec/* (refused, no entities), identity (read_identity has
# it) and /device/<n> Collections (owned by the subdevice code).
_SKIP_EXACT = frozenset({"/oic/d", "/oic/p", "/oic/res", *ONBOARDING_HREFS})
_SKIP_PREFIXES = ("/oic/sec/", "/device/")


def flat_hrefs(links) -> list[str]:
    """GET-able hrefs from a raw /oic/res answer, bare or wrapped, sorted."""
    hrefs = {
        link["href"] for link in _iter_oic_res_hrefs(links) if isinstance(link.get("href"), str)
    }
    return sorted(h for h in hrefs if h not in _SKIP_EXACT and not h.startswith(_SKIP_PREFIXES))


def read_all(transport, links, *, logger: logging.Logger) -> dict[str, dict]:
    """Every resource /oic/res advertises, for discovery. Blocking.

    Nothing unless at least half of them answered. A board in network
    standby can complete the handshake and then let most reads time out;
    discovering from that would register a handful of entities and drop
    the rest until it is read in full again.
    """
    hrefs = flat_hrefs(links)
    resources = dict(
        iter_resources(
            transport,
            hrefs,
            timeout=FLAT_TIMEOUT_S,
            budget=FLAT_ENUMERATION_BUDGET_S,
            logger=logger,
        )
    )
    if 2 * len(resources) < len(hrefs):
        logger.debug("only %d of %d resources answered; not ready", len(resources), len(hrefs))
        return {}
    return resources


def iter_resources(
    transport,
    hrefs: list[str],
    *,
    timeout: float,
    logger: logging.Logger,
    budget: float | None = None,
) -> Iterator[tuple[str, dict]]:
    """GET each href in turn, tolerating individual failures. Blocking.

    A 4.04 or timeout just leaves the href out, and each GET is clamped to
    what is left of `budget`. Sequential, because concurrent reads corrupt
    IoTivity's per-peer blockwise state (see probing._discover_advertised_ports).
    """
    deadline = time.monotonic() + budget if budget is not None else None
    first = True
    for index, href in enumerate(hrefs):
        remaining = deadline - time.monotonic() if deadline is not None else timeout
        if remaining <= 0:
            logger.debug("flat read budget spent; skipping %d hrefs", len(hrefs) - index)
            break
        try:
            if not first:
                transport.pace()
            first = False
            code, rep = transport.read(href.strip("/").split("/"), timeout=min(timeout, remaining))
            if code == 0x45 and isinstance(rep, dict):
                yield href, _without_secrets(rep)
        except Exception as e:
            logger.debug("flat read %s: %s", href, e)


# The WiFi passphrase (oic.r.wificonf's cd) and the network's identity: its
# BSSID, and the SSIDs a soundbar's networkInfo reports.
_SECRET_KEYS = frozenset({"cd", "x.com.samsung.bssid", "ssid", "bssid", "wifidirectssid"})


def _without_secrets(rep: dict) -> dict:
    """`rep` without _SECRET_KEYS, at the top level or one level down, so they
    are never cached or stored. tnn (the SSID, or "ethernet" on a wired unit)
    is kept as just "wifi" or "ethernet"."""

    def _strip(value):
        if not isinstance(value, dict) or _SECRET_KEYS.isdisjoint(value):
            return value
        return {k: v for k, v in value.items() if k not in _SECRET_KEYS}

    rep = {k: _strip(v) for k, v in _strip(rep).items()}
    if rep.get("tnn") and str(rep["tnn"]).lower() != "ethernet":
        rep["tnn"] = "wifi"
    return rep
