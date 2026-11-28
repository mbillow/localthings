# iPhone backup and ARTIK051 2-in-1 testing notes

This records one owner's local setup of an `ARTIK051_FAC_TIME_21K` floor unit
and its `ARTIK051_FAC_TIME_RAC_21K` wall unit. Read the
[credential-acquisition warning](credential-acquisition.md) first. This is a
reported route, not a supported credential-acquisition service or a guarantee
for other appliances. No phone, backup or Samsung account is uploaded to
LocalThings.

## What was verified

With Home Assistant 2026.11.3, LocalThings 0.40.0 plus these changes, and
`smartthings-local` 0.1.25:

- An OwnerPSK recovered from a completed, unencrypted Finder backup completed
  a local DTLS handshake. The authenticated `/oic/d` device ID matched the
  selected backup record's `subjectuuid`.
- The public CoAP endpoint answered on UDP 49154 and advertised DTLS port
  49155. These were the observed ports, not permanent addresses to hardcode.
- One integration entry produced two devices and two `climate` entities.
  Both reported current temperature, desired temperature, modes and fan
  options; the wall unit also reported its swing options.
- The two devices were added to a second Home Assistant installation and
  their states were read there too. Only one installation held the local
  connection at a time.

**No power, mode, temperature or other control writes were tested.** The
observations above establish authentication, discovery and state reads. They
do not establish that every control works, that keys survive re-registration,
or that this procedure works on every ARTIK051 firmware.

## Find the private SmartThings store

Use a completed **local Finder backup**, made from the iPhone that registered
the appliance. This example does not decrypt encrypted backups. Finder's
**Files** export does not include the private app store.

On macOS, Finder normally stores backups below:

```text
~/Library/Application Support/MobileSync/Backup/<backup-id>/
```

The relevant logical file is:

```text
AppDomain-com.samsung.oneconnect4ios
Library/Security/oic_svr_db_client.dat
```

It is not stored under that visible filename in the backup. Look it up in
`Manifest.db`:

```sql
SELECT fileID FROM Files
WHERE domain = 'AppDomain-com.samsung.oneconnect4ios'
  AND relativePath = 'Library/Security/oic_svr_db_client.dat';
```

Require exactly one match. The actual file is
`<backup>/<first-two-fileID-characters>/<fileID>`. Do not copy the entire backup
or credential store into an issue, PR, public repository or chat attachment.

## Extract one existing OwnerPSK

In a temporary Python environment, install `cbor2` with
`python -m pip install cbor2`. Save the following example outside your Home
Assistant configuration as `extract_owner_psk.py`:

```python
import base64
import json
import os
import plistlib
import re
import sqlite3
import sys
from pathlib import Path
from uuid import UUID

backup = Path(sys.argv[1]).expanduser().resolve()
output = Path(sys.argv[2]).expanduser()
expected_device = str(UUID(sys.argv[3])) if len(sys.argv) > 3 else None

import cbor2

manifest = plistlib.loads((backup / "Manifest.plist").read_bytes())
status = plistlib.loads((backup / "Status.plist").read_bytes())
if manifest.get("IsEncrypted"):
    raise SystemExit("This example only reads unencrypted Finder backups.")
if status.get("SnapshotState") != "finished":
    raise SystemExit("Wait for the backup to finish before reading it.")
with sqlite3.connect((backup / "Manifest.db").as_uri() + "?mode=ro", uri=True) as db:
    rows = db.execute(
        "SELECT fileID FROM Files WHERE domain = ? AND relativePath = ?",
        ("AppDomain-com.samsung.oneconnect4ios",
         "Library/Security/oic_svr_db_client.dat"),
    ).fetchall()
if len(rows) != 1 or not re.fullmatch(r"[0-9a-f]{40}", rows[0][0]):
    raise SystemExit("Expected exactly one SmartThings credential store.")
file_id = rows[0][0]
store = cbor2.loads((backup / file_id[:2] / file_id).read_bytes())
cred = store["cred"]
if isinstance(cred, bytes):
    cred = cbor2.loads(cred)


def uuid_text(value):
    return str(UUID(bytes=value) if isinstance(value, bytes) else UUID(value))


records = [r for r in cred.get("creds", []) if r.get("credtype") == 1]
if expected_device:
    records = [r for r in records
               if uuid_text(r["subjectuuid"]) == expected_device]
if len(records) != 1:
    raise SystemExit(
        "Expected one matching PSK. If there are several, supply the appliance's "
        "OCF device UUID as the third argument; do not select an arbitrary key."
    )
record = records[0]
private = record["privatedata"]
encoding = private.get("encoding")
if encoding == "oic.sec.encoding.base64":
    key = base64.b64decode(private["data"], validate=True)
elif encoding == "oic.sec.encoding.raw" and isinstance(private["data"], bytes):
    key = private["data"]
else:
    raise SystemExit("Unsupported PSK encoding; do not guess its conversion.")
if len(key) not in (16, 32):
    raise SystemExit("Expected a 16- or 32-byte PSK.")
identity = uuid_text(cred["rowneruuid"])
if UUID(identity).int == 0:
    raise SystemExit("The store did not contain a usable owner identity.")
credential = {
    "psk_identity": identity,
    "psk_key": key.hex(),
    "ocf_device_id": uuid_text(record["subjectuuid"]),
}
# Refuse to overwrite an existing file and keep the credential owner-only.
with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
    json.dump(credential, f, indent=2)
    f.write("\n")
print("Saved the selected credential to the private output file.")
```

