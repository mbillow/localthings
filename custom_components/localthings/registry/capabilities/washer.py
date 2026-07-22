"""Capabilities specific to washer appliances (Samsung DA_WM_TP1-class
front-load washers).

Resources verified against two live WW90DG6U25LEU4 dumps (Table_02 course
family). Washers never report `oneUiVersion` -- see
`registry/by_type/__init__.py`'s `for_device_by_model()` for the fallback
detection this device type requires.

The shared laundry surface -- power/kids-lock/remote-control OCF+vendor
fallback pairs, buzzer, energy meter, job-beginning-status, and the
/course/vs/0 cycle-select machinery -- lives in laundry.py. Only washer-
specific controls (wash settings, drum-clean tracking, dispenser dosing) are
here; they read washer-only fields off the same shared /course/vs/0 options
array.
"""
from datetime import datetime, timezone

from ..capability import Capability
from ..entities import BinarySensorDesc, SelectDesc, SensorDesc
from .laundry import cycle_select, hex_pairs, option_value, replace_in_options

# ---------------------------------------------------------------------------
# Course_XX hex codes. 23 of the codes named in strings.json/translations
# under entity.select.washer_cycle.state.<id, lowercased> were captured
# from a live WW90DG6U25LEU4's x.com.samsung.da.editCourseList
# (EditCourseList_1C1D211B1E29243328262722202325322F2E30662D8F96), matched
# positionally against a Slovak-UI user's screenshots of their app's course
# list (same order, same count -- see issue #2) and cross-checked against
# the printed user manual's course table (confirming e.g. '8F' as 'Intense
# Cold', not the position-adjacent-looking but distinct 'Mixed Load', a
# cycle the manual marks "applicable models only" and that does not appear
# in this device's editCourseList -- nor does 'AI Wash', also "applicable
# models only"). FixedCourseList_1C29 (the two courses always pinned in the
# app) maps to '1C'/'29' = Eco 40-60 and Drum Clean+, which matches what
# you'd expect to be pinned (default cycle + maintenance cycle),
# corroborating the positional match.
#
# A further 5 codes -- '36' Wash+Dry, '37' Air Wash, '38' Cotton Dry,
# '39' Synthetics Dry, and a second, distinct '1F' Intense Cold (not the
# same code as '8F' above) -- came from a WD90T654DBN/S1 washer/dryer
# combo's editCourseList and were named from that user's app screenshot
# (issue #22). Combo units carry their own course set, so these codes
# don't imply anything about '1F' on a plain washer.
#
# No static fallback list of those codes is kept here, deliberately: other
# washer models have a different actual course set (a second dump's active
# course, '65', isn't even in the list above; models with 'AI Wash'/'Mixed
# Load' -- both "applicable models only" per the manual -- would have yet
# another set), so hardcoding one device's list would show/hide the wrong
# options on a different model. laundry.cycle_options() reads only the live
# x.com.samsung.da.editCourseList; if a device doesn't populate that
# resource, the cycle select isn't created at all (see cycle_select's
# exists_fn). x.com.samsung.da.options' MostUsed_* entry was considered as a
# fallback source (its first byte reliably equals the currently-selected
# Course_XX on both dumps we have), but the bytes after that don't
# correspond to any confirmed course code on either device -- e.g. dump 1's
# MostUsed_1C8410923FA67F00000000000000 decodes to
# ['1C','84','10','92','3F','A6','7F',...] and only '1C' is a real code --
# so it isn't trustworthy as a list of selectable courses and isn't used.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# /washer/vs/0 -- wash temperature, spin speed, rinse cycle count
#
# Despite the shared href, this is unrelated to dryer.DRYER_SETTINGS (also
# bound to '/washer/vs/0') -- an artifact of Samsung reusing the same OCF
# path for different device families. Only one of the two ever binds for a
# given device, since dryer and washer are separate by_type registries.
# ---------------------------------------------------------------------------

WASHER_SETTINGS = Capability(
    href='/washer/vs/0',
    entities=(
        SelectDesc(key='wash_temperature', field='x.com.samsung.da.waterTemperature',
                   name='Wash temperature', icon='mdi:thermometer-water',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedWaterTemperature',
                   write_fn=lambda p, rep, href=None: (
                       ['washer', 'vs', '0'], {'x.com.samsung.da.waterTemperature': p})),
        SelectDesc(key='spin_speed', field='x.com.samsung.da.spinLevel',
                   name='Spin speed', icon='mdi:sync',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedSpinLevel',
                   write_fn=lambda p, rep, href=None: (
                       ['washer', 'vs', '0'], {'x.com.samsung.da.spinLevel': p})),
        SelectDesc(key='rinse_cycles', field='x.com.samsung.da.rinseCycles',
                   name='Rinse cycles', icon='mdi:water-sync',
                   entity_category='config',
                   options_field='x.com.samsung.da.supportedRinseCycles',
                   write_fn=lambda p, rep, href=None: (
                       ['washer', 'vs', '0'], {'x.com.samsung.da.rinseCycles': p})),
        # Washer/dryer combo units carry a dryLevel field on the wash
        # resource itself (no separate dryer device/course) -- see issue
        # #22. Self-gates off on plain washers, which never report
        # supportedDryLevel.
        SelectDesc(key='dry_level', field='x.com.samsung.da.dryLevel',
                   name='Dry level', icon='mdi:tumble-dryer',
                   entity_category='config',
                   translation_key='washer_dry_level',
                   options_field='x.com.samsung.da.supportedDryLevel',
                   exists_fn=lambda rep, resources: bool(
                       rep.get('x.com.samsung.da.supportedDryLevel')),
                   write_fn=lambda p, rep, href=None: (
                       ['washer', 'vs', '0'], {'x.com.samsung.da.dryLevel': p})),
    ),
)

