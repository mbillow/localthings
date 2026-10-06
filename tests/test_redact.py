"""Tests for registry.redact — the safety net for diagnostics downloads."""

import json
import re
from pathlib import Path

from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.redact import REDACTED, redact_resources

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    data = json.loads((FIXTURES / name).read_text())
    return parse_device0_batch(data["device0"])


def test_redacts_known_sensitive_fields_in_dishwasher_dump():
    resources = _load("dishwasher_device.json")
    redacted = redact_resources(resources)

    info = redacted["/information/vs/0"]
    assert info["x.com.samsung.da.serialNum"] == REDACTED
    assert info["x.com.samsung.da.otnDUID"] == REDACTED

    wireless = redacted["/wirelessinfo/vs/0"]
    assert wireless["macaddressWiFi"] == REDACTED
    assert wireless["macaddressBLE"] == REDACTED

    provisioning = redacted["/voice/provisioning/vs/0"]
    headers = provisioning["voice.provisioning.headers"]
    assert headers["login_id"] == REDACTED
    deviceinfo = provisioning["voice.provisioning.deviceinfo"]
    assert deviceinfo["voice.provisioning.deviceinfo.accesstoken"] == REDACTED
    assert deviceinfo["voice.provisioning.deviceinfo.deviceid"] == REDACTED
    assert deviceinfo["voice.provisioning.deviceinfo.userid"] == REDACTED


def test_ordinary_state_fields_survive_untouched():
    resources = _load("dishwasher_device.json")
    redacted = redact_resources(resources)

    op_state = redacted["/operational/state/vs/0"]
    assert op_state["x.com.samsung.da.state"] == "Run"
    assert op_state["x.com.samsung.da.progress"] == "Finish"

    power = redacted["/power/vs/0"]
    assert power["x.com.samsung.da.power"] == "On"

    dishwasher = redacted["/dishwasher/vs/0"]
    assert dishwasher["x.com.samsung.da.sanitize"] == "On"
    assert dishwasher["x.com.samsung.da.rinseLevel"] == "4"

    alarms = redacted["/alarms/vs/0"]["x.com.samsung.da.items"]
    assert alarms[0]["x.com.samsung.da.code"] == "SNSF_Reached"


def test_redacts_known_sensitive_fields_in_refrigerator_dump():
    resources = _load("refrigerator_device.json")
    redacted = redact_resources(resources)

    info = redacted["/information/vs/0"]
    assert info["x.com.samsung.da.serialNum"] == REDACTED

    wireless = redacted["/wirelessinfo/vs/0"]
    assert wireless["macaddressWiFi"] == REDACTED
    assert wireless["macaddressBLE"] == REDACTED


def test_redact_resources_does_not_mutate_input():
    resources = _load("dishwasher_device.json")
    original_serial = resources["/information/vs/0"]["x.com.samsung.da.serialNum"]

    redact_resources(resources)

    assert resources["/information/vs/0"]["x.com.samsung.da.serialNum"] == original_serial


def test_keeps_ocf_identity_uuids_but_redacts_the_owner_set_name():
    """/oic/d's `di` and /oic/p's `pi` survive redaction.

    They are randomly-assigned per-unit UUIDs, not account data, and they
    are what the entry's registry keys are minted from (issue #381) -- a
    report that blanks them hides the identity every entity in it is named
    after, and can't answer the one question a duplicate-serial report
    exists to ask: whether two units differ here at all.

    `n` is the opposite case and stays redacted: free text the owner sets
    from the SmartThings app, so it can carry a person's name.
    """
    redacted = redact_resources(
        {
            "/oic/d": {
                "di": "ab-cd-ef",
                "n": "Marc's Fridge",
                "rt": ["oic.wk.d", "oic.d.refrigerator"],
            },
            "/oic/p": {"pi": "12-34-56", "mnmo": "RF9000B"},
        }
    )

    assert redacted["/oic/d"]["di"] == "ab-cd-ef"
    assert redacted["/oic/p"]["pi"] == "12-34-56"
    assert redacted["/oic/d"]["n"] == REDACTED
    # `rt`, the device-type signal we actually want out of /oic/d, is kept.
    assert redacted["/oic/d"]["rt"] == ["oic.wk.d", "oic.d.refrigerator"]
    assert redacted["/oic/p"]["mnmo"] == "RF9000B"


