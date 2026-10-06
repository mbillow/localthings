# Credential acquisition

This document records credential-acquisition routes reported by device owners:
what each route required, what worked on the tested hardware, and what remains
unverified.

The [current LocalThings scope](https://github.com/mbillow/localthings/issues/435#issuecomment-5556562678)
is to import an already-provisioned credential. Acquisition happens separately;
the integration does not perform ownership transfer (OTM) or write `/oic/sec/*`
security resources.

## Importing a credential

Add the appliance by IP as usual. If it refuses the automatic certificate, setup
offers an owner pre-shared key, a peer pre-shared key and, last, an AC14K_M CA
certificate. No appliance that refused the automatic certificate has been
reported to accept AC14K_M. When the appliance's unencrypted `/oic/sec/doxm`
reports a PSK credential type (`sct` with bit `1` set and bit `8` clear), the
menu says so.

- **Owner** is an OwnerPSK. Its identity is the OCF owner UUID, which setup
  fills in from `devowneruuid` when the appliance reports one.
- **Peer** is a pairwise credential installed alongside Samsung's ownership.
  Its identity can't be read from the appliance, so enter it with the key.

The identity is a UUID and the key is 32 or 64 hex characters. Setup adds the
appliance only after the key completes a handshake and the appliance reports its
`/oic/d` `di` over that session. That `di` is recorded, and later connections
must match it.

The secure port can change when the appliance restarts. When a handshake on
the stored port fails, LocalThings asks the appliance which port it now
advertises and reconnects there, waiting longer between lookups while it stays
unreachable.

## WD86 washer/dryer

An OwnerPSK recovered after SmartThings removal and re-registration authenticated
one `AWM-KR-M64-24-WD86` washer/dryer. A fresh local session returned the expected
device identity and the washer's protected resources. SmartThings access also
remained available after re-registration ([test notes](https://github.com/mbillow/localthings/issues/435#issuecomment-5519459434)).

### Equipment and software

| Where | What to prepare |
| --- | --- |
| Appliance | The WD86 to be registered, with access to its panel for onboarding. This test used one `AWM-KR-M64-24-WD86`. |
| Phone | A rooted physical Samsung Galaxy S10 on which SmartThings opens and the appliance owner's Samsung account can sign in. |
| USB connection | A data-capable cable connecting the S10 to the Mac, with USB debugging enabled and the Mac authorized on the phone. |
| Mac | Android Platform-Tools (`adb`), a Python environment for the recovery helpers, and Node.js/npm for building their Frida Java agent. |
| Mac and phone | Frida host tools on the Mac and a matching Android arm64 Frida server on the rooted S10. The prepared toolchain used Frida 17.17.0 and `frida-tools` 14.10.4. |
| Recovery tools on the Mac | Helpers that recover the selected washer's stored IoTivity credential, save it to Keychain, and verify it with a fresh PSK session. |
| Network | Internet access for SmartThings registration and a local network on which the Mac can reach the washer for authenticated OCF reads. |
| Private storage on the Mac | A location for the pre-removal SmartThings exports and macOS Keychain for the recovered credential. |

The recovery and verification helpers used in this test are local research
tools, not part of LocalThings. A public download and installation procedure is
not provided here. The removal steps below assume that working recovery and
verification helpers have already been prepared on the Mac.

Root access and independent local tooling for the app's encrypted IoTivity
security data were part of this test. The account and device ownership describe
the test environment; they do not establish Samsung's approval of the recovery
method or permission under applicable service terms or law.

### 1. Prepare the phone and Mac

1. On the rooted S10, open SmartThings and sign in to the same Samsung account
   that owns the WD86. Confirm that the app opens normally and that the intended
   washer can be identified in the account.
2. Connect the S10 to the Mac over USB, accept the phone's debugging prompt, and
   confirm that `adb` can access the intended test phone and that root is
   available.
3. Prepare the matching Frida server on the phone and the host tools on the Mac.
   Build the recovery helper's Java agent and check that the helper can connect
   to the intended SmartThings process.
4. Prepare the private backup location and the helper's Keychain storage before
   proceeding to removal.

### 2. Save the current SmartThings state

The pre-removal backup was saved on the Mac. It included the washer's device
record and status, room information, Rules, and the available Scene records.
Record the washer's cloud `deviceId` and known OCF `di` so that the re-registered
device and recovered credential can be checked against the intended appliance.

Keep the exports private. They provide a baseline for comparison; an export is
not a tested restore procedure for a failed registration or lost automation.

### 3. Remove the WD86 and register it again

1. In SmartThings on the prepared S10, the WD86 device entry was removed from
   the account. The SmartThings app itself remained installed on the phone.
2. In that same app, using the same Samsung account, the washer was added again
   through the normal add-device flow. The washer's onboarding steps were
   completed as prompted by SmartThings. No separate physical factory reset
   was performed on the appliance.
3. Registration completed and the washer was accessible again in SmartThings.
   Keep this app installation and its stored data available for the recovery
   step.

### 4. Recover the stored credential on the Mac

1. With the registered S10 connected to the Mac, the local recovery helper read
   and decrypted SmartThings' persisted IoTivity security data.
2. The helper selected the stored credential whose device identity matched the
   intended washer. Exactly one matching credential was found. The successful
   result came from this stored record, not from a live capture of newly
   generated OwnerPSK material.
3. The recovered key and its associated PSK identity were saved to macOS
   Keychain and read back for verification. Credential material was not printed
   to the terminal or saved in a plaintext capture file.

This recovery did not require another removal or registration. The local
recovery tools reused the stored credential; they did not perform a separate
manufacturer-OTM transaction, derive or install a new OwnerPSK, or write
`/oic/sec/*`. SmartThings performed its normal registration flow; the security
changes made internally by the app were not independently audited.

### 5. Verify the credential and compare SmartThings state

1. On the Mac, the verification helper loaded the credential from Keychain and
   opened a fresh PSK-authenticated session to the selected WD86.
2. An authenticated `GET /oic/d` checked that the returned `di` matched the
   expected washer and that the device type included `oic.d.washer`.
3. The same session read `/device/0?if=oic.if.b` to confirm access to the washer's
   protected resources. This verification used reads and sent no appliance
   control or OCF security writes.
4. The post-registration SmartThings identity and available Rule and Scene
   records were compared with the pre-removal baseline, with the limits below.

The PSK identity authenticates the session; the `di` in the authenticated
`/oic/d` response identifies the appliance. They serve different roles, even
where a cloud device ID happens to match the appliance's `di`.

### Results and verification limits

The table summarizes observations from this WD86 test and what remains
unverified.

| Check | Result | Limit |
| --- | --- | --- |
| Local authentication | A fresh PSK session succeeded; authenticated `GET /oic/d` returned `2.05 Content` with the expected `di`. | The check covered credential validity for this appliance; it did not test every control. |
| Protected resource read | `/device/0?if=oic.if.b` returned one flat batch with 38 child resources over the same session. | This is authenticated read evidence, not a test of every resource's write behavior. |
| SmartThings | The washer remained accessible. The before-and-after comparison recorded unchanged cloud `deviceId` and OCF `di` values; the authenticated local `di` matched. | This is one unit's result, not proof that every registration preserves identity or every cloud command works. |
| Rules | The before-and-after comparison recorded unchanged IDs and exported definitions for 13 Rules, apart from normal execution timestamps. | None of the 13 exported Rule definitions directly referenced the washer, so washer-specific routine preservation was not tested. |
| Scenes | The before-and-after comparison recorded unchanged IDs and available exported records for 14 Scenes. | The saved Scene responses contain metadata, not action definitions. Full Scene-definition preservation, washer dependencies, and execution behavior were not verified. |

## Samsung LCD oven with rooted Android 17

A device owner recovered a Samsung LCD oven's existing OwnerPSK from SmartThings
on rooted Android 17. Their [recovery scripts and procedure](https://github.com/KRZ303/locathings-oven-psk-retrieval)
record the tested versions, backups, results, and limits. Import the recovered
identity unchanged. An identity containing a zero byte needs `smartthings-local`
0.1.23 or newer, which LocalThings requires.

## References

- [WD86 acquisition and SmartThings observations (issue comment)](https://github.com/mbillow/localthings/issues/435#issuecomment-5519459434)
- [WD86 authenticated resource inspection](https://github.com/mbillow/localthings/issues/435#issuecomment-5504979053)
- [Maintainer's scope and documentation request](https://github.com/mbillow/localthings/issues/435#issuecomment-5556562678)
