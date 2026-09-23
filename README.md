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

**Control your Samsung appliances from Home Assistant over your home network, with no cloud account.**

LocalThings connects Home Assistant to Samsung washers, dryers, refrigerators, air conditioners, ovens and other appliances that work with the SmartThings app. It talks to each appliance on your local network.

- **Updates are fast.** Appliances that support push send changes as they happen. LocalThings checks the rest every 30 seconds.
- **It works without the internet.** After setup, LocalThings never contacts Samsung's servers.
- **Your data stays home.** LocalThings sends nothing to Samsung. The appliance itself still keeps its own connection to Samsung, and LocalThings doesn't change that.
- **Setup needs an IP address.** For most appliances, you enter the address and LocalThings works out the type and model, then creates matching controls and sensors.

## What it supports

| Appliance | Appliance |
|---|---|
| Air conditioner, including multi-unit systems | EHS heat pump |
| Air dresser | Microwave |
| Air monitor | Oven |
| Air purifier | Range and range hood |
| Cooktop, with gas cooktops read-only | Refrigerator, kimchi refrigerator, wine cellar |
| Dehumidifier | Vacuum clean station |
| Dishwasher | Washer and washer-dryer |
| Dryer | Water purifier |

What you see depends on what each appliance reports. Typical controls and sensors include power, program or mode selection, start, pause and stop, temperatures and setpoints, time remaining and finish time, door and child-lock state, energy use, filter status and alarms.

