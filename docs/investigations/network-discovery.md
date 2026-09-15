# Network discovery: finding an appliance, and following it when its IP moves

Issue #469: a user's three air conditioners get new DHCP leases every so
often, and each time, every config entry has to be deleted and re-added by
hand. The issue asks for two things, and they are worth separating because
they have different answers:

1. **Auto-discovery at setup** — stop asking the user for an IP at all.
2. **Following a changed IP** — repair an entry whose appliance moved.

The second is solvable today with machinery Home Assistant already runs.
The first is only partly solvable, and the part that isn't hinges on a
hardware question nobody has answered yet.

Line references are Home Assistant 2026.2.3 and `aiodiscover` 2.7.1.

## What "DHCP discovery" actually is in Home Assistant

It is not only packet snooping. Four watchers produce sightings, all
funneling into one function,
`dhcp/__init__.py:176 async_process_client(ip, hostname, mac)`:

| Watcher | Source | Needs |
| --- | --- | --- |
| `DHCPWatcher` (:403) | `aiodhcpwatcher` sniffing DHCP REQUESTs | raw sockets, host networking |
| `DeviceTrackerWatcher` (:342) | any `device_tracker` with `source_type: router` and `ip`/`mac` attributes | a router integration (AVM FRITZ!Box Tools qualifies) |
| `DeviceTrackerRegisteredWatcher` (:380) | the same data at registration time | as above |
| `NetworkWatcher` (:289) | `aiodiscover`, at startup and every 60 min (`SCAN_INTERVAL`, :71) | nothing |

The last one is the important one for this issue, and for the objection
raised in the issue thread that we are not going to port-scan anyone's
network. We don't have to: `aiodiscover` already sweeps it, hourly, whether
or not this integration participates. Its method (`discovery.py:262`) is a
reverse-DNS PTR sweep of the local subnet against the router's own resolver,
followed by an ARP lookup for each address that answered
(`network.py:310`) — no connection is ever opened to the device, and
networks larger than 2048 addresses are skipped entirely
(`discovery.py:291`).

Two consequences follow from it being PTR-first:

- An appliance shows up only if the router serves a reverse name for it.
  That is exactly why the reporter sees all three ACs in the DHCP browser
  (a FRITZ!Box serves PTRs for its leases) and why a device on a router
  that doesn't may never appear there at all.
- The hostname we see is the router's, not the appliance's choice of
  identity. All three of the reporter's ACs report `Samsung-Room-AC`.

`async_process_client` deduplicates on `(mac → ip, hostname)` (:203) and
only dispatches when something changed. **An IP change is therefore
precisely the event that produces a discovery flow** — the integration is
handed the new address without asking for it.

## What a sighting tells us, and what we key entries on

`DhcpServiceInfo` carries three fields: `ip`, `hostname` (lowercased short
name), and `macaddress` (lowercase, no colons). Entries here are keyed on
`localthings_<device_key>` — the OCF device UUID since issue #381. Nothing
in a DHCP sighting resolves to that, and the hostname is not an identity:
three units, one name.

The only usable join key is the MAC. We don't store one today.

## The MAC is already on the wire

`/wirelessinfo/vs/0` reports `macaddressWiFi`, and it rides the `/device/0`
batch that both the config-flow probe (`config_flow.py:_read_device`) and
every poll already read. It is redacted out of diagnostics dumps by
`registry/redact.py`, and ignored as an entity source
(`registry/capabilities/ignored.py:44`), but it is there.

Coverage across `tests/fixtures`, 45 of 89 device dumps:

| Family | has `/wirelessinfo/vs/0` | doesn't |
| --- | --- | --- |
| air conditioner | 12 | 11 |
| refrigerator | 9 | 5 |
| washer / dryer | 9 | 5 |
| air treatment / monitoring | 7 | 5 |
| microwave | 4 | 0 |
| cooking (range, oven, cooktop) | 3 | 11 |
| other (dishwasher, EHS, water purifier, vacuum) | 1 | 7 |

The split is not cleanly generational — TP1x-era boards mostly carry it,
ARTIK051-era ones don't, but several TP2x dumps lack it too. Some absences
are likely dump artifacts rather than firmware, so treat 51% as the floor.

That gives the plan its shape: capture the MAC where the device offers it,
and degrade gracefully where it doesn't.

## Following a changed IP: yes, and it's small

