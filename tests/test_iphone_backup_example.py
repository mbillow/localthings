"""Exercise the documented offline extraction with synthetic Finder backups."""

import base64
import json
import os
import plistlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import cbor2
import pytest

OWNER = "11111111-1111-4111-8111-111111111111"
DEVICE = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"
KEY = bytes(range(16))
EXAMPLE = (
    (Path(__file__).parents[1] / "docs/iphone-artik051-2in1.md")
    .read_text()
    .split("```python\n", 1)[1]
    .split("```", 1)[0]
)


def backup_at(path, records, *, encrypted=False, finished=True, nested=True):
    path.mkdir()
    (path / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": encrypted}))
    (path / "Status.plist").write_bytes(
        plistlib.dumps({"SnapshotState": "finished" if finished else "in_progress"})
    )
    file_id = "ab" * 20
    with sqlite3.connect(path / "Manifest.db") as db:
        db.execute("CREATE TABLE Files(fileID TEXT, domain TEXT, relativePath TEXT)")
        db.execute(
            "INSERT INTO Files VALUES (?, ?, ?)",
            (
                file_id,
                "AppDomain-com.samsung.oneconnect4ios",
                "Library/Security/oic_svr_db_client.dat",
            ),
        )
    cred = {"creds": records, "rowneruuid": OWNER}
    (path / "ab").mkdir()
    (path / "ab" / file_id).write_bytes(
        cbor2.dumps({"cred": cbor2.dumps(cred) if nested else cred})
    )
    return path


def record(device=DEVICE, encoding="base64"):
    return {
        "credtype": 1,
        "subjectuuid": device,
        "privatedata": {
            "encoding": "oic.sec.encoding." + encoding,
            "data": base64.b64encode(KEY).decode() if encoding == "base64" else KEY,
        },
    }


def run(backup, output, device=None):
    args = [sys.executable, "-c", EXAMPLE, str(backup), str(output)]
    if device:
        args.append(device)
    return subprocess.run(args, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("encoding", ["base64", "raw"])
@pytest.mark.parametrize("nested", [True, False])
def test_extracts_only_selected_private_credential(tmp_path, encoding, nested):
    backup = backup_at(tmp_path / "backup", [record(encoding=encoding)], nested=nested)
    output = tmp_path / "owner.json"
    result = run(backup, output)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text()) == {
        "psk_identity": OWNER,
        "psk_key": KEY.hex(),
        "ocf_device_id": DEVICE,
    }
    assert KEY.hex() not in result.stdout
    assert OWNER not in result.stdout
    if os.name == "posix":
        assert output.stat().st_mode & 0o777 == 0o600


def test_multiple_records_require_explicit_appliance_selection(tmp_path):
    backup = backup_at(tmp_path / "backup", [record(), record(OTHER)])
    output = tmp_path / "owner.json"
    assert run(backup, output).returncode != 0
    assert not output.exists()
    assert run(backup, output, OTHER).returncode == 0
    assert json.loads(output.read_text())["ocf_device_id"] == OTHER


@pytest.mark.parametrize("encrypted,finished", [(True, True), (False, False)])
def test_rejects_unreadable_or_unfinished_backup(tmp_path, encrypted, finished):
    backup = backup_at(tmp_path / "backup", [record()], encrypted=encrypted, finished=finished)
    output = tmp_path / "owner.json"
    assert run(backup, output).returncode != 0
    assert not output.exists()


def test_existing_output_is_not_overwritten(tmp_path):
    backup = backup_at(tmp_path / "backup", [record()])
    output = tmp_path / "owner.json"
    output.write_text("existing")
    assert run(backup, output).returncode != 0
    assert output.read_text() == "existing"
