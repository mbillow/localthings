"""Capabilities specific to the dryer family (Samsung DA_WM_TP1/TP2-class).

Dryer-specific controls only. The shared laundry surface -- power/kids-lock/
remote-control fallback pairs, buzzer, energy meter, job-beginning-status, and
the /course/vs/0 cycle select -- lives in laundry.py.

  /washer/vs/0   -> DRYER_SETTINGS (dryLevel, dryTime, dryerType, wrinklePrevent)
  /course/vs/0   -> DRYER_COURSE (shared cycle select; see below)
  /diagnosis/vs/0 -> DRYER_DIAGNOSIS
"""

from ..capability import Capability
from ..entities import SelectDesc, SensorDesc, SwitchDesc
from .common import diagnosis_status
from .laundry import cycle_select, drum_clean_cycles_remaining, drum_clean_last_cleaned


def _wrinkle_write(p, rep, href=None):
    if p not in ("On", "Off"):
        return None
    return ["washer", "vs", "0"], {"x.com.samsung.da.wrinklePrevent": p}


DRYER_SETTINGS = Capability(
    href="/washer/vs/0",
    poll_tier="warm",
    entities=(
        # dryLevel here is a dryness dial with a live supportedDryLevel
        # list, in one of two vocabularies: Damp/Less/Normal/More/Very on
        # the TP1_21 dryers, or a numeric 1/2/3 on both DV5000T (TP2_20)
        # and DV6800N (A51_20). Hence its own translation_key rather than
        # washer.py's washer_dry_level, whose state table is that field's
        # *other* meaning on a combo (minutes, "30" -> "30 min").
        #
        # The write was exercised on a DV5000T, which reports
        # isModelSettingWithoutSC true and so takes it with Smart Control
        # off (see common.remote_control_required_for_write).
        #
        # Deliberately field-gated rather than carrying washer.py's
        # exists_fn on supportedDryLevel: there the gate discriminates a
        # combo from a plain washer, but every dryer has a dry level, so
        # here it would only suppress the entity -- including on a rep that
        # is merely a stub at discovery (entity._is_included) and would
        # have populated on the next sub-poll.
        #
        # No entity_category either, unlike washer.py's: this is a per-load
        # choice made alongside the cycle, not device configuration, so it
        # belongs in Controls next to the cycle select and wrinkle_prevent
        # -- and that is where the sensor it replaces already sat.
        SelectDesc(
            key="dry_level",
            field="x.com.samsung.da.dryLevel",
            icon="mdi:tumble-dryer",
            translation_key="dryer_dry_level",
            options_field="x.com.samsung.da.supportedDryLevel",
            write_fn=lambda p, rep, href=None: (
                ["washer", "vs", "0"],
                {"x.com.samsung.da.dryLevel": p},
            ),
        ),
        SensorDesc(key="dry_time", field="x.com.samsung.da.dryTime", icon="mdi:timer"),
        SensorDesc(
            key="dryer_type",
            field="x.com.samsung.da.dryerType",
            icon="mdi:tumble-dryer",
            device_class="enum",
            # Only 'Electricity' confirmed across shipped fixtures (#366); an
            # unrecognized value still passes through raw via sensor.py's
            # options property rather than breaking the entity.
            options=("electricity",),
            value_fn=lambda v: v.lower() if isinstance(v, str) else v,
        ),
        SwitchDesc(
            key="wrinkle_prevent",
            field="x.com.samsung.da.wrinklePrevent",
            icon="mdi:iron",
            value_fn=lambda v: v == "On",
            write_fn=_wrinkle_write,
        ),
    ),
)

# /course/vs/0 -- cycle selection, shared with washer/dishwasher via
# laundry.cycle_select. Course display names live in translations under
# entity.select.dryer_cycle (Table_03, DV5000-class). Codes '01' Normal and
# '06' Time dry were confirmed on a DVE50A8600V/A3 by selecting each cycle
# on the appliance and reading back the raw code (issue #80); '51' Eco
# Cotton, '53' AI Dry+, and '4e' Self Dry the same way on a DV90DG6845LHU5
# (issue #244). /st/dryercourse/vs/0 re-encodes the same selected course
# and is ignored (ignored.py), mirroring /st/washercourse/vs/0 for washers.
#
# dryer_cycle_table_00 is a separate, older course-code family reported by
# a DVE45R6300W/A3 (issue #357), confirmed the same way: the reporter
# selected each cycle on the appliance and read back the resulting raw
# code. It shares no codes with Table_03 above -- 'a5' Bedding here and
# '01' Normal are both table-scoped, so a Table_03 dryer never picks up a
# Table_00 label or vice versa (see laundry.cycle_select's table_href).
# A DV6800N -- same DA_WM_A51_20_COMMON board, also Table_00 -- confirmed
# 14 more courses the same way (issue #394); its /course/vs/0 supportedOptions
# only advertises a different subset of this same table (each model exposes
# whichever courses its hardware supports), not a conflicting code family --
# the one code both reporters confirmed, 'a5', means Bedding on both. Folded
# into the same catalog entry below rather than a new one.
#
# Drum Clean+ maintenance tracking (issue #258) reuses washer.py's
# DrumCleanProposal_/WashingTimes_/DrumCleanLog_ tokens on this same
# options[] array -- see laundry.drum_clean_cycles_remaining/
# drum_clean_last_cleaned. No separate heat-exchanger-clean tracking was
# found on either dump #258 supplied, so if the app surfaces that reminder,
# it isn't computed from anything this integration can read locally.
DRYER_COURSE = Capability(
    href="/course/vs/0",
    entities=(
        cycle_select(
            translation_key="dryer_cycle",
            icon="mdi:tumble-dryer",
            table_href="/st/dryercourse/vs/0",
        ),
        SensorDesc(
            key="drum_clean_cycles_remaining",
            unit="cycles",
            icon="mdi:tumble-dryer-alert",
            state_class="measurement",
            exists_fn=lambda rep, resources: drum_clean_cycles_remaining(rep) is not None,
            rep_fn=drum_clean_cycles_remaining,
        ),
        SensorDesc(
            key="drum_clean_last_cleaned",
            device_class="timestamp",
            icon="mdi:calendar-clock",
            entity_category="diagnostic",
            exists_fn=lambda rep, resources: drum_clean_last_cleaned(rep) is not None,
            rep_fn=drum_clean_last_cleaned,
        ),
    ),
)

DRYER_DIAGNOSIS = Capability(
    href="/diagnosis/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="diagnosis",
            field="x.com.samsung.da.diagnosisStart",
            entity_category="diagnostic",
            device_class="enum",
            options=("ready",),
            value_fn=diagnosis_status,
        ),
    ),
)
