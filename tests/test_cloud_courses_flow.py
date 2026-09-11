"""End-to-end wiring for cloud "Download" cycles (issue #342).

The store's own rules are covered in test_cloud_courses.py. This file covers
the parts that only exist once a coordinator is running: learning from an
applied rep, persisting to the config entry, surfacing the store to entity
descriptors through the synthetic field, the Repairs nudge, and the options
flow that supplies the names.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import cloudcourse
from custom_components.localthings.const import (
    CONF_CLOUD_COURSES,
    CONF_CLOUD_COURSES_ENABLED,
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.entities import SelectDesc
from custom_components.localthings.registry.subdevices import MAIN
from tests.conftest import _load_device
from tests.test_subdevice_discovery import ENTRY_DATA


@pytest.fixture(autouse=True)
def _fast_guided_waits(monkeypatch):
    """Guided setup polls the appliance on a human timescale. Left at its
    real values a single lingering round would hold the suite for its whole
    timeout, since async_block_till_done waits on the task."""
    from custom_components.localthings import config_flow as cf

    monkeypatch.setattr(cf, "_CLOUD_PROBE_INTERVAL_S", 0.01)
    monkeypatch.setattr(cf, "_CLOUD_WAIT_TIMEOUT_S", 0.5)


FIXTURE = "washer_ww5000c_cloud"
COURSE = cloudcourse.COURSE_HREF

SPORTS = "0021550449284D134AA04C0035F004F005F0AC00"
JEANS = "001F6B0449284D134AA04C0035F004F005F0AC00"


async def _flush(hass: HomeAssistant) -> None:
    """hass.add_job hops used by the persist path."""
    for _ in range(3):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


def _entry(hass: HomeAssistant, data=None, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, **(data or {})},
        options=options or {},
        unique_id="localthings_CLOUD-COURSE-TEST",
    )
    entry.add_to_hass(hass)
    return entry


async def _coordinator(hass: HomeAssistant, entry=None) -> LocalThingsCoordinator:
    coordinator = LocalThingsCoordinator(hass, entry or _entry(hass))
    resources = _load_device(FIXTURE)
    coordinator._run_discovery(resources)
    for href, rep in resources.items():
        coordinator._observe.apply(href, rep, source="poll")
    await _flush(hass)
    return coordinator


def _cycle_state(coordinator: LocalThingsCoordinator):
    from custom_components.localthings.registry.adapter import flatten

    return flatten(coordinator.bound, coordinator.entity_resources()).get("cycle")


def _cycle_desc(coordinator: LocalThingsCoordinator) -> SelectDesc:
    return cast(SelectDesc, next(b.desc for b in coordinator.bound if b.desc.key == "cycle"))


def _cycle_options(coordinator: LocalThingsCoordinator):
    from custom_components.localthings.registry.subdevices import MAIN

    return _cycle_desc(coordinator).options(coordinator.canonical_resources(MAIN))


async def test_a_poll_learns_and_persists_the_loaded_programs(hass: HomeAssistant):
    entry = _entry(hass)
    coordinator = await _coordinator(hass, entry)

    assert coordinator.cloud_courses.snapshot()["slots"]["55"]["blob"] == SPORTS
    assert coordinator.cloud_courses.snapshot()["slots"]["6B"]["blob"] == JEANS
    # Survives a restart -- the payload is only visible while loaded.
    assert entry.data[CONF_CLOUD_COURSES]["slots"]["55"]["blob"] == SPORTS


async def test_an_optimistic_write_teaches_nothing(hass: HomeAssistant):
    """An optimistic rep is what this integration just wrote, not what the
    appliance reported."""
    coordinator = LocalThingsCoordinator(hass, _entry(hass))
    coordinator._observe.apply(
        COURSE,
        {"x.com.samsung.da.options": ["CloudExtraCourse_55", f"CloudCourse_{SPORTS}"]},
        source="optimistic",
    )
    await _flush(hass)
    assert "55" not in coordinator.cloud_courses.snapshot()["slots"]


async def test_learned_but_unnamed_programs_stay_out_of_the_cycle_select(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    options = _cycle_options(coordinator)
    assert all(not o.startswith(cloudcourse.RAW_PREFIX) for o in options)
    # The local courses are untouched.
    assert "87" in options


async def test_naming_a_program_puts_it_in_the_cycle_select(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)

    assert "cloud:55" in _cycle_options(coordinator)
    # The fixture is sitting on Download with a Jeans one-time override, and
    # Jeans is still unnamed -- so the state falls through to the raw course.
    assert _cycle_state(coordinator) == "87"

    coordinator.apply_cloud_courses({"6B": "Jeans"}, "87")
    await _flush(hass)
    assert _cycle_state(coordinator) == "cloud:6B"


async def test_the_synthetic_field_never_reaches_the_device_snapshot(hass: HomeAssistant):
    """It is merged at read time only: last_resources stays exactly what the
    appliance reported, so it can't be polled over, written back, or land in
    a diagnostics dump."""
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)

    assert cloudcourse.FIELD in coordinator.entity_resources()[COURSE]
    assert cloudcourse.FIELD not in coordinator.last_resources[COURSE]
    assert cloudcourse.FIELD not in coordinator.resource(COURSE)


async def test_selecting_a_named_program_writes_both_tokens(hass: HomeAssistant):
    """Driven through async_send_command rather than write_fn directly: the
    command path builds its own rep, and a rep taken straight off the state
    cache carries no cloud programs, so the write would silently no-op."""
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)

    sent: list[tuple[list[str], dict]] = []

    class _FakeSession:
        def write(self, path_segs, body, timeout=None):
            sent.append((path_segs, body))
            return 0x44

        def pace(self):
            pass

    coordinator._session = cast(Any, _FakeSession())
    # Same shape as test_coordinator_send_command's fixture: a write schedules
    # a debounced refresh, which would poll through this fake and leave its
    # timer behind after the test.
    coordinator.async_request_refresh = AsyncMock()
    bound = next(b for b in coordinator.bound if b.desc.key == "cycle")
    await coordinator.async_send_command(bound, "cloud:55")

    assert len(sent) == 1
    path_segs, payload = sent[0]
    assert path_segs == ["course", "vs", "0"]
    assert payload["x.com.samsung.da.options"] == [
        "Course_87",
        f"OneTimeCloudCourse_{SPORTS}",
    ]
    # The optimistic merge keeps the rest of the array intact for the settle
    # window -- including the device's own slot list and the saved default.
    merged = coordinator.resource(COURSE)["x.com.samsung.da.options"]
    assert "Course_87" in merged
    assert f"OneTimeCloudCourse_{SPORTS}" in merged
    assert "CloudExtraCourse_0A5C286B2D0C55301A" in merged


async def test_a_repair_is_raised_until_every_program_is_named(hass: HomeAssistant):
    entry = _entry(hass)
    coordinator = await _coordinator(hass, entry)
    coordinator._refresh_cloud_course_issue()
    await _flush(hass)

    registry = ir.async_get(hass)
    issue_id = f"cloud_courses_{entry.entry_id}"
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert (issue.translation_placeholders or {})["total"] == "9"

    for slot in cloudcourse.advertised_slots(coordinator.cloud_course_rep()):
        # Only two are learned; name every advertised slot to close the gap.
        coordinator.cloud_courses.observe(
            {
                "x.com.samsung.da.options": [
                    "CloudExtraCourse_0A5C286B2D0C55301A",
                    f"CloudCourse_0000{slot}0449284D134AA04C0035F004F005F0AC00",
                ]
            }
        )
        coordinator.apply_cloud_courses({slot: f"Program {slot}"}, "87")
    await _flush(hass)

    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_no_repair_for_a_device_without_downloaded_programs(hass: HomeAssistant):
    entry = _entry(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    resources = _load_device("washer")
    coordinator._run_discovery(resources)
    for href, rep in resources.items():
        coordinator._observe.apply(href, rep, source="poll")
    coordinator._refresh_cloud_course_issue()
    await _flush(hass)

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"cloud_courses_{entry.entry_id}") is None


async def test_a_malformed_entry_record_does_not_block_setup(hass: HomeAssistant):
    entry = _entry(hass, data={CONF_CLOUD_COURSES: {"slots": {"55": {"blob": "junk"}}}})
    coordinator = await _coordinator(hass, entry)
    # Dropped on restore, then relearned from the live poll.
    assert coordinator.cloud_courses.snapshot()["slots"]["55"]["blob"] == SPORTS


# ---------------------------------------------------------------------------
# cloud_courses_enabled toggle (issue #364) -- some devices report a
# downloaded program's payload before the owner has ever meant to use the
# feature, so the Repair and the entity offering need a way to be turned
# off entirely without discarding what was already learned.
# ---------------------------------------------------------------------------


def test_cloud_courses_enabled_defaults_to_true(hass: HomeAssistant):
    coordinator = LocalThingsCoordinator(hass, _entry(hass))
    assert coordinator.cloud_courses_enabled is True


async def test_disabling_suppresses_the_repair(hass: HomeAssistant):
    entry = _entry(hass, options={CONF_CLOUD_COURSES_ENABLED: False})
    coordinator = await _coordinator(hass, entry)
    coordinator._refresh_cloud_course_issue()
    await _flush(hass)

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"cloud_courses_{entry.entry_id}") is None


async def test_disabling_clears_an_already_open_repair(hass: HomeAssistant):
    """Turning the option off has to reach a Repair that's already showing,
    not just prevent a future one -- otherwise the toggle looks broken to
    anyone who flips it after already seeing the nudge."""
    entry = _entry(hass)
    coordinator = await _coordinator(hass, entry)
    coordinator._refresh_cloud_course_issue()
    await _flush(hass)
    registry = ir.async_get(hass)
    issue_id = f"cloud_courses_{entry.entry_id}"
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    hass.config_entries.async_update_entry(entry, options={CONF_CLOUD_COURSES_ENABLED: False})
    coordinator._refresh_cloud_course_issue()
    await _flush(hass)

    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_disabling_hides_an_already_named_program_from_the_cycle_select(
    hass: HomeAssistant,
):
    """`coordinator._on_cloud_courses_changed()` after the option write
    stands in for __init__.py's options-update listener, which this
    lightweight coordinator (no async_setup_entry) never registers --
    see test_disabling_clears_an_already_open_repair for the same shape."""
    entry = _entry(hass)
    coordinator = await _coordinator(hass, entry)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)
    assert "cloud:55" in _cycle_options(coordinator)

    hass.config_entries.async_update_entry(entry, options={CONF_CLOUD_COURSES_ENABLED: False})
    coordinator._on_cloud_courses_changed()
    assert "cloud:55" not in _cycle_options(coordinator)
    # Not discarded -- the store itself is untouched, only what
    # entity_resources() hands the registry changes.
    assert coordinator.cloud_courses.named() == {"55": "Sports"}
    assert entry.data[CONF_CLOUD_COURSES]["slots"]["55"]["name"] == "Sports"


async def test_re_enabling_immediately_restores_the_offering(hass: HomeAssistant):
    entry = _entry(hass, options={CONF_CLOUD_COURSES_ENABLED: False})
    coordinator = await _coordinator(hass, entry)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)
    assert "cloud:55" not in _cycle_options(coordinator)

    hass.config_entries.async_update_entry(entry, options={CONF_CLOUD_COURSES_ENABLED: True})
    coordinator._on_cloud_courses_changed()
    assert "cloud:55" in _cycle_options(coordinator)


async def test_disabling_does_not_stop_passive_observation(hass: HomeAssistant):
    """Guided/manual setup depend on live observation to detect a newly
    selected program at all -- see CONF_CLOUD_COURSES_ENABLED's own comment
    for why the option leaves this running rather than gating it too."""
    entry = _entry(hass, options={CONF_CLOUD_COURSES_ENABLED: False})
    coordinator = await _coordinator(hass, entry)

    assert coordinator.cloud_courses.snapshot()["slots"]["55"]["blob"] == SPORTS
    assert entry.data[CONF_CLOUD_COURSES]["slots"]["55"]["blob"] == SPORTS


async def test_disabling_pushes_the_new_current_option_immediately(hass: HomeAssistant):
    """select.py's current_option reads coordinator.data, not
    canonical_resources() -- clearing only the canonical-view cache left
    it stale until whatever poll happened to run next. The fixture is
    sitting on a one-time Jeans ('6B') override, so naming it is what
    makes the live selection actually depend on the cloud store rather
    than falling back to the raw course code."""
    entry = _entry(hass)
    coordinator = await _coordinator(hass, entry)
    coordinator.apply_cloud_courses({"55": "Sports", "6B": "Jeans"}, "87")
    await _flush(hass)
    assert coordinator.data["cycle"] == "cloud:6B"

    hass.config_entries.async_update_entry(entry, options={CONF_CLOUD_COURSES_ENABLED: False})
    coordinator._on_cloud_courses_changed()

    assert coordinator.data["cycle"] == "87"


async def test_an_unrelated_option_save_before_first_poll_does_not_clear_a_real_repair(
    hass: HomeAssistant,
):
    """_on_cloud_courses_changed now runs from __init__.py's options-update
    listener on *every* entry save, not just a cloud-course-specific one --
    including one made before this device's first poll (a fresh restart,
    still rehydrating). /course/vs/0 unpolled reads as an empty rep, which
    must not be treated as evidence that nothing is pending: that would
    delete a Repair a real poll had every reason to raise."""
    entry = _entry(hass)
    coordinator = await _coordinator(hass, entry)
    coordinator._refresh_cloud_course_issue()
    await _flush(hass)
    registry = ir.async_get(hass)
    issue_id = f"cloud_courses_{entry.entry_id}"
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    # A second coordinator against the same entry, standing in for a
    # restart that hasn't polled yet -- its own resource cache is empty.
    fresh = LocalThingsCoordinator(hass, entry)
    fresh._on_cloud_courses_changed()
    await _flush(hass)

    assert registry.async_get_issue(DOMAIN, issue_id) is not None


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


async def _options_handler(hass: HomeAssistant, coordinator: LocalThingsCoordinator):
    from custom_components.localthings.config_flow import LocalThingsOptionsFlow

    entry = coordinator.config_entry
    assert entry is not None
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    handler = LocalThingsOptionsFlow()
    handler.hass = hass
    # OptionsFlow.config_entry resolves through `handler`, which the flow
    # manager normally sets when it starts the flow.
    handler.handler = entry.entry_id
    return handler


async def test_the_menu_offers_download_cycles_only_when_the_device_has_them(
    hass: HomeAssistant,
):
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    result = await handler.async_step_init()
    assert "cloud_courses" in result["menu_options"]


async def test_naming_through_the_flow_persists(hass: HomeAssistant):
    entry = _entry(hass)
    coordinator = await _coordinator(hass, entry)
    handler = await _options_handler(hass, coordinator)

    await handler.async_step_cloud_manual()
    await handler.async_step_cloud_manual(
        {"name_55": "Sports", "name_6B": "Jeans", "download_course": "87"}
    )
    await _flush(hass)

    assert coordinator.cloud_courses.named() == {"55": "Sports", "6B": "Jeans"}
    assert entry.data[CONF_CLOUD_COURSES]["download_course"] == "87"


async def test_the_flow_rejects_two_programs_sharing_a_name(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)

    result = await handler.async_step_cloud_manual(
        {"name_55": "Sports", "name_6B": "sports", "download_course": "87"}
    )
    assert result["errors"] == {"base": "cloud_course_name_duplicate"}
    assert coordinator.cloud_courses.named() == {}


async def test_clearing_a_name_removes_the_program_from_the_select(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_manual({"name_55": "Sports", "download_course": "87"})
    await _flush(hass)
    assert "cloud:55" in _cycle_options(coordinator)

    await handler.async_step_cloud_manual({"name_55": "", "download_course": "87"})
    await _flush(hass)
    assert "cloud:55" not in _cycle_options(coordinator)


async def test_without_a_confirmed_download_course_nothing_is_offered(hass: HomeAssistant):
    """Names alone aren't enough -- there'd be no course code to write."""
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_manual({"name_55": "Sports", "download_course": ""})
    await _flush(hass)

    assert coordinator.cloud_courses.named() == {"55": "Sports"}
    assert "cloud:55" not in _cycle_options(coordinator)


async def test_the_form_proposes_the_observed_download_course(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    result = await handler.async_step_cloud_manual()

    assert result["step_id"] == "cloud_manual"
    assert result["description_placeholders"]["total"] == "9"
    # Two learned so far, seven still to walk through on the appliance.
    assert result["description_placeholders"]["found"] == "2"
    _observe_program_load(coordinator, SPORTS)
    assert coordinator.cloud_courses.download_candidates() == ["87"]


async def test_no_repair_until_a_program_has_actually_been_seen(hass: HomeAssistant):
    """The DW5000C advertises four downloaded programs and has never loaded
    one, so nothing about them is learnable and no name field can be offered.
    Warning its owner about a feature they may never use -- with no action
    that could clear it -- is worse than staying quiet until they run one."""
    entry = _entry(hass)
    coordinator = LocalThingsCoordinator(hass, entry)
    resources = _load_device("dishwasher_dw5000c_cloud")
    coordinator._run_discovery(resources)
    for href, rep in resources.items():
        coordinator._observe.apply(href, rep, source="poll")
    coordinator._refresh_cloud_course_issue()
    await _flush(hass)

    assert cloudcourse.advertised_slots(coordinator.cloud_course_rep()) == ["8E", "8D", "8F", "02"]
    assert coordinator.cloud_courses.snapshot()["slots"] == {}
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"cloud_courses_{entry.entry_id}") is None


async def test_the_synthetic_field_never_reaches_diagnostics(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """canonical_resources carries it so entity descriptors can read it, and
    diagnostics reads canonical_resources -- so the drop has to happen at the
    redaction boundary. A dump is meant to be what the appliance said, and
    cloud program names are text the user typed."""
    from custom_components.localthings.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = _entry(hass)
    coordinator = await _coordinator(hass, entry)
    coordinator.apply_cloud_courses({"55": "Marc's weekend towels"}, "87")
    await _flush(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert cloudcourse.FIELD not in json.dumps(diag["resources"])
    # The device's own tokens are still there -- resources stays what it said.
    assert "CloudExtraCourse_0A5C286B2D0C55301A" in json.dumps(diag["resources"])

    # The store is reported in full in its own block: what was discovered,
    # what the user named it, and which course they confirmed. Half of what
    # goes wrong here is a configuration question and none of it is
    # answerable from the payloads alone.
    cloud = diag["cloud_courses"]
    assert cloud["advertised_slots"] == ["0A", "5C", "28", "6B", "2D", "0C", "55", "30", "1A"]
    assert cloud["download_course"] == "87"
    assert cloud["slots"]["55"] == {"blob": SPORTS, "name": "Marc's weekend towels"}
    # A slot seen but never named is reported too -- that gap is the thing a
    # "why isn't my download cycle showing up" report needs to reveal.
    assert cloud["slots"]["6B"] == {"blob": JEANS, "name": ""}


async def test_the_debug_read_service_reports_only_device_state(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)

    stripped = coordinator.device_resources(MAIN)
    assert cloudcourse.FIELD not in stripped[COURSE]
    assert "x.com.samsung.da.options" in stripped[COURSE]


async def test_the_flow_rejects_a_download_course_the_device_does_not_offer(
    hass: HomeAssistant,
):
    """Whatever lands here becomes the Course_ token of a real write."""
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)

    result = await handler.async_step_cloud_manual({"name_55": "Sports", "download_course": "FF"})
    assert result["errors"] == {"base": "cloud_course_unknown_course"}
    assert coordinator.cloud_courses.snapshot()["download_course"] is None
    assert coordinator.cloud_courses.named() == {}


async def test_the_flow_rejects_a_name_shadowing_a_personal_course(hass: HomeAssistant):
    """Personal course names come from the device, not the catalog, and the
    select renders them the same way -- so they collide the same way."""
    coordinator = await _coordinator(hass)
    # 'MyCo' as a personal course label on a code the device offers.
    rep = dict(coordinator.resource("/wm/personalcourse/vs/0") or {})
    rep["x.com.samsung.da.courses"] = ["1C_01044D79436F"]
    coordinator._observe.apply("/wm/personalcourse/vs/0", rep, source="poll")
    await _flush(hass)

    handler = await _options_handler(hass, coordinator)
    result = await handler.async_step_cloud_manual({"name_55": "MyCo", "download_course": "87"})
    assert result["errors"] == {"base": "cloud_course_name_duplicate"}


# ---------------------------------------------------------------------------
# Guided setup
# ---------------------------------------------------------------------------


def _observe_program_load(coordinator, blob, course="87"):
    """Stand in for the user loading a program while Home Assistant watches.
    A candidate Download course comes only from a transition that was
    actually observed, so tests that expect one have to produce it."""
    coordinator._observe.apply(
        COURSE,
        {
            "x.com.samsung.da.options": [
                "CloudExtraCourse_0A5C286B2D0C55301A",
                f"Course_{course}",
                f"OneTimeCloudCourse_{blob}",
            ]
        },
        source="poll",
    )


def _stub_probe(coordinator, sequence):
    """Drive async_probe_cloud_courses from a scripted list of loaded slots,
    standing in for the user turning the dial. The last entry repeats."""
    seq = list(sequence)

    async def probe() -> str | None:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    # setattr, not assignment: the stub stands in for a bound method.
    setattr(coordinator, "async_probe_cloud_courses", probe)  # noqa: B010


async def _run_wait(handler):
    """Re-enter the progress step the way Home Assistant does until its task
    settles, then follow the completion through."""
    result = await handler.async_step_cloud_wait()
    for _ in range(200):
        if result["type"] != "progress":
            return result
        if handler._cloud_task is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(handler._cloud_task), 5)
        result = await handler.async_step_cloud_wait()
    raise AssertionError("progress step never settled")


async def test_guided_waits_for_a_change_not_a_state(hass: HomeAssistant):
    """The trap this design exists to avoid. After naming a program the
    appliance is still sitting on it, so a loop that fires on "a known slot is
    loaded" would re-offer the same one forever. Each round baselines on what
    is loaded when it starts and completes only on a change."""
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)

    # The fixture is already loaded with 6B, so that is the baseline.
    await handler.async_step_cloud_guided()
    assert handler._cloud_baseline == "6B"

    # The appliance stays on 6B: no round completes.
    _stub_probe(coordinator, ["6B"])
    handler._cloud_task = None
    task = hass.async_create_task(handler._await_cloud_selection(coordinator))
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_guided_names_a_newly_selected_program(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()

    _stub_probe(coordinator, ["55"])
    result = await _run_wait(handler)
    assert result["step_id"] == "cloud_name"

    form = await handler.async_step_cloud_name()
    assert form["description_placeholders"]["slot"] == "55"
    # Persisted immediately -- closing the dialog now would lose nothing.
    await handler.async_step_cloud_name({"name": "Sports"})
    await _flush(hass)
    assert coordinator.cloud_courses.named() == {"55": "Sports"}


async def test_guided_rebaselines_so_the_named_program_cannot_refire(
    hass: HomeAssistant,
):
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()
    _stub_probe(coordinator, ["55"])
    await _run_wait(handler)
    await handler.async_step_cloud_name({"name": "Sports"})
    await _flush(hass)
    # The next round starts from the program just named, so the appliance
    # sitting on it is not a new selection.
    assert handler._cloud_baseline == "55"


async def test_reselecting_a_named_program_offers_an_edit_not_an_error(
    hass: HomeAssistant,
):
    """Re-picking one is how someone checks their work. It gets the existing
    name pre-filled, and the counter deliberately does not move."""
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)

    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()
    _stub_probe(coordinator, ["55"])
    await _run_wait(handler)

    form = await handler.async_step_cloud_name()
    assert form["errors"] == {}
    assert form["data_schema"]({})["name"] == "Sports"
    before = form["description_placeholders"]["named"]
    await handler.async_step_cloud_name({"name": "Sports"})
    await _flush(hass)
    after = handler._cloud_progress_placeholders(coordinator)["named"]
    assert before == after == "1"


async def test_guided_times_out_into_a_retry_or_finish_menu(hass: HomeAssistant, monkeypatch):
    from custom_components.localthings import config_flow as cf

    monkeypatch.setattr(cf, "_CLOUD_WAIT_TIMEOUT_S", 0.0)
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()
    _stub_probe(coordinator, ["6B"])

    result = await _run_wait(handler)
    assert result["step_id"] == "cloud_timeout"
    menu = await handler.async_step_cloud_timeout()
    assert set(menu["menu_options"]) == {"cloud_guided", "cloud_finish"}


async def test_a_failed_probe_does_not_end_the_round(hass: HomeAssistant):
    """A dropped read is one missed poll, not a reason to bail on someone
    standing at the appliance."""
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()
    _stub_probe(coordinator, [None, None, "55"])

    result = await _run_wait(handler)
    assert result["step_id"] == "cloud_name"
    assert handler._cloud_slot == "55"


async def test_the_entry_step_is_a_menu_offering_both_paths(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    result = await handler.async_step_cloud_courses()
    assert set(result["menu_options"]) == {"cloud_guided", "cloud_manual"}


async def test_closing_the_dialog_stops_probing_the_appliance(hass: HomeAssistant):
    """Closing the dialog is the documented way to leave guided setup, so it
    has to actually stop -- an abandoned round would otherwise keep taking the
    session lock every few seconds for a user who has walked away."""
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()

    probes = 0

    async def probe() -> str | None:
        nonlocal probes
        probes += 1
        return "6B"  # never changes, so the round would run to timeout

    # setattr, not assignment: the stub stands in for a bound method.
    setattr(coordinator, "async_probe_cloud_courses", probe)  # noqa: B010
    result = await handler.async_step_cloud_wait()
    assert result["type"] == "progress"
    task = handler._cloud_task
    assert task is not None and not task.done()

    handler.async_remove()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()
    settled = probes
    await asyncio.sleep(0.05)
    assert probes == settled


async def test_guided_lists_the_names_assigned_so_far(hass: HomeAssistant):
    """The only orientation available on a long walk -- the programs still to
    do are unnamed by definition, so 'done so far' is all there is to show.
    It doubles as duplicate avoidance, since the form rejects a repeat."""
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)

    assert handler._cloud_progress_placeholders(coordinator)["named_list"] == "none yet"

    coordinator.apply_cloud_courses({"55": "Sports", "6B": "Jeans"}, "87")
    await _flush(hass)
    placeholders = handler._cloud_progress_placeholders(coordinator)
    assert placeholders["named"] == "2"
    assert placeholders["total"] == "9"
    # The appliance's own advertised order, not naming order: 6B precedes 55
    # in CloudExtraCourse_0A5C286B2D0C55301A.
    assert placeholders["named_list"] == "Jeans, Sports"


async def test_the_name_form_shows_the_other_names_while_typing(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"6B": "Jeans"}, "87")
    await _flush(hass)

    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()
    _stub_probe(coordinator, ["55"])
    await _run_wait(handler)

    form = await handler.async_step_cloud_name()
    assert form["description_placeholders"]["named_list"] == "Jeans"


async def test_guided_naming_never_clears_a_confirmed_download_course(hass: HomeAssistant):
    """A program needs both a name and the Download course before it can be
    offered. The guided name form says nothing about the course, and passing
    that silence through as None wiped a confirmed one -- so naming a program
    removed every already-named program from the cycle list."""
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"6B": "Jeans"}, "87")
    await _flush(hass)
    assert "cloud:6B" in _cycle_options(coordinator)

    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()
    _stub_probe(coordinator, ["55"])
    await _run_wait(handler)
    await handler.async_step_cloud_name({"name": "Sports"})
    await _flush(hass)

    assert coordinator.cloud_courses.snapshot()["download_course"] == "87"
    assert {"cloud:55", "cloud:6B"} <= set(_cycle_options(coordinator))


async def test_guided_setup_alone_produces_a_selectable_cycle(hass: HomeAssistant):
    """The walk has to stand on its own: someone who only ever uses guided
    setup must end up with something in the cycle list. So the first name
    form also asks for the Download course, prefilled from what was just
    observed, and drops the field once it is confirmed."""
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()
    _stub_probe(coordinator, ["55"])
    await _run_wait(handler)

    first = await handler.async_step_cloud_name()
    assert "download_course" in str(first["data_schema"].schema)
    # Prefilled once a load has actually been watched -- in real use the
    # guided probe loop feeds observe() and produces exactly this.
    _observe_program_load(coordinator, SPORTS)
    assert handler._cloud_course == "87"

    await handler.async_step_cloud_name({"name": "Sports", "download_course": "87"})
    await _flush(hass)
    assert "cloud:55" in _cycle_options(coordinator)

    # Confirmed now, so the next program is asked for its name only.
    handler._cloud_slot = "6B"
    second = await handler.async_step_cloud_name()
    assert "download_course" not in str(second["data_schema"].schema)


async def test_guided_rejects_a_download_course_the_device_does_not_offer(hass: HomeAssistant):
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()
    _stub_probe(coordinator, ["55"])
    await _run_wait(handler)

    result = await handler.async_step_cloud_name({"name": "Sports", "download_course": "FF"})
    assert result["errors"] == {"base": "cloud_course_unknown_course"}
    assert coordinator.cloud_courses.snapshot()["download_course"] is None


async def test_guided_rejects_a_name_another_program_already_has(hass: HomeAssistant):
    """Guided setup submits one program at a time, so a within-form check
    can't see the others. Two programs sharing a label resolve to whichever
    option comes first, meaning picking the second would run the first one's
    payload."""
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"6B": "Sports"}, "87")
    await _flush(hass)

    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()
    _stub_probe(coordinator, ["55"])
    await _run_wait(handler)

    result = await handler.async_step_cloud_name({"name": "Sports"})
    await _flush(hass)
    assert result["errors"] == {"base": "cloud_course_name_duplicate"}
    assert coordinator.cloud_courses.named() == {"6B": "Sports"}


async def test_renaming_a_program_to_its_own_name_is_allowed(hass: HomeAssistant):
    """The slot being edited is excluded from the taken set, or confirming an
    unchanged name would reject itself."""
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)

    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()
    _stub_probe(coordinator, ["55"])
    await _run_wait(handler)

    result = await handler.async_step_cloud_name({"name": "Sports"})
    assert result["type"] == "progress"  # accepted, straight back to waiting


async def test_the_timeout_screen_fills_in_its_counters(hass: HomeAssistant):
    """Its copy interpolates the same placeholders as the other screens, so
    without them the dialog renders literal braces."""
    coordinator = await _coordinator(hass)
    coordinator.apply_cloud_courses({"55": "Sports"}, "87")
    await _flush(hass)

    handler = await _options_handler(hass, coordinator)
    result = await handler.async_step_cloud_timeout()
    assert result["description_placeholders"]["named"] == "1"
    assert result["description_placeholders"]["named_list"] == "Sports"


async def test_guided_ignores_a_payload_the_store_would_not_record(hass: HomeAssistant):
    """The probe reports whatever payload is loaded; observe() declines one
    whose slot the device doesn't advertise. Offering to name that would take
    a name and discard it -- there is no record to hang it on."""
    coordinator = await _coordinator(hass)
    handler = await _options_handler(hass, coordinator)
    await handler.async_step_cloud_guided()

    # 'ZZ' is not in this appliance's CloudExtraCourse_ list.
    _stub_probe(coordinator, ["ZZ"])
    result = await _run_wait(handler)
    assert result["step_id"] == "cloud_timeout"


async def test_the_prefilled_course_is_always_one_the_dropdown_offers(hass: HomeAssistant):
    """An observed candidate the appliance's course list no longer contains
    would prefill a value the selector rejects, failing validation on
    something the user never chose."""
    coordinator = await _coordinator(hass)
    coordinator.cloud_courses._candidates["FF"] = 99  # not in the course list
    handler = await _options_handler(hass, coordinator)

    offered = handler._cloud_course_selector(coordinator).config["options"]
    assert handler._cloud_course is None or handler._cloud_course in offered
