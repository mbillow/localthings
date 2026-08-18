import cbor2

from custom_components.localthings.registry.identity import (
    DeviceIdentity,
    device_display_name,
    is_usable_device_id,
    ocf_device_key,
    read_identity,
    resolve_device_key,
)


class FakeSession:
    def __init__(self, table):
        self.table = table  # tuple(path) -> rep dict

    def get(self, path, timeout=10.0):
        rep = self.table.get(tuple(path))
        if rep is None:
            return 0x84, b""  # 4.04 not found
        return 0x45, cbor2.dumps(rep)


def test_read_identity_from_oic_p_and_d():
    sess = FakeSession(
        {
            ("oic", "p"): {"mnmn": "Samsung Electronics", "mnmo": "RF9000B"},
            ("oic", "d"): {"n": "Family Hub"},
        }
    )
    ident = read_identity(sess, serial="ABC123")
    assert ident.manufacturer == "Samsung Electronics"
    assert ident.model == "RF9000B"
    assert ident.name == "Family Hub"
    assert ident.serial == "ABC123"


def test_read_identity_tolerates_missing_resources():
    ident = read_identity(FakeSession({}), serial=None)
    assert ident.manufacturer == "Samsung"
    assert ident.model == ""
    assert ident.serial is None
    assert ident.device_types == ()
    assert ident.raw == {"/oic/p": {}, "/oic/d": {}, "/oic/res": []}


def test_read_identity_captures_oic_d_device_types():
    """/oic/d's `rt` is OCF's own device-type declaration -- captured so
    diagnostics can show whether real hardware populates it usefully."""
    sess = FakeSession(
        {
            ("oic", "d"): {
                "n": "Living Room AC",
                "rt": ["oic.wk.d", "oic.d.airconditioner"],
            },
        }
    )
    ident = read_identity(sess, serial=None)
    assert ident.device_types == ("oic.wk.d", "oic.d.airconditioner")


def test_read_identity_normalizes_scalar_and_malformed_rt():
    """Firmware that reports a bare string, or a non-list, must not explode."""
    assert read_identity(
        FakeSession({("oic", "d"): {"rt": "oic.d.refrigerator"}}), None
    ).device_types == ("oic.d.refrigerator",)
    assert read_identity(FakeSession({("oic", "d"): {"rt": 42}}), None).device_types == ()
    assert read_identity(
        FakeSession({("oic", "d"): {"rt": ["oic.wk.d", 7, None]}}), None
    ).device_types == ("oic.wk.d",)


def test_read_identity_keeps_raw_payloads_for_diagnostics():
    sess = FakeSession(
        {
            ("oic", "p"): {"mnmn": "Samsung Electronics", "mnmo": "RF9000B"},
            ("oic", "d"): {"n": "Family Hub", "di": "abc-123"},
        }
    )
    ident = read_identity(sess, serial=None)
    oic_p = ident.raw["/oic/p"]
    assert isinstance(oic_p, dict)
    assert oic_p["mnmo"] == "RF9000B"
    oic_d = ident.raw["/oic/d"]
    assert isinstance(oic_d, dict)
    assert oic_d["di"] == "abc-123"