Most Samsung appliances from about 2022 onward work, and so do some earlier washers that use an older local interface. See [Older appliances on TCP 8888](#older-appliances-on-tcp-8888). Setup detects which interface an appliance has, and tells you if it finds none.

If an appliance sets up but LocalThings doesn't recognize it, or a control is missing, Home Assistant shows a notice asking for a diagnostics download. That download is usually all the maintainers need to add support. See [Reporting a capability gap](#reporting-a-capability-gap).

## Built on open standards

LocalThings uses the standards the appliances implement:

- **OCF, the [Open Connectivity Foundation](https://openconnectivity.org/) specification.** Samsung's connected appliances are OCF devices. LocalThings discovers each appliance's resources through `/oic/res` and reads its device type, such as `oic.d.washer`, from `/oic/d`. Where an appliance offers both a standard OCF resource and a Samsung extension for the same state, LocalThings uses the standard one.
- **CoAP over DTLS.** LocalThings sends Constrained Application Protocol requests, defined in [RFC 7252](https://www.rfc-editor.org/rfc/rfc7252), over an encrypted DTLS session with an X.509 client certificate. It uses CoAP Observe, [RFC 7641](https://www.rfc-editor.org/rfc/rfc7641), for push updates, and block-wise transfer, [RFC 7959](https://www.rfc-editor.org/rfc/rfc7959), for large responses. The [`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local) library implements the protocol layer.
- **Home Assistant's own patterns.** Setup, reconfigure and reauthentication run in the UI config flow. A data-update coordinator combines push and polling. DHCP discovery follows an appliance to a new IP address. Repairs notices report gaps. LocalThings redacts diagnostics downloads and translates entity names into eight languages.
- **Declarative appliance maps.** Each appliance type is a table that maps the resources an appliance reports to Home Assistant entities. Tests check the tables against 90+ captures from real appliances, so a new model usually needs a table entry and no protocol code.

---

## Install

1. In [HACS](https://hacs.xyz/), go to **Integrations**, search for **LocalThings**, and select **Download**. LocalThings is in HACS's default list, so you don't need to add a custom repository.

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mbillow&repository=localthings&category=integration)

   Without HACS, copy `custom_components/localthings/` into the `custom_components/` folder of your Home Assistant config.
2. Restart Home Assistant.

## Add an appliance

1. Go to **Settings > Devices & Services > Add Integration > LocalThings**.
2. Enter the appliance's IP address. For most appliances, that's the whole setup.
3. LocalThings connects, reads the appliance's type and model, and adds one Home Assistant device for it.

When you add your first appliance, LocalThings contacts Samsung's servers once to read a public identifier. It uses no account or login. Later appliances reuse that identifier, so adding them needs only an IP address and no internet access.

A few appliances need one more step:

- **Some newer models ask for a CA certificate and key.** These are the public `AC14K_M` credentials, which LocalThings doesn't ship. The `smartthings-local` project's [`setup_cert.py`](https://github.com/QuiteYellow/SmartThings-Local/blob/main/setup_cert.py) shows one way to get them, with `python setup_cert.py --fallback`. Paste them in when setup asks. You only do this once.
- **Older washers on TCP 8888 ask for a device token.** See [Older appliances on TCP 8888](#older-appliances-on-tcp-8888).

You can rename devices. LocalThings identifies each appliance by its OCF device ID, not by its name or serial number. Some models ship the same serial number on every unit.

## Per-device settings

Each appliance has a **Configure** option in **Settings > Devices & Services**, with these settings under **Device settings**:

- **"Allow writes even when remote control is reported off".** By default, LocalThings refuses a command while the appliance reports remote control off, and shows an error, because the appliance would ignore the command without saying so. Some appliances accept certain commands anyway. Turn this on only after you confirm yours does.
- **"Estimated finish -- minimum change (minutes)".** LocalThings holds a washer's, dryer's or dishwasher's finish time until the appliance's estimate changes by at least this many minutes. This keeps one-minute jitter out of your history. The default is 3. Set 0 to report every change.
- **"Remember modes the device reports but doesn't advertise".** Some appliances run in a mode they never list as available. With this setting on, which is the default, LocalThings remembers such a mode and keeps offering it. **Forget remembered modes** in the same menu clears the list.

---

## Known device behavior

**Brief disconnects are normal.** Samsung appliances drop their connection for a moment now and then. LocalThings reconnects, and entities keep their last value meanwhile. If reconnects happen several times a minute, check the appliance's Wi-Fi signal. Also check that nothing else on your network holds a connection to it, because an appliance accepts one connection at a time.

**Keep appliances registered in SmartThings.** If you remove an appliance from SmartThings, it resets its network settings the next time it reaches Samsung's servers. It then drops off Wi-Fi until you set it up in the app again. This happens even if you block its internet access most of the time.

### Restarting while an appliance is powered off

If Home Assistant restarts while an appliance is off, LocalThings loads the appliance's device and entities from the last successful setup. They show `unavailable` until the appliance answers, then recover without any action from you. This works for any appliance LocalThings has reached at least once. A new appliance must be reachable to set it up.

### When an appliance's IP address changes

About half of Samsung's appliances report their Wi-Fi MAC address. For those, Home Assistant's DHCP discovery spots the new address and LocalThings updates the entry. For other appliances, select **Reconfigure** on the entry and enter the new address. LocalThings checks that the appliance at that address is the right one, and keeps its entities, history and automations. Deleting and re-adding the appliance would lose all three.

### Older appliances on TCP 8888

Some appliances from about 2018 to 2022 have a different local interface, HTTPS on TCP port 8888. So far this covers a WW6500 washer. Setup detects the interface from the IP address and then asks for a **device token**. Leave the field empty and the appliance issues a token, sending it back to Home Assistant on port 8889. For that to work, switch Remote Control on at the appliance with the door closed, and make sure the appliance can reach Home Assistant on port 8889. If the appliance later rejects its token, Home Assistant asks for a new one.

On these appliances, a chosen cycle, temperature, rinse count or spin speed takes effect only when the cycle starts. The selects hold your choice, and the **Start** button sends it. If you turn the dial on the appliance, the dial's setting replaces the held choice.

This interface speaks only TLS 1.0 and presents a certificate LocalThings can't verify, so LocalThings accepts both on this one connection. No other part of LocalThings relaxes TLS.

### Multi-unit air conditioners

Some installations run several indoor units from one outdoor unit, all at one IP address. LocalThings finds the extra units after its first connection. Each unit gets its own Home Assistant device and climate card, linked to the main unit. LocalThings skips the unused slots some systems report. If a unit you expect is missing, attach a diagnostics download to an issue.

---

## Reporting a capability gap

If LocalThings doesn't recognize an appliance, or the appliance reports something LocalThings doesn't handle yet, a notice appears under **Settings > System > Repairs**. It points you to **Download diagnostics** on the device. LocalThings removes personal and network details from that download before you save it. It removes the account email, tokens, device IDs, MAC addresses, serial numbers, the Wi-Fi network name and the device's name. You can attach the file to a [device-support issue](https://github.com/mbillow/localthings/issues/new?template=device-support.yml), and that is the fastest way to get an appliance supported.

A diagnostics download can't show whether a command sticks or what order commands need. The `localthings.read_resource` and `localthings.write_resource` actions answer those questions by reading and writing an appliance's resources without going through its entities. See [Reading and writing resources directly](docs/resource-actions.md).

## Contributing

We welcome patches, especially:

- Support for appliance types LocalThings doesn't cover yet, or for models that don't fully work. Start from a diagnostics download. [docs/development.md](docs/development.md) covers adding a type.
- Reports that confirm or rule out more models of a supported type.
- Protocol-level fixes. These belong upstream in [`smartthings-local`](https://github.com/QuiteYellow/SmartThings-Local). Fixes to entities, setup, the coordinator or the registry belong here.

Please don't include real device UUIDs, MAC addresses, serial numbers, IP addresses or private keys in a pull request. [docs/development.md](docs/development.md) has the dev environment, test setup and repo layout.