# ---------------------------------------------------------------------------
# /course/vs/0 -- the cycle select is the shared laundry.cycle_select; the
# drum-clean and dispenser-dosing entities below are washer-specific reads off
# the same options array.
# ---------------------------------------------------------------------------


# Drum Clean+ maintenance tracking, from the same options[] array as the
# selected course. DrumCleanProposal_<N> is the wash-cycle interval between
# recommended cleans; WashingTimes_<N> is the count since the last one --
# their difference is exactly the "N cycles until due" figure the Samsung
# app shows (verified: DrumCleanProposal_40 - WashingTimes_3 == 37, matching
# a live app screenshot's "Potreba cistenia po 37 cykloch"). DrumCleanLog_
# is the last-clean timestamp (verified against the same screenshot's "10
# days ago"); no explicit timezone field accompanies it on this resource,
# so it's treated as UTC, matching this integration's convention for other
# bare ISO datetime fields (see fridge.py's night-light schedule comment).
def _drum_clean_cycles_remaining(rep):
    opts = rep.get('x.com.samsung.da.options') or []
    proposal = option_value(opts, 'DrumCleanProposal')
    washed = option_value(opts, 'WashingTimes')
    if proposal is None or washed is None:
        return None
    try:
        return max(int(proposal) - int(washed), 0)
    except ValueError:
        return None


def _drum_clean_last_cleaned(rep):
    raw = option_value(rep.get('x.com.samsung.da.options'), 'DrumCleanLog')
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# Detergent/softener auto-dispense dosing, from the same options[] array
# (issue #9). '<Prefix>LevelCtrl_<code>' is the selected dose quantity;
# '<Prefix>Level2Ctrl_<code>' is a second dial -- water hardness for
# detergent, concentration for softener -- matching the SmartThings app's
# two-field dispenser screens ("Distributeur de lessive": Quantité + Dureté
# de l'eau; "Distributeur d'adoucissant": Quantité + Concentration, per
# issue #9's screenshots). 'Supported<Prefix>Ctrl_<hexpairs>' lists the
# valid raw codes for its field, same hex-pair shape as EditCourseList.
# '<Prefix>Alarm_<On/Off>' is a low-reservoir warning flag.
#
# Label mapping (entity.select.washer_dosing_quantity/washer_detergent_
# water_hardness/washer_softener_concentration in strings.json) is an
# assumed, not cross-device-verified, reading of the single issue #9 dump +
# screenshots: LevelCtrl's 4 codes as None/Low/Medium/High (00 has no
# on-screen equivalent -- the app's Quantité picker only offers
# Faible/Moyen/Élevé, i.e. codes 01-03; 00 is assumed to be what
# "Activation" off collapses to) matches DetergentLevelCtrl_3/
# SoftenerLevelCtrl_3 = "Élevé" on both dispensers. Level2Ctrl's 3 codes as
# Soft/Medium/Hard for detergent (Dureté de l'eau: Douce/Moyenne/Dure)
# matches DetergentLevel2Ctrl_2 = "Moyenne". The same 3-code shape as
# 1x/2x/3x for softener concentration does *not* cleanly match
# SoftenerLevel2Ctrl_2 against the screenshot's "3x" -- assumed to be a
# setting the user changed in the app between the dump (issue body) and the
# screenshots (a later comment), not a different code scheme, since it's
# otherwise identical in shape to the detergent side. Revisit if a second
# device's dump contradicts this.
def _supported_level_options(resources, prefix):
    rep = resources.get('/course/vs/0') or {}
    raw = option_value(rep.get('x.com.samsung.da.options'), f'Supported{prefix}')
    return hex_pairs(raw) if raw else []


def _level_options(prefix):
    return lambda resources: _supported_level_options(resources, prefix)