def test_bare_key_redaction_does_not_leak_into_substring_matching():
    """The bare keys are whole-key matches only -- plenty of ordinary
    appliance fields contain those letters and must survive untouched."""
    redacted = redact_resources(
        {
            "/x": {
                "condition": "Normal",
                "display": "On",
                "dispenser": "Cubed",
                "humidity": "45",
                "spinSpeed": "1200",
                "name": "FilterProgress",
            },
        }
    )

    assert redacted["/x"] == {
        "condition": "Normal",
        "display": "On",
        "dispenser": "Cubed",
        "humidity": "45",
        "spinSpeed": "1200",
        "name": "FilterProgress",
    }


def test_redacts_ocf_wificonf_credentials_and_terse_mac_values():
    """wificonf's SSID and passphrase are redacted by key, devconf's embedded MACs by shape."""
    redacted = redact_resources(
        {
            "/WiFiConfResURI": {"tnn": "somebodys-house", "cd": "hunter2", "swf": 2},
            "/DevConfResURI": {
                "x.com.samsung.rsd": "{\n"
                '\t"bm" : "AA:BB:CC:DD:EE:FF",\n'
                '\t"rk" :\n\t[\n'
                '\t\t"VOICE"\n\t]\n'
                "}"
            },
        }
    )
    assert redacted["/WiFiConfResURI"] == {"tnn": REDACTED, "cd": REDACTED, "swf": 2}
    rsd = redacted["/DevConfResURI"]["x.com.samsung.rsd"]
    assert "AA:BB:CC:DD:EE:FF" not in rsd
    assert "VOICE" in rsd  # the substitution redacts the address, not the string


def test_redacts_upnp_device_name():
    """UPnP's udn is a per-unit UUID (TV /sec/tv/deviceinfo)."""
    redacted = redact_resources(
        {"/sec/tv/deviceinfo": {"x.com.samsung.tv.udn": "11112222-3333-4444-aaaa-bbbbccccdddd"}}
    )
    assert redacted["/sec/tv/deviceinfo"]["x.com.samsung.tv.udn"] == REDACTED


def test_redacts_the_wifi_network_name():
    """connectedApSsid is the owner's WiFi network name. It is not an account
    credential, which is why it went unredacted long enough for eleven dumps
    in this corpus to arrive carrying one, but an SSID maps to a street
    address in public wardriving databases -- and the README tells users a
    diagnostics download is already stripped of network identifiers."""
    redacted = redact_resources(
        {"/wirelessinfo/vs/0": {"connectedApSsid": "somebodys-house", "macaddressWiFi": "x"}}
    )
    assert redacted["/wirelessinfo/vs/0"]["connectedApSsid"] == REDACTED


def test_no_fixture_in_the_corpus_carries_a_real_ssid():
    """A regression guard on the dumps themselves, not on redact_resources:
    fixtures are committed to a public repository, and the redaction above
    only protects dumps captured after it existed."""
    for path in sorted(FIXTURES.glob("*_device.json")):
        for rep in parse_device0_batch(json.loads(path.read_text())["device0"]).values():
            ssid = rep.get("connectedApSsid")
            assert ssid in (None, REDACTED), f"{path.name} carries a real SSID: {ssid!r}"


def test_redacts_the_sso_account_roster():
    """ssolist is a JSON string holding the owner's account, so it goes whole."""
    rep = {
        "/DevConfResURI": {
            "x.com.samsung.ssolist": '{"AccountList":["owner@example.com"],"RegisteredCount":1}',
            "x.com.samsung.rsd": "DLNADMR",
        }
    }
    redacted = redact_resources(rep)
    assert redacted["/DevConfResURI"]["x.com.samsung.ssolist"] == REDACTED
    assert "@" not in json.dumps(redacted)
    assert redacted["/DevConfResURI"]["x.com.samsung.rsd"] == "DLNADMR"


def test_no_fixture_in_the_corpus_carries_an_email_address():
    """Like the SSID guard above: no fixture may carry a real e-mail address."""
    email = re.compile(r"[\w.+-]+@(?!example\.(?:com|org|net)\b)[\w-]+\.[a-z]{2,}")
    for path in sorted(FIXTURES.glob("*_device.json")):
        assert not email.search(path.read_text()), f"{path.name} carries an email address"