def test_read_identity_captures_oic_res_links():
    """/oic/res is OCF's discovery endpoint. Real hardware (issue #177
    follow-up, a TP1X_REF_21K fridge dump) groups the response by `di`: one
    entry per logical Device, each carrying its own `links` array -- not a
    flat array of individually-`di`-tagged links. Every entry that dump
    returned had its discoverable policy bit set (`bm`'s bit 0); the whole
    x.com.samsung.da.* tree (including /device/0 itself) did not appear at
    all, meaning it's registered non-discoverable and simply invisible to
    this endpoint -- see _SPECULATIVE_DEVICE_INDICES' docstring for why that
    motivated probing /device/1 and /device/2 directly instead."""
    sess = FakeSession(
        {
            ("oic", "res"): [
                {
                    "di": "aaaa",
                    "links": [
                        {
                            "href": "/oic/d",
                            "rt": ["oic.wk.d", "oic.d.refrigerator"],
                            "p": {"bm": 1},
                        },
                        {"href": "/oic/sec/doxm", "rt": ["oic.r.doxm"], "p": {"bm": 1}},
                    ],
                },
            ],
        }
    )
    ident = read_identity(sess, serial=None)
    assert ident.raw["/oic/res"] == [
        {
            "di": "aaaa",
            "links": [
                {"href": "/oic/d", "rt": ["oic.wk.d", "oic.d.refrigerator"], "p": {"bm": 1}},
                {"href": "/oic/sec/doxm", "rt": ["oic.r.doxm"], "p": {"bm": 1}},
            ],
        },
    ]


def test_read_identity_tolerates_malformed_oic_res():
    """A single Property map instead of an array (or anything else
    non-list-shaped) must not explode -- same defensive posture as
    _device_types' handling of a malformed /oic/d rt."""
    ident = read_identity(FakeSession({("oic", "res"): {"not": "a list"}}), serial=None)
    assert ident.raw["/oic/res"] == []


# ---------------------------------------------------------------------------
# The device key: which identity field registry keys are minted from (#381)
# ---------------------------------------------------------------------------


def _identity(
    *,
    serial: str | None = None,
    device_id: str | None = None,
    platform_id: str | None = None,
) -> DeviceIdentity:
    """A DeviceIdentity carrying only the fields the key chain reads."""
    return DeviceIdentity(
        manufacturer="Samsung",
        model="M",
        name="N",
        serial=serial,
        device_id=device_id,
        platform_id=platform_id,
    )


def test_read_identity_captures_the_ocf_uuids_as_named_fields():
    """`di`/`pi` are what resolve_device_key mints keys from, so they are
    lifted out of `raw` rather than dug back out of it at every call site."""
    sess = FakeSession(
        {
            ("oic", "p"): {"pi": "ccfd73b3-aeb4-792a-1100-68f06f5d603b"},
            ("oic", "d"): {"di": "3771f8bf-c184-3a2d-d885-e4c9818736d2"},
        }
    )
    ident = read_identity(sess, serial=None)
    assert ident.device_id == "3771f8bf-c184-3a2d-d885-e4c9818736d2"
    assert ident.platform_id == "ccfd73b3-aeb4-792a-1100-68f06f5d603b"


def test_read_identity_ignores_non_string_uuids():
    """Firmware answering with a number or a map must not put a non-string
    into a field that goes on to be string-formatted into a unique_id."""
    ident = read_identity(FakeSession({("oic", "d"): {"di": 42}, ("oic", "p"): {"pi": {}}}), None)
    assert ident.device_id is None
    assert ident.platform_id is None


def test_two_units_sharing_a_serial_get_distinct_keys():
    """Issue #381 exactly: two Samsung air purifiers of the same model ship
    the identical, well-formed serialNum 'BS7SP9AW400114A', so keying on it
    collapsed them onto one identity and the second was refused as already
    configured. Their `di` differs, which is what makes them separable."""
    shared_serial = "BS7SP9AW400114A"
    first = resolve_device_key(
        _identity(device_id="ccfd73b3-aeb4-792a-1100-68f06f5d603b"), shared_serial, "192.168.0.3"
    )
    second = resolve_device_key(
        _identity(device_id="3771f8bf-c184-3a2d-d885-e4c9818736d2"), shared_serial, "192.168.0.14"
    )
    assert first != second
    assert shared_serial not in (first, second)


def test_platform_id_is_the_fallback_when_oic_d_is_unreadable():
    ident = _identity(device_id=None, platform_id="ccfd73b3-aeb4-792a-1100-68f06f5d603b")
    assert resolve_device_key(ident, "REAL-SERIAL", "10.0.0.1") == (
        "ccfd73b3-aeb4-792a-1100-68f06f5d603b"
    )