def _dosing_level(prefix):
    """Current dose code, normalized to the `Supported<prefix>` code format.

    The device reports the selected level as `<prefix>_<code>` with the code
    un-padded (e.g. '3'), but the valid codes -- which are also this select's
    options and its translation keys -- come from `Supported<prefix>_<hexpairs>`
    as zero-padded hex pairs (e.g. '03'). Left as '3', the current value sits
    outside the select's own option list, so HA renders it 'unknown' (issue #9).
    Resolve it to the supported code with the same integer value so
    current_option matches an option (and its translation)."""
    def fn(rep):
        opts = rep.get('x.com.samsung.da.options')
        raw = option_value(opts, prefix)
        if raw is None:
            return None
        supported_raw = option_value(opts, f'Supported{prefix}')
        try:
            target = int(raw, 16)
        except (TypeError, ValueError):
            return raw
        for code in hex_pairs(supported_raw) if supported_raw else []:
            try:
                if int(code, 16) == target:
                    return code
            except (TypeError, ValueError):
                continue
        return raw
    return fn


def _level_write(prefix):
    def write(p, rep, href=None):
        opts = list(rep.get('x.com.samsung.da.options') or [])
        if not opts:
            return None
        # `p` is the zero-padded supported code the UI selected (e.g. '03');
        # the device stores the level un-padded (e.g. '3'), matching how it
        # reports it, so write it back in that native shape.
        try:
            native = format(int(p, 16), 'X')
        except (TypeError, ValueError):
            native = p
        return ['course', 'vs', '0'], {
            'x.com.samsung.da.options': replace_in_options(opts, prefix, native),
        }
    return write


def _dosing_low(prefix):
    return lambda rep: option_value(
        rep.get('x.com.samsung.da.options'), prefix) not in (None, 'Off')


def _dosing_alarm_exists(prefix):
    return lambda rep, resources: option_value(
        rep.get('x.com.samsung.da.options'), prefix) is not None


WASHER_COURSE = Capability(
    href='/course/vs/0',
    entities=(
        cycle_select(translation_key='washer_cycle', icon='mdi:washing-machine'),
        SensorDesc(key='drum_clean_cycles_remaining', name='Drum clean due in',
                   icon='mdi:washing-machine-alert', unit='cycles',
                   state_class='measurement',
                   exists_fn=lambda rep, resources: _drum_clean_cycles_remaining(rep) is not None,
                   rep_fn=_drum_clean_cycles_remaining),
        SensorDesc(key='drum_clean_last_cleaned', name='Drum last cleaned',
                   icon='mdi:calendar-clock', device_class='timestamp',
                   entity_category='diagnostic',
                   exists_fn=lambda rep, resources: _drum_clean_last_cleaned(rep) is not None,
                   rep_fn=_drum_clean_last_cleaned),
        SelectDesc(key='detergent_quantity', name='Detergent quantity', icon='mdi:cup-water',
                   translation_key='washer_dosing_quantity',
                   entity_category='config',
                   options=_level_options('DetergentLevelCtrl'),
                   exists_fn=lambda rep, resources: bool(
                       _level_options('DetergentLevelCtrl')(resources)),
                   rep_fn=_dosing_level('DetergentLevelCtrl'),
                   write_fn=_level_write('DetergentLevelCtrl')),
        SelectDesc(key='detergent_water_hardness', name='Detergent water hardness',
                   icon='mdi:water-opacity',
                   translation_key='washer_detergent_water_hardness',
                   entity_category='config',
                   options=_level_options('DetergentLevel2Ctrl'),
                   exists_fn=lambda rep, resources: bool(
                       _level_options('DetergentLevel2Ctrl')(resources)),
                   rep_fn=_dosing_level('DetergentLevel2Ctrl'),
                   write_fn=_level_write('DetergentLevel2Ctrl')),
        SelectDesc(key='softener_quantity', name='Softener quantity', icon='mdi:flask-outline',
                   translation_key='washer_dosing_quantity',
                   entity_category='config',
                   options=_level_options('SoftenerLevelCtrl'),
                   exists_fn=lambda rep, resources: bool(
                       _level_options('SoftenerLevelCtrl')(resources)),
                   rep_fn=_dosing_level('SoftenerLevelCtrl'),
                   write_fn=_level_write('SoftenerLevelCtrl')),
        SelectDesc(key='softener_concentration', name='Softener concentration',
                   icon='mdi:flask-plus-outline',
                   translation_key='washer_softener_concentration',
                   entity_category='config',
                   options=_level_options('SoftenerLevel2Ctrl'),
                   exists_fn=lambda rep, resources: bool(
                       _level_options('SoftenerLevel2Ctrl')(resources)),
                   rep_fn=_dosing_level('SoftenerLevel2Ctrl'),
                   write_fn=_level_write('SoftenerLevel2Ctrl')),
        BinarySensorDesc(key='detergent_low', name='Detergent low',
                         icon='mdi:alert-circle-outline', device_class='problem',
                         exists_fn=_dosing_alarm_exists('DetergentAlarm'),
                         rep_fn=_dosing_low('DetergentAlarm')),
        BinarySensorDesc(key='softener_low', name='Softener low',
                         icon='mdi:alert-circle-outline', device_class='problem',
                         exists_fn=_dosing_alarm_exists('SoftenerAlarm'),
                         rep_fn=_dosing_low('SoftenerAlarm')),
    ),
)