1. `_read_device` already parses the batch the MAC is in — store it on the
   entry, and backfill it for existing entries from the first poll the same
   way `_persist_identity` backfills model and type.
2. Publish it as `connections={(CONNECTION_NETWORK_MAC, format_mac(mac))}`
   on the coordinator's `DeviceInfo`.
3. Declare `"dhcp": [{"registered_devices": true}]` in the manifest. Custom
   integrations are included in the matcher set (`loader.py async_get_dhcp`),
   so this needs no core change. The matcher resolves a sighting's MAC
   against the device registry and dispatches to whichever domain owns it
   (`dhcp/__init__.py:233-239`) — an exact match, with no hostname patterns,
   no OUI list, no probing, and no discovery card for unrelated Samsung gear.
4. In `async_step_dhcp`, find the entries carrying that MAC, adopt the
   entry's own unique_id, and let HA do the rest:
   `_abort_if_unique_id_configured(updates={CONF_HOST: info.ip})` writes the
   new host and schedules a reload (`config_entries.py`).

A reload is the whole fix: every consumer reads `entry.data[CONF_HOST]`
live (`coordinator.py:818`, `:1231`, `:1568`), and
`_local_source_port` already tolerates a host string that isn't an IPv4
address.

Two things worth knowing about this path:

- **A sighting also reloads an entry that is merely retrying.** For a
  discovery source, HA reloads a `SETUP_RETRY` entry even when nothing
  changed — a free "the appliance is back on the network" signal, which is
  what issue #295 wants and can't get from a poll of a device that is off.
- **Latency is feed-dependent**: immediate from a DHCP packet or a router
  device_tracker, up to 60 minutes from the ARP/PTR sweep alone.

### Where it doesn't reach

- **Boards with no `/wirelessinfo/vs/0`.** No MAC, no auto-repair. They are
  no worse off than today.
- **A wired install.** `macaddressWiFi` is the Wi-Fi interface; a unit
  bridged onto Ethernet would be sighted under a different MAC. That fails
  closed — no match, no update.
- **The bootstrap gap.** An entry learns its MAC from a poll, so an entry
  *already* broken by an IP change appears to have no way to learn one.
  HA's own address data would close it (mac ← ip, `dhcp/helpers.py`), but
  both accessors are named `_internal` and documented as "not intended for
  use by integrations". The offline snapshot closes it instead — see
  "What shipped" below. What is left is an entry that has never polled
  successfully at all, and a board that reports no MAC: both need the
  reconfigure step once.

### Trust

MAC binding is LAN-level trust: someone able to spoof a MAC can point an
entry at an address of their choosing. What stands behind it is unchanged —
the DTLS handshake still requires the CA-signed leaf, and
`_resolve_identity` still refuses to re-key an entry onto an appliance that
can't corroborate the registered identity (issue #435's credential binding).
The worst case degrades to "the entry stops updating", not "the entry adopts
the intruder".

