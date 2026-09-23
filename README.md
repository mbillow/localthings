<!-- dark mode -->
<img src="custom_components/localthings/brand/dark_logo@2x.png#gh-dark-mode-only" alt="LocalThings Logo"/>
<!-- light mode -->
<img src="custom_components/localthings/brand/logo@2x.png#gh-light-mode-only" alt="LocalThings Logo"/>

<p align="center">
  <img alt="GitHub Repo stars" src="https://img.shields.io/github/stars/mbillow/localthings" />
  <img alt="GitHub watchers" src="https://img.shields.io/github/watchers/mbillow/localthings" />
</p>

<p align="center">
  <img alt="GitHub Release" src="https://img.shields.io/github/v/release/mbillow/localthings" />
  <img alt="hacs validation" src="https://img.shields.io/github/check-runs/mbillow/localthings/main?nameFilter=HACS%20validation&label=hacs%20validation" />
  <img alt="hassfest" src="https://img.shields.io/github/check-runs/mbillow/localthings/main?nameFilter=Hassfest%20validation&label=hassfest" />
  <img alt="tests" src="https://img.shields.io/github/check-runs/mbillow/localthings/main?nameFilter=Pytest&label=tests" />

</p>

# LocalThings

**Control your Samsung appliances from Home Assistant directly over your home network. No cloud account, no internet round-trip.**

LocalThings connects Home Assistant to Samsung washers, dryers, refrigerators, air conditioners, ovens and more, the same appliances that work with the SmartThings app. It talks to each appliance on your local network, so:

