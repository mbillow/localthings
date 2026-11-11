"""What the second element of a /sensors/vs/0 dust reading means, and why
it is what confirms Dust/FineDust/SuperFineDust are PM10/PM2.5/PM1.

`x.com.samsung.da.value` is `[concentration, grade]` on the fields that
carry a magnitude and `[grade]` on Odor/CleanLevel, which are grades
already. Only the range hood binds index 1 (range_hood._particle_grade),
since its floor differs by board family. It is also the device's own
opinion about its own readings, which makes it the one piece of evidence
for the PM mapping that doesn't depend on Samsung's field names or on a
user's screenshot.

These assertions read the shipped fixtures rather than restating numbers,
so a re-captured dump that contradicts the mapping fails here instead of
silently weakening the argument in air_purifier.py's comment.
"""

import json
import pathlib

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
DUST_TYPES = ("Dust", "FineDust", "SuperFineDust")


def _items(fixture: str):
    dump = json.loads((FIXTURES / f"{fixture}_device.json").read_text(encoding="utf-8"))
    for entry in dump["device0"]:
        if entry.get("href") == "/sensors/vs/0":
            return {
                item.get("x.com.samsung.da.type"): item.get("x.com.samsung.da.value")
                for item in entry.get("rep", {}).get("x.com.samsung.da.items") or []
            }
    raise AssertionError(f"{fixture} has no /sensors/vs/0")


def _fixtures_reporting_sensors():
    for path in sorted(FIXTURES.glob("*_device.json")):
        dump = json.loads(path.read_text(encoding="utf-8"))
        entries = dump.get("device0")
        if not isinstance(entries, list):
            continue
        if any(e.get("href") == "/sensors/vs/0" for e in entries):
            name = path.name.removesuffix("_device.json")
            if any(t in _items(name) for t in DUST_TYPES):
                yield name


def test_magnitude_fields_carry_a_grade_and_graded_fields_do_not():
    """The shape asymmetry is the whole argument for what index 1 is: the
    fields that already *are* grades have no second slot."""
    checked = 0
    for fixture in _fixtures_reporting_sensors():
        items = _items(fixture)
        for type_ in (*DUST_TYPES, "CO2"):
            if type_ in items:
                assert len(items[type_]) == 2, (fixture, type_, items[type_])
                checked += 1
        for type_ in ("Odor", "CleanLevel"):
            if type_ in items:
                assert len(items[type_]) == 1, (fixture, type_, items[type_])
    assert checked >= 30


def test_concentration_falls_with_particle_size_on_every_fixture():
    """PM10 >= PM2.5 >= PM1 by definition -- they are cumulative masses, so
    a violation would mean the three fields aren't nested size tiers at
    all."""
    for fixture in _fixtures_reporting_sensors():
        items = _items(fixture)
        if not all(t in items for t in DUST_TYPES):
            continue
        coarse, fine, finest = (int(items[t][0]) for t in DUST_TYPES)
        assert coarse >= fine >= finest, (fixture, coarse, fine, finest)


def test_the_same_reading_grades_differently_as_dust_than_as_superfinedust():
    """18 is one step above the grade floor as SuperFineDust but sits *at*
    the floor as Dust, on two families that both grade good air as 1.

    One shared threshold cannot produce both, so the firmware treats the
    coarse field as tolerating more than the fine one -- three scales
    ordered coarse-to-fine, which is what PM10/PM2.5/PM1 requires and what
    "all three are the same kind of reading" cannot explain.
    """
    monitor, hood = _items("air_monitor"), _items("range_hood")
    assert monitor["SuperFineDust"] == ["18", "2"]
    assert hood["Dust"] == ["18", "1"]
    # Both families put good air at grade 1, so the two grades are
    # comparable -- ARTIK051_TVTL's 0-based floor is the reason this
    # comparison is drawn between these two fixtures and not against it.
    assert monitor["Odor"] == ["1"]
    assert hood["CleanLevel"] == ["2"]
    assert _items("air_purifier")["Dust"] == ["11", "0"]


def test_grade_boundaries_bracket_the_korean_cai_bands():
    """Where each field crosses from its floor to the next grade lines up
    with the band that field's PM tier is graded on in Korea's CAI:
    PM10 breaks at 30/31, PM2.5 at 15/16. A PM1 reading has no standard
    index and is graded on PM2.5-like widths.
    """
    monitor, hood = _items("air_monitor"), _items("range_hood")
    # Dust: still at the floor at 18, above it at 31 -> boundary in (18, 31].
    assert (hood["Dust"], monitor["Dust"]) == (["18", "1"], ["31", "2"])
    # FineDust: at the floor at 14, above it at 23 -> boundary in (14, 23].
    assert (hood["FineDust"], monitor["FineDust"]) == (["14", "1"], ["23", "2"])
    # SuperFineDust: at the floor at 9, above it at 18 -> boundary in (9, 18],
    # strictly below where Dust's sits.
    assert (hood["SuperFineDust"], monitor["SuperFineDust"]) == (["9", "1"], ["18", "2"])


def test_clean_level_aggregates_the_per_field_grades():
    """CleanLevel is the highest per-field grade on every family except the
    range hood and one RAC, which report a higher CleanLevel than any dust
    grade -- those two fold in something this resource doesn't expose, so
    CleanLevel is never derived from the dust grades in code."""
    exceptions = {"range_hood", "airconditioner_tp1x_da_ac_rac_01011"}
    for fixture in _fixtures_reporting_sensors():
        items = _items(fixture)
        if "CleanLevel" not in items:
            continue
        grades = [int(v[1]) for v in items.values() if len(v) == 2]
        if not grades:
            continue
        aggregate = int(items["CleanLevel"][0])
        if fixture in exceptions:
            assert aggregate > max(grades), (fixture, aggregate, grades)
        else:
            assert aggregate == max(grades), (fixture, aggregate, grades)


def test_grade_floor_is_zero_based_on_artik051_tvtl_and_one_based_elsewhere():
    """Why index 1 has no shared descriptor: it would need a per-family
    offset to mean anything, so only the hood maps its own grades."""
    assert _items("air_purifier")["CleanLevel"] == ["0"]
    for fixture in ("air_monitor", "air_purifier_avt_ww", "air_purifier_vtww", "range_hood"):
        assert int(_items(fixture)["CleanLevel"][0]) >= 1, fixture