Host-keyed entries (issues #83/#189: placeholder serial, no usable `di`)
need no special handling, which is worth writing down because it looks like
they would. The host is their registry key, so a moved address makes
`polled_key != current_key` with nothing to corroborate it. But a v4 entry
stores that key, and a board in this state reports no `di`, so
`_resolve_identity` returns on its `stored_key is not None and polled_ocf is
None` branch before the corroboration test — the entry keeps its key, keeps
its rows, and logs nothing. Only a pre-v4 entry that has not polled once
since upgrading reaches the warning, and its first poll ends that state.

## Auto-discovery at setup: partly

For an appliance already configured, `registered_devices` covers everything.
For one that isn't, we need a matcher that recognizes it cold, and both
options are weak:

- **Hostname.** One confirmed data point, `Samsung-Room-AC`, and it depends
  on the router publishing PTRs. Building a corpus means collecting
  hostnames from users — worth doing opportunistically (the DHCP browser
  screenshot is an easy ask on any device-support issue), but it will never
  be complete.
- **OUI.** Samsung's OUIs are shared with phones, TVs, and monitors. Matching
  them either produces a discovery card for every Samsung device on the LAN,
  or forces a DTLS probe of each to filter them — which is the port scan we
  already ruled out, just narrowed.

There may be a third option, and it would change the picture: **plaintext
CoAP on UDP 5683**. `smartthings-local` already implements it —
`protocol/ocf_discovery.read_plaintext_ocf_resource` and
`discover_ocf_secure_ports` read `/oic/res` unauthenticated, and
`protocol/ocf_multicast.discover_ocf_responder_ports` does the multicast
form against `224.0.1.187:5683`. One unicast UDP packet, no credentials, and
`/oic/d` would hand back `di` — the exact key entries are already keyed on.

If these appliances answer it, then: OUI matchers become safe (one packet
filters a phone from an appliance), a discovery card can name the device
before the user clicks it, and re-identification after an IP change becomes
positive rather than MAC-trust. They do answer it -- measured after this was
written, see the last section.

Note what discovery can't do either way: an appliance that needs CA
credentials still needs them. Discovery prefills the host and skips the
"find the IP in your router" chore; the first device on an install still
goes through the CA step.

## mDNS, and the issue title

No evidence any of these appliances advertise mDNS. The reporter's units
appear in the DHCP browser and nowhere else, and RT-OCF's native discovery
is CoAP multicast, not mDNS. Adding a zeroconf matcher would be guessing.

The issue's literal request — "resolve by hostname instead of a static IP" —
looked free, and isn't. The host string would survive the trip: it is passed
to `socket.connect()` and to `DtlsCoapSession`, which resolves through
`getaddrinfo` (`protocol/endpoint.py`) on every connect, and
`_local_source_port` already handles a host that isn't an IPv4 literal. What
kills it is the install it was asked for. All three of the reporter's air
conditioners answer to `Samsung-Room-AC`; one name that resolves to one
address cannot address three units, and a router that disambiguates them
does so by appending a suffix of its own choosing, which is no more stable
than the lease. A hostname is not an identity here, and offering the field
as one would hand two of the three entries the wrong appliance.

That leaves the MAC as the only identity a sighting carries — which is what
shipped.

## What shipped

Everything in "Following a changed IP" above, plus the reconfigure step:

| | |
| --- | --- |
| `registry/identity.py` | `resolve_mac` — `/wirelessinfo/vs/0`'s `macaddressWiFi`, normalized to HA's connection form |
| `config_flow.py` | the probe returns it, `_create_entry` stores it, `async_step_dhcp` follows a sighting, `async_step_reconfigure` moves an entry by hand |
| `coordinator.py` | every poll re-reads it; `DeviceInfo` publishes `CONNECTION_NETWORK_MAC` |
| `manifest.json` | `"dhcp": [{"registered_devices": true}]` |

Three decisions inside that are worth keeping:

- **A poll only contributes a MAC if the entry adopted what answered** — the
  gate `CONF_OCF_DEVICE_ID` already gets for `di` (issue #435). Recording
  the address of an appliance `_resolve_identity` just refused to re-key
  onto would let this entry follow *its* lease later.
- **A snapshot replay does contribute one.** It is this entry's own last
  reading of the device (issue #295), so an entry already broken by a moved
  lease learns its MAC while the appliance is unreachable, and the next
  sighting repairs it. That is the bootstrap gap this document called
  unclosable without an internal HA API, closed without one.
- **Reconfigure checks who answered before writing.** `_identity_matches`
  mirrors `_resolve_identity`'s structure: the registered key, else the
  serial corroborating a regenerated `di`, else a host-keyed entry
  (issues #83/#189) that has no identity to defend. One digit wrong in an
  address would otherwise hand an entry's registry rows to the neighbour.

## Plaintext CoAP on 5683: answered, and it works

Measured against the DW5000-series dishwasher this repo's `dishwasher`
fixture came from. One unauthenticated CoAP GET per line, no CA, no DTLS, no
port sweep:

| Resource | Result |
| --- | --- |
| `/oic/res` | 2.05, 1755 bytes over two Block2 blocks; 15 links |
| `/oic/res?rt=oic.r.doxm` | 2.05, 147 bytes, one link, **carrying the secure port** |
| `/oic/d` | 2.05, 163 bytes, **carrying `di`**, on every run |
| `/oic/p` | 2.05, 336 bytes (`mnmn`, `mnmo`, `mnfv`, ...), on every run |

The directory carries no OCF 1.0 `eps`. It uses the older per-link policy
instead, which is the form `discover_ocf_secure_ports` falls back to:

```
/oic/sec/doxm   p: {bm: 1, sec: True,  port: 49154, x.org.iotivity.tls: 0}
/oic/sec/pstat  p: {bm: 1, sec: True,  port: 49154, x.org.iotivity.tls: 0}
/oic/d          p: {bm: 1, sec: False,             x.org.iotivity.tcp: 0}
/oic/p          p: {bm: 1, sec: False,             x.org.iotivity.tcp: 0}
```

Two things fall out of that table. The DTLS port is 49154 and the device
says so to anyone who asks -- and `/oic/d` and `/oic/p` answer in plaintext
because the device declares them `sec: False`, which is a policy decision in
its own resource model rather than a gap in it. This unit is onboarded and
in service, so that is the post-onboarding state, not a pre-onboarding one.

So an appliance can be identified by a single packet, and the identity it
gives up is the OCF device UUID entries are already keyed on (issue #381).
Four details to build on, every one of which a naive reader gets wrong:

- **One token for the whole blockwise transfer.** A continuation carrying a
  fresh token is silently dropped -- it asks about a transfer the device
  never began, since IoTivity-lite keys its transfer state on the token.
  This is what `smartthings-local`'s "token-stable" accumulator is for, and
  it is what stalls a naive reader at block 0; measured by trying the
  variants until one answered.
- **Responses come from an ephemeral port, not 5683** (49153 on this unit).
  Match the source address, never the source port, and address
  continuations to the endpoint that answered, as
  `protocol/ocf_discovery` does. Whether 5683 would also serve them was not
  established -- the pinned form answered first and the probe stopped
  there.
- **`/oic/res` needs Block2.** A reader that takes the first datagram gets
  exactly 1024 bytes of a truncated representation that won't decode.
  `protocol/ocf_discovery` already accumulates blocks.
- **Retransmit like a CON message.** One first-block request went
  unanswered across five runs, and it was on one of the two runs whose
  client sent each datagram exactly once; all three runs that retransmitted
  read it first time. Ordinary UDP loss, in other words, not a device that
  declines to answer -- but a reader that sends once will occasionally
  conclude the opposite.

Multicast to `224.0.1.187` drew no responders, from the default interface or
from a named one -- but not because of the devices. The appliance is not on
the same segment as the host asking: the probe reported sending from
`192.168.1.7` while the dishwasher answers unicast at `10.0.0.129`, so the
route to it crosses a router, and link-local multicast at TTL 1 cannot.

That is worth more than the untested answer it replaces. Multicast discovery
is only ever available when Home Assistant shares a segment with the
appliance, and a VLAN'd or routed install -- exactly the shape a household
that segregates IoT devices ends up with -- would see it silently find
nothing. Whatever first-time discovery ends up being built on, it cannot be
multicast alone.

What it unlocks, in the order the value lands:

1. **Verify a DHCP sighting instead of trusting it.** Before writing a new
   host, GET `/oic/d` there and compare `di` to `CONF_OCF_DEVICE_ID`. That
   turns MAC binding from an assumption into a check, for every entry whose
   appliance reports a usable `di`.
2. **First-time discovery becomes possible.** An OUI matcher is safe once one
   packet separates an appliance from a Samsung phone, and the card can name
   the device before the user clicks it. This route survives a routed
   install, where the multicast one does not: HA's DHCP feeds cross segments
   whenever the router's own device tracker or DNS does.
3. **The nine-port sweep gets a one-datagram fast path**, through the
   filtered query rather than the whole directory.
   `/oic/res?rt=oic.r.doxm` answers in 147 bytes -- one datagram, no
   blockwise, no credentials -- and names the secure port. Cheaper than
   reading the full directory and cheaper than nine parallel probes, and it
   can name a port outside `49152-49160`, which the sweep cannot reach at
   all. Keep the sweep behind it: a UDP read can be lost, some board may
   answer neither form, and setup is the worst place to discover that.

Confirmed on one board, over seven runs. What is still unknown is narrower
than it was: whether the older ARTIK051-era families answer plaintext at
all, and whether any family declares `/oic/d` `sec: True` and so refuses to
identify itself unauthenticated. The onboarding worry that sat here is
answered for this unit -- it is onboarded, in service, and still answers.

One caution for whoever builds on this. Four claims in this document were
written from what `smartthings-local`'s docstrings and the OCF spec imply,
and measurement contradicted each one: that a continuation to 5683 is what
stalls a blockwise read (it was a fresh token), that `/oic/res` advertises
`eps` (it advertises none), that the directory therefore can't name the port
(the filtered query does), and, in the other direction, that `/oic/res` is
unreliable (it was a client that didn't retransmit). The probe used is
`docs/investigations/ocf_plaintext_probe.py`; run it before trusting a line
of this.