Run it against the backup directory, choosing an output outside this repository:

```sh
python extract_owner_psk.py "/path/to/finished-backup" "/private/path/owner-psk.json"
```

If several PSK records exist, provide the expected appliance OCF UUID as a
third argument. That is the appliance's `/oic/d` `di`, which must match the
record's `subjectuuid`. Do not use it as the PSK identity: the identity is
`cred.rowneruuid`, matching the appliance's `devowneruuid`.

The file holds a real credential. Keep it private, import it locally, and
follow your backup-retention policy. Do not publish its contents.

### If the store has certificates but no matching PSK

The initial backup from the same iPhone in this test contained only
`credtype: 8` certificate records. After the owner removed and re-added the
appliance in the SmartThings iPhone app and made another completed Finder
backup, one `credtype: 1` record appeared. Its base64 data decoded to a
16-byte key. **That does not mean every missing-key case requires
re-registration.** App history and versions can differ; read the risks in the
credential-acquisition warning before considering it. Routines, scenes and
history may be affected. No factory reset was used in this test.

## Add the appliance with this change

1. Install this branch's `custom_components/localthings/` over the existing
   integration and restart Home Assistant. Back up the existing component first.
2. Open **Settings > Devices & Services > Add Integration > LocalThings**.
3. Enter the appliance IP and select **Use an existing owner PSK (skip automatic
   certificate)**. This is optional: leave it off for the existing automatic
   certificate and fallback setup paths.
4. Enter `psk_identity` as **Identity (owner UUID)** and `psk_key` as **Key**.
   Setup still proves the key and records the authenticated appliance UUID.
5. For this 2-in-1 model, expect one integration entry, a floor-unit device
   and a wall-unit device, each with its own `climate` entity. Do not add the
   same IP twice or try the wall-unit UUID as a separate PSK identity.

Stop any test Home Assistant instance before connecting the production one.
An appliance may accept only one active local connection. Keep the appliance
registered in SmartThings; removing it permanently is not a cloud-disconnection
procedure.

## Why the wall unit needed different discovery

The master advertised a subdevice UUID. Its wall unit answered individual
UUID-prefixed information, power, mode, temperature and wind resources, but
`/<subdevice-uuid>/device/0` did not answer. After that unsupported Collection
request, subsequent prefixed reads also timed out in this test.

For the observed `ARTIK051_FAC_TIME_21K` board, bounded discovery now checks
identity and primary Property resources before the unadvertised Collection.
Once that Property namespace is proven, it uses the individual resources
instead. Climate dependencies are read before optional meters, and discovery
snapshots merge by indoor-unit identity when the confirmed href set grows.
The exception is scoped to this observed board; other collection-based
families retain their Collection discovery path.

If only one unit appears, download LocalThings diagnostics and inspect
`subdevice_probes` and `subdevices`. Remove credentials and personal identifiers
before sharing anything. Do not attach the iPhone backup or private PSK file.