def test_redacts_the_provisioning_log_id():
    """tglogid is a per-unit provisioning UUID (present live on KTSU2
    boards) -- same identity class as the OCF uuids."""
    redacted = redact_resources(
        {"/sec/provisioninginfo": {"x.com.samsung.provisioning.tglogid": "some-uuid-here"}}
    )
    assert redacted["/sec/provisioninginfo"]["x.com.samsung.provisioning.tglogid"] == REDACTED


def test_udn_match_is_segment_exact_not_substring():
    """'udn' inside an unrelated feature key must NOT drag it out."""
    rep = {
        "/sec/tv/deviceinfo": {"x.com.samsung.tv.udn": "1111-2222"},
        "/sec/tv/advancedaudio": {"x.com.samsung.tv.autoLoudness": "on"},
    }
    redacted = redact_resources(rep)
    assert redacted["/sec/tv/deviceinfo"]["x.com.samsung.tv.udn"] == REDACTED
    assert redacted["/sec/tv/advancedaudio"]["x.com.samsung.tv.autoLoudness"] == "on"


def test_redacts_soundbar_connection_rows_but_keeps_channel_names():
    """Source names in connection rows can be a person's phone; channel names are not."""
    row = {"connectionType": "BT", "name": "Owner's phone", "groupName": "g", "ip": "192.0.2.9"}
    redacted = redact_resources(
        {
            "/sec/networkaudio/soundFrom": {
                "x.com.samsung.networkaudio.soundFrom": dict(row),
                "x.com.samsung.networkaudio.name": "Owner's phone",
            },
            "/sec/networkaudio/lastConnections": {
                "x.com.samsung.networkaudio.lastConnections": [dict(row)]
            },
            "/sec/networkaudio/channelVolume": {
                "x.com.samsung.networkaudio.channelVolume": [{"name": "Spk_Center", "value": 0}]
            },
        }
    )
    sound_from = redacted["/sec/networkaudio/soundFrom"]
    assert sound_from["x.com.samsung.networkaudio.name"] == REDACTED
    for r in (
        sound_from["x.com.samsung.networkaudio.soundFrom"],
        redacted["/sec/networkaudio/lastConnections"]["x.com.samsung.networkaudio.lastConnections"][
            0
        ],
    ):
        assert r["connectionType"] == "BT"
        assert r["name"] == r["groupName"] == r["ip"] == REDACTED
    channel = redacted["/sec/networkaudio/channelVolume"][
        "x.com.samsung.networkaudio.channelVolume"
    ]
    assert channel[0]["name"] == "Spk_Center"


def test_serial_numbers_under_short_keys_are_redacted():
    """The TV's serial (x.com.samsung.tv.sn) and the soundbar's Alexa
    product_dsn are too short for the "serial" rule."""
    redacted = redact_resources(
        {
            "/sec/tv/deviceinfo": {"x.com.samsung.tv.sn": "0ABC123", "x.com.samsung.tv.snd": 1},
            "/sec/networkaudio/alexa": {"x.com.samsung.alexa.product_dsn": "5XYZ"},
        }
    )
    assert redacted["/sec/tv/deviceinfo"]["x.com.samsung.tv.sn"] == REDACTED
    assert redacted["/sec/tv/deviceinfo"]["x.com.samsung.tv.snd"] == 1
    assert redacted["/sec/networkaudio/alexa"]["x.com.samsung.alexa.product_dsn"] == REDACTED


def test_av_board_history_and_paired_phone_are_redacted():
    redacted = redact_resources(
        {
            "/DevConfResURI": {"x.com.samsung.rmd": '{"dn":"[Phone] X"}'},
            "/sec/provisioninginfo": {
                "x.com.samsung.provisioning.sessionid": "2026081600311240",
                "x.com.samsung.provisioning.laststatus": ";2026;HW;1786888527 ES11",
            },
            "/devicelog/connectioninfo": {"x.com.samsung.devicelog.connectioninfo": "{}"},
        }
    )
    assert all(v == REDACTED for rep in redacted.values() for v in rep.values())