def test_device_id_wins_over_platform_id():
    """`pi` is platform-scoped, so a board hosting more than one logical OCF
    device shares it -- the collision this exists to prevent."""
    ident = _identity(device_id="dddddddd-0000-1111-2222-333333333333", platform_id="shared-plat")
    assert resolve_device_key(ident, "REAL-SERIAL", "10.0.0.1") == (
        "dddddddd-0000-1111-2222-333333333333"
    )


def test_falls_back_to_the_serial_then_the_host():
    """A board that answers neither OCF resource lands exactly where it did
    before any of this existed -- no regression for existing hardware."""
    assert resolve_device_key(None, "REAL-SERIAL", "10.0.0.1") == "REAL-SERIAL"
    assert resolve_device_key(_identity(), "REAL-SERIAL", "10.0.0.1") == "REAL-SERIAL"
    # ...and a placeholder serial still resolves to the host (#83/#189).
    assert resolve_device_key(_identity(), "Nothing(SVC)", "10.0.0.1") == "10.0.0.1"


def test_the_key_is_case_normalized():
    """The stored key is compared against a freshly polled one on every
    poll; firmware that changed case between reads would otherwise look
    like a different appliance every time."""
    ident = _identity(device_id="CCFD73B3-AEB4-792A-1100-68F06F5D603B")
    assert resolve_device_key(ident, None, "10.0.0.1") == "ccfd73b3-aeb4-792a-1100-68f06f5d603b"


def test_the_nil_uuid_is_not_an_identity():
    """OCF's unset UUID is identical on every unit that never had one
    assigned -- the #189 failure mode on a new field. Its dashes stop
    is_placeholder_serial's repeated-digit rule from seeing it, so it needs
    its own check; falling through to the serial is the right answer."""
    ident = _identity(device_id="00000000-0000-0000-0000-000000000000")
    assert resolve_device_key(ident, "REAL-SERIAL", "10.0.0.1") == "REAL-SERIAL"
    assert not is_usable_device_id("00000000-0000-0000-0000-000000000000")


def test_known_junk_disqualifies_a_uuid_the_same_way_it_does_a_serial():
    """A board firmware-flashed with 'Nothing(SVC)' in one identity field is
    not a board to trust in another."""
    assert not is_usable_device_id("Nothing(SVC)")
    assert not is_usable_device_id("FFFFFFFFFFFFFFF")
    assert not is_usable_device_id("")
    assert not is_usable_device_id(None)
    assert is_usable_device_id("3771f8bf-c184-3a2d-d885-e4c9818736d2")


def test_ocf_device_key_reports_absence_rather_than_collapsing_to_the_serial():
    """The coordinator needs "the device said nothing" and "the device said
    this" to be different answers, so it never demotes a UUID-keyed entry
    onto a serial because one poll couldn't read /oic/d."""
    assert ocf_device_key(None) is None
    assert ocf_device_key(_identity(serial="REAL-SERIAL")) is None
    assert ocf_device_key(_identity(device_id="abc-123")) == "abc-123"


def test_the_device_name_is_the_device_type_alone():
    """The name is what HA slugifies into every entity_id on the device, so
    the board string that modelNum reports stays out of it -- it is still
    registered as the device's `model` (see coordinator.device_info)."""
    assert device_display_name("refrigerator") == "Samsung Refrigerator"
    assert device_display_name("range_hood") == "Samsung Range Hood"
    assert device_display_name(None) == "Samsung Appliance"


def test_registry_type_names_that_dont_title_case_into_english():
    """Two registry names are SmartThings/Samsung spellings, not what the
    appliance is called -- and this one is the user-visible half."""
    assert device_display_name("airconditioner") == "Samsung Air Conditioner"
    assert device_display_name("ehs") == "Samsung Heat Pump"
