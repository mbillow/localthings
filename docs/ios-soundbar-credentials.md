# An existing soundbar credential from a local iPhone backup

This records a successful HW-Q990H credential recovery, not a general Samsung
onboarding method. Acquisition stays outside LocalThings: it imports a
credential the owner already possesses, without transferring ownership or
writing `/oic/sec/*`. See [Credential acquisition](credential-acquisition.md).

## What worked

- A finished **unencrypted local backup**, made with Apple Devices on Windows,
  contained SmartThings' legacy `oic_svr_db_client.dat` store.
- The backup came from the iPhone which originally paired the soundbar. A
  phone which only signed into the account later was not tested.
- The app domain was `com.samsung.oneconnect4ios`. The selected file contained
  CBOR, with a `cred` section that could itself be CBOR-encoded bytes.
- Exactly one PSK entry matched the selected soundbar's OCF device UUID. The
  client identity came from `cred.rowneruuid`; a conflicting nonzero client
  UUID in `doxm.deviceuuid` was rejected. The recovered client identity also
  matched the soundbar's reported owner in this test.
- An authenticated `/oic/d` read confirmed the expected soundbar identity
  before its protected settings were read. The phone app remained usable.

The existing app installation and pairing were retained. No jailbreak, factory
reset, device removal, re-registration, Keychain access or OCF security write
was used. These observations cover this unit and store format; they do not
establish how Samsung provisions keys on every model or app version.

## Prerequisites

Keep the backup and recovered file on a trusted computer, outside repositories,
shared folders and public uploads. A phone backup contains much more personal
data than this procedure needs. Use only a backup you own and select its
directory explicitly. Do not send it to an integration maintainer.

The verified backup was created with Apple Devices on Windows. Finder/iTunes
backup compatibility was not tested. Encrypted backup indexes, iCloud backups,
newer encrypted app stores, certificate-only stores and Android recovery are
not supported by the helper described below. Do not disable backup encryption
or reset/re-pair a device merely because this method cannot read its backup.

The soundbar must be powered on and reachable over the local network for the
read-only verification. Its public and secure CoAP ports may change after a
restart. Use advertised-port discovery, not a port value copied from another
unit. This test used `smartthings-local` 0.1.24 and known-host multicast
discovery; network isolation or multicast filtering can prevent discovery.

## External recovery helper

An external [soundbar contribution](https://github.com/joeblack2k/ha_samsung_smartthings/pull/18)
contains a [pinned recovery helper and its client modules](https://github.com/Shafty199/ha_samsung_smartthings/tree/093c92c3ad5c1b1b6cba460e17343b11a8bcdb32).
It is not bundled with LocalThings. Review the source and its limitations
before use; the helper is part of an unmerged contribution.

1. Obtain that complete source revision: the script imports the adjacent
   `soundbar_ocf_*` client modules, so downloading only the script is insufficient.
2. On the computer holding the backup, create a separate Python 3.11+ virtual
   environment and install `smartthings-local==0.1.24` there. Do not install
   recovery tools or development dependencies into Home Assistant Core/HAOS.
3. Run the helper with an explicit finished backup directory, the soundbar's
   own LAN address and a new output filename outside the source checkout:

   ```sh
   python tools/recover_soundbar_ocf_key.py \
     --backup /private/path/to/selected-backup \
     --host 192.168.1.50 \
     --output /private/path/outside-the-checkout/credentials.json
   ```

   Use the virtual environment's Python and replace the example paths/address.
   On Windows, put the command on one line and quote paths containing spaces.

The helper opens the backup index read-only and queries only SmartThings'
`oic_svr_db_client.dat`, rather than extracting the whole phone. It rejects a
missing or ambiguous file, conflicting identities and a missing or ambiguous
device key. The PSK is selected by the target OCF UUID, not a friendly name or
the first key in the store.

Only after a fresh authenticated identity check and protected reads succeed
does it exclusively create the private output. It does not print the key or
identities, overwrite an existing output, or change a soundbar setting. The
output is created with mode 0600 on POSIX; Windows users must protect its
directory with appropriate NTFS permissions.

## LocalThings' key format

The external helper writes a JSON file containing `version`, `host`,
`device_id`, `identity` and a **base64** `key`. LocalThings expects the identity
as its UUID string and the same key bytes as **32 or 64 hexadecimal characters**.
Do not paste the base64 string into LocalThings' key field or change the UUID
to the soundbar's device UUID: client identity and device identity are separate.

Convert locally without printing the credential. For example, this Python
snippet reads `credentials.json` and exclusively creates `local-import.json`
in a private directory outside either checkout:

```python
import base64
import json
import os
from pathlib import Path

data = json.loads(Path("credentials.json").read_text(encoding="utf-8"))
key = base64.b64decode(data["key"], validate=True)
if len(key) not in (16, 32):
    raise ValueError("Unexpected key length")
fd = os.open("local-import.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as output:
    json.dump({"identity": data["identity"], "key_hex": key.hex()}, output)
```

Open that file privately to enter the identity/key into LocalThings' **Owner**
credential option. Keep the recovered identity unchanged. This test's owner
identity matched the unit's owner; do not classify an arbitrary pairwise key
as an owner key merely because it was found in the app store.

Treat both files and backups containing them as credentials. Do not include
them in screenshots, issue reports, terminal output or source control.

## Verification limits

The underlying recovery method and authenticated setting reads were tested on
HW-Q990H through a separate local implementation. The newly published helper
has synthetic backup/matching tests; its exact end-to-end real-backup export
was not repeated. The exact proposed external client subsequently authenticated
and read all sixteen mapped settings, without exporting the key again.

This is evidence of a usable existing credential. Soundbar discovery and
settings support landed in [#606](https://github.com/mbillow/localthings/pull/606),
with further AV work tracked in [#388](https://github.com/mbillow/localthings/issues/388).
The recovered key has not been entered into a production LocalThings soundbar
entry as part of this contribution. Neither a handshake nor `2.04 Changed`
alone establishes that a control worked; compare an authenticated readback
with the requested value.
