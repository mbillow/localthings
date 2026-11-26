# Credential acquisition

> [!WARNING]
> **This page is a record of testing, not a supported process.** No accepted
> way to get a credential for an appliance that refuses LocalThings' automatic
> certificate is available today. Each route below is one or a few owners'
> report of what worked on their own hardware, and none has been reviewed or
> endorsed by the LocalThings project.
>
> - **Removing, re-registering or resetting an appliance carries risk.** Several
>   routes need the appliance removed from SmartThings and added again.
>   Registration can fail or loop, the appliance can be unusable from
>   SmartThings until it succeeds, and Routines, Scenes and history tied to it
>   can be lost. None of the routes below tested a restore.
> - **External tools are not part of LocalThings.** The scripts, patched apps
>   and repositories linked here belong to their authors. LocalThings doesn't
>   review, maintain or vouch for them and can't guarantee their safety. A
>   patched or instrumented SmartThings app runs with your Samsung account
>   signed in. Read anything you run, and prefer a spare phone and account.
> - **Rooting a phone, patching an app or decrypting its storage** may break
>   the device's security model, its warranty or Samsung's service terms. Nothing
>   here establishes that any route is permitted where you live.
>
> Proceed only with appliances and accounts you own, and at your own risk.

## Scope

LocalThings imports a credential that already exists. It doesn't run an
ownership transfer (OTM), derive keys or write the appliance's `/oic/sec/*`
security resources ([scope](https://github.com/mbillow/localthings/issues/435#issuecomment-5556562678)).
Where a credential comes from is outside the integration. This page collects
what owners have reported so far. [#435](https://github.com/mbillow/localthings/issues/435)
is the place to report a new route or a result.

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
must match it. If the automatic certificate probe times out, setup offers the
credential menu with a retry (#822).

The secure port can change when the appliance restarts. When a handshake on
the stored port fails, LocalThings asks the appliance which port it now
advertises and reconnects there, waiting longer between lookups while it stays
unreachable.

## Where the credential lives

Every route so far reads an OwnerPSK that the SmartThings app already stored;
none creates one.

- **Only the phone that onboarded the appliance locally holds it.** An
  appliance added from another phone, or a cloud-only one, has no OwnerPSK on
  this phone. That is why some routes remove the appliance and add it again
  from the phone being read, with the risks in the warning above.
- **The identity is the owner UUID, not the appliance's.** Import the record's
  `rowneruuid` (which matches the appliance's `devowneruuid`) as the identity.
  The appliance's own UUID is `subjectuuid`, and it equals the OCF `di` and,
  for these devices, the SmartThings cloud `deviceId`. Use it to pick the right
  record, never as the identity.
- **The store differs by platform.** On Android (SmartThings 1.8.52.23) the PSK
  was in an encrypted, account-specific `<UUID>.datenc` IoTivity store; the
  plaintext SVR stores inspected held certificates only. On iOS it was in
  `Library/Security/oic_svr_db_client.dat` inside the app's data.
- **One phone holds a record for every appliance it paired**, not only the one
  you're after. Select exactly one record and never publish the store.

## Reported routes

Ordered from least to most invasive. "Re-registration" means removing the
appliance from SmartThings and adding it again.

| Route | Phone | Root | Re-registration | Reported on | Tooling |
| --- | --- | --- | --- | --- | --- |
| [Local iPhone backup](#local-iphone-backup) | iPhone | No | No | `WD11BB944DGBGU` washer, ViewFinity S9 monitor, The Frame 2021 TV | Steps on this page |
| [Patched Android app (Morphe)](#patched-android-app-morphe) | Android | No | Sometimes | `HW-Q950A`, `HW-Q990C`, `HW-Q850A` soundbars, `S95B` TV, a Family Hub | [smortthings](https://github.com/neoKushan/smortthings), external |
| [Rooted Android: native credential list](#rooted-android-native-credential-list) | Android | Yes | Not reported | `AF18DX936WFN` air conditioner | Described in [#435](https://github.com/mbillow/localthings/issues/435#issuecomment-6159673363) |
| [Rooted Android: oven scripts](#rooted-android-oven-scripts) | Android 17 | Yes | See its procedure | A Samsung LCD oven | [Owner's scripts](https://github.com/KRZ303/locathings-oven-psk-retrieval), external |
| [Rooted Android with Frida](#rooted-android-with-frida) | Galaxy S10 | Yes | Yes | `AWM-KR-M64-24-WD86` washer/dryer | Private helpers, not published |

### Local iPhone backup

An existing OwnerPSK for one `WD11BB944DGBGU` washer was read from a finished,
unencrypted Finder backup of the iPhone that originally paired it. The washer
was not removed, reset or re-registered. Owners have since added a ViewFinity
S9 monitor (#891) and The Frame 2021 TV (#868) the same way.

1. Keep the backup private. In its `Manifest.db`, query `Files` for
   `domain = 'AppDomain-com.samsung.oneconnect4ios'` and
   `relativePath = 'Library/Security/oic_svr_db_client.dat'`. Require exactly
   one match. Its `fileID` names the file at
   `<backup>/<first two fileID characters>/<fileID>`. Finder's *Files* export
   doesn't include this private app store.
2. Decode the file as CBOR. The `cred` section may itself be CBOR-encoded
   bytes; inspect its `creds` array. Select exactly one PSK record whose
   `subjectuuid` matches the appliance's OCF device UUID. Decode its
   `privatedata.data` according to its `encoding` (raw bytes or base64).
3. Convert the 16- or 32-byte key to 32 or 64 hex characters and import it as
   **Owner**, with `cred.rowneruuid` as the identity. Then
   [verify it](#verifying-a-credential).

On the washer the key completed a handshake and an authenticated
`GET /oic/sec/acl` returned `2.05 Content`, with no security write. That shows
local access for one appliance, not that every control works or that every
SmartThings version stores credentials this way.

### Patched Android app (Morphe)

[smortthings](https://github.com/neoKushan/smortthings) is a Morphe patch for
the SmartThings Android app (`com.samsung.android.oneconnect`), pinned to
version `1.8.52.23`. It is maintained by its author, not by LocalThings. It uses
the app's own `CryptoManager` to decrypt the `<UUID>.datenc` store and shows the
OwnerPSK and its identity on the device's **Edit** screen. It reports that it
sends nothing off the phone and makes no `/oic/sec/*` write; LocalThings hasn't
audited that.

1. Install Morphe Manager, add the smortthings repository as a patch source,
   and apply the patch to SmartThings `1.8.52.23`.
2. Open the device's **Edit** screen. If it shows no key, the appliance wasn't
   onboarded locally from this phone. The author's workaround is removing the
   appliance and adding it again from the same phone, in a new Location if
   registration loops; that is re-registration, with its risks.
3. Import the **Owner PSK** as **Owner**, with the displayed **Identity** (the
   owner UUID). Then [verify it](#verifying-a-credential).

The author verified it on one `HW-Q950A` soundbar. Reports in #435 add
`HW-Q990C` and `HW-Q850A` soundbars, an `S95B` TV and a Family Hub. Cloud-only
devices show no key.

### Rooted Android: native credential list

An owner read an `AF18DX936WFN` air conditioner's PSK
(`R18_BIXBY_FAC_TIME_23K`, MAIN and RAC subdevices) from SmartThings' native
credential list on rooted Android 13. A later look on SmartThings 1.8.52.23
found the same key in the `<UUID>.datenc` store. Imported as **Owner**, it
worked on LocalThings 0.33.0. The
[report in #435](https://github.com/mbillow/localthings/issues/435#issuecomment-6159673363)
has the details.

### Rooted Android: oven scripts

An owner read a Samsung LCD oven's existing OwnerPSK from SmartThings on rooted
Android 17. Their [scripts and procedure](https://github.com/KRZ303/locathings-oven-psk-retrieval)
record the tested versions, backups, results and limits; they are maintained by
that owner, not by LocalThings. Import the identity unchanged. One containing a
zero byte needs `smartthings-local` 0.1.23 or newer, which LocalThings requires.

### Rooted Android with Frida

The most invasive route reported, and the only one that needs re-registration
every time. On one `AWM-KR-M64-24-WD86` washer/dryer, an owner:

1. Exported the washer's SmartThings record, Rules and Scenes as a baseline,
   noting its cloud `deviceId` and OCF `di`. The export is a comparison point,
   not a tested restore.
2. Removed the washer from SmartThings on a rooted Galaxy S10 and added it
   again from the same app and account, with no factory reset.
3. Read and decrypted the app's stored IoTivity credential with local helpers
   built on Frida 17.17.0, selected the one record matching the washer, and kept
   the key in macOS Keychain, never in a plaintext file.
4. Verified it with a fresh PSK session.

The helpers are private research tools; no download is offered. The
[test notes](https://github.com/mbillow/localthings/issues/435#issuecomment-5519459434)
and [resource inspection](https://github.com/mbillow/localthings/issues/435#issuecomment-5504979053)
have the details.

| Check | Result | Limit |
| --- | --- | --- |
| Local authentication | Authenticated `GET /oic/d` returned `2.05 Content` with the expected `di`. | Covers this appliance's credential, not every control. |
| Protected read | `/device/0?if=oic.if.b` returned a batch of 38 resources. | Read evidence only; no writes were tested. |
| SmartThings | The washer stayed usable, with unchanged `deviceId` and `di`. | One unit; not proof that every re-registration keeps identity. |
| Rules | 13 Rules kept their IDs and definitions. | None referenced the washer, so washer routines weren't tested. |
| Scenes | 14 Scenes kept their IDs and metadata. | The exports hold no actions, so Scene contents and execution weren't tested. |

## Verifying a credential

Before relying on an imported credential:

- Confirm that an authenticated `/oic/d` read returns the `di` of the appliance
  you meant, and the expected device type. LocalThings' setup does this and
  refuses a mismatch; do the same in any external tool before using a key.
  The PSK identity authenticates the session; the `di` identifies the appliance.
- Read before you write. Every report above verified reads; few tested controls.
- Never publish a key, an identity, a backup, a credential store or the UUIDs
  in them, including in a diagnostics download or an issue.

## References

- [#435: plan, findings and reports](https://github.com/mbillow/localthings/issues/435)
- [Maintainer's scope and documentation request](https://github.com/mbillow/localthings/issues/435#issuecomment-5556562678)
- [WD86 test notes](https://github.com/mbillow/localthings/issues/435#issuecomment-5519459434) and [authenticated resource inspection](https://github.com/mbillow/localthings/issues/435#issuecomment-5504979053)
- [AF18DX936WFN hardware report](https://github.com/mbillow/localthings/issues/435#issuecomment-6159673363)
- External, not maintained by LocalThings: [smortthings](https://github.com/neoKushan/smortthings), [LCD oven PSK retrieval](https://github.com/KRZ303/locathings-oven-psk-retrieval)