- **It's fast.** Appliances that support it push changes the moment they happen, and the rest are checked every 30 seconds.
- **It keeps working when the internet doesn't.** Once an appliance is set up, nothing depends on Samsung's servers.
- **It stays private.** LocalThings sends nothing to Samsung. (The appliance itself still keeps its own connection to Samsung; LocalThings doesn't change that.)
- **It's easy to set up.** For most appliances, you enter an IP address and that's it. LocalThings works out what the appliance is and creates the right controls and sensors for it.

## What it supports

| Appliance | Appliance |
|---|---|
| Air conditioner (including multi-unit systems) | Heat pump (EHS) |
| Air dresser | Microwave |
| Air monitor | Oven |
| Air purifier | Range and range hood |
| Cooktop (gas cooktops are read-only) | Refrigerator, kimchi refrigerator, wine cellar |
| Dehumidifier | Vacuum clean station |
| Dishwasher | Washer and washer-dryer |
| Dryer | Water purifier |

What you see depends on what each appliance reports. Typical controls and sensors include power, program or mode selection, start, pause and stop, temperatures and setpoints, time remaining and finish time, door and child-lock state, energy use, filter status and alarms.

Most Samsung appliances from about 2022 onward work. So do some earlier washers, which use an older local interface (see [Older appliances](#older-appliances-on-tcp-8888)). You don't need to check which one you have: setup detects it, and tells you plainly if an appliance has no local interface to connect to.

If your appliance sets up but isn't recognized, or something is missing, Home Assistant shows a notice asking for a diagnostics download. That's usually all it takes to add support (see [Reporting a gap](#reporting-a-capability-gap)).

## Built on open standards

LocalThings doesn't scrape an app or replay cloud traffic. It speaks the standards the appliances themselves implement:

- **[OCF](https://openconnectivity.org/) (Open Connectivity Foundation).** Samsung's connected appliances are OCF devices. LocalThings uses the OCF resource model throughout: it discovers resources through `/oic/res`, identifies the appliance from the device type it declares in `/oic/d` (for example `oic.d.washer`), and uses standard OCF resource types in preference to Samsung's vendor extensions wherever an appliance offers both.
- **CoAP over DTLS.** Traffic uses the Constrained Application Protocol ([RFC 7252](https://www.rfc-editor.org/rfc/rfc7252)) on an encrypted DTLS session authenticated with X.509 client certificates. It uses CoAP Observe ([RFC 7641](https://www.rfc-editor.org/rfc/rfc7641)) for instant push updates and block-wise transfer ([RFC 7959](https://www.rfc-editor.org/rfc/rfc7959)) for large responses. The protocol layer is the [`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local) library.
- **Home Assistant's own patterns.** LocalThings uses the UI config flow with reconfigure and reauthentication, a data-update coordinator with push, and DHCP discovery to follow an appliance whose IP address changes. It also uses Repairs notices, redacted diagnostics and fully translated entities (eight languages).
- **Data, not special cases.** Each appliance type is a declarative map from the resources an appliance reports to Home Assistant entities. The maps are tested against 90+ captures from real appliances, so supporting a new model is usually a table entry, not new protocol code.

---

## Install

1. In [HACS](https://hacs.xyz/), go to **Integrations**, search for **LocalThings**, and select **Download**. It's in HACS's default list, so you don't need to add a custom repository.

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mbillow&repository=localthings&category=integration)

   Without HACS, copy `custom_components/localthings/` into your Home Assistant config's `custom_components/` folder.
2. Restart Home Assistant.

## Add an appliance

1. Go to **Settings > Devices & Services > Add Integration > LocalThings**.
2. Enter the appliance's IP address. For most appliances, that's the whole setup.
3. LocalThings connects, reads what the appliance is (its type and model) and adds it, with one Home Assistant device per appliance.

Setting up the first appliance briefly contacts Samsung's servers to read a public identifier, with no account or login involved. Every appliance after that reuses what the first setup worked out, so it only needs an IP address and no internet access.

A small number of appliances need one more thing:

- **Some newer models ask for a CA certificate and key**, known as the `AC14K_M` credentials, the first time. They're a public pair that isn't shipped with LocalThings. The `smartthings-local` project's [`setup_cert.py`](https://github.com/QuiteYellow/SmartThings-Local/blob/main/setup_cert.py) shows one way to obtain them (`python setup_cert.py --fallback`). Paste them in when setup asks; you only do this once.
- **Older washers on TCP 8888 ask for a device token.** See [Older appliances](#older-appliances-on-tcp-8888).

Rename devices freely. Each one is tracked by the appliance's own OCF device ID, not its name or serial number, which some models share across every unit.

## Per-device settings

Each appliance has a **Configure** option in **Settings > Devices & Services**:

- **Allow writes even when remote control is reported off.** By default, LocalThings refuses a command with a clear error while the appliance reports remote control off, because the appliance would otherwise ignore it silently. Some appliances accept certain commands anyway. Only turn this on once you've confirmed yours does.
- **Estimated finish, minimum change (minutes).** This holds a washer's, dryer's or dishwasher's finish time steady until the appliance's estimate moves by at least this much, which keeps your history free of one-minute jitter. The default is 3; 0 reports every change.
- **Remember modes the device reports but doesn't advertise.** Some appliances run in a mode they never list as available. With this on (the default), LocalThings remembers such a mode and keeps offering it. **Forget remembered modes** in the same menu clears the list.

---

## Known device behavior

**Brief disconnects are normal.** Samsung appliances occasionally drop their connection for a moment. LocalThings reconnects on its own, and entities hold their last value in the meantime. If reconnects become constant (several a minute), check the appliance's Wi-Fi signal. Also check that nothing else on your network holds a connection to it, since an appliance accepts only one at a time.

**Keep appliances registered in SmartThings.** Removing an appliance from SmartThings resets its network settings the next time it reaches Samsung's servers, which drops it off Wi-Fi until you set it up in the app again. That holds even if you block its internet access most of the time.

### Restarting while an appliance is powered off

If Home Assistant restarts while an appliance is switched off, its device and entities still load from the last successful setup. They show `unavailable` until the appliance answers, and then come back on their own. This works for any appliance LocalThings has reached at least once. A brand-new appliance has to be reachable to set it up.

### When an appliance's IP address changes

Appliances that report their Wi-Fi MAC address (about half do) are followed automatically. Home Assistant's DHCP discovery spots the new address and the entry updates itself. For any other appliance, use **Reconfigure** on the entry and enter the new address. It checks that the appliance answering there is the right one, and keeps its entities, history and automations. Deleting and re-adding the appliance would lose all three.

### Older appliances on TCP 8888

Some appliances from about 2018 to 2022 (so far, a WW6500 washer) have a different local interface: HTTPS on TCP port 8888. Setup detects this from the IP address and then asks for a **device token**. Leave the field empty and the appliance issues one, sending it back to Home Assistant on port 8889. For that to work, switch Remote Control on at the appliance with the door closed, and make sure the appliance can reach Home Assistant on port 8889. If the appliance later rejects its token, Home Assistant asks for a new one.

On these appliances, a chosen cycle, temperature, rinse count or spin speed only takes effect when the cycle starts. The selects hold your choice, and the **Start** button sends it. Turning the dial on the appliance replaces what was held.

This interface only speaks TLS 1.0 and presents a certificate that can't be verified, so this one connection accepts both. Nothing else in LocalThings relaxes TLS.

### Multi-unit air conditioners

Some installations run several indoor units from one outdoor unit, all reachable at one IP address. LocalThings finds the extra units on its own after the first connection. Each one gets its own Home Assistant device and climate card, linked to the main unit. Unused slots that some systems report are skipped rather than shown as phantom devices. If a unit you expect is missing, attach a diagnostics download to an issue.

---

## Reporting a capability gap

If an appliance isn't recognized, or reports something LocalThings doesn't handle yet, a notice appears under **Settings > System > Repairs**. It points you to **Download diagnostics** on the device. That download is already stripped of personal and network details (account email, tokens, device IDs, MAC addresses, serial numbers, Wi-Fi network name and the device's name), so you can attach it directly to a [device-support issue](https://github.com/mbillow/localthings/issues/new?template=device-support.yml). That's the fastest way to get your appliance supported.

For behavior a diagnostics download can't show, such as whether a command sticks or what order commands need, the `localthings.read_resource` and `localthings.write_resource` actions let you query and command an appliance directly. See [Reading and writing resources directly](docs/resource-actions.md).

## Contributing

Patches are welcome, especially:

- Support for appliance types not covered yet, or models that don't fully work. Start from a diagnostics download; [docs/development.md](docs/development.md) covers adding a type.
- Reports confirming, or ruling out, additional models of a supported type.
- Protocol-level fixes, which belong upstream in [`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local). Fixes to entities, setup, the coordinator or the registry belong here.

Please don't include real device UUIDs, MAC addresses, serial numbers, IP addresses or private keys in a pull request. [docs/development.md](docs/development.md) has the dev environment, test setup and repo layout.
