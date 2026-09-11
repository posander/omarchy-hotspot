# Omarchy Wi-Fi Hotspot

An Omarchy Quattro bar widget for creating a temporary Wi-Fi access point
through NetworkManager.

## Features

- detects wireless adapters, AP support, concurrent station + AP support,
  supported bands and usable channels;
- configures SSID, AP BSSID/MAC, hidden SSID, WPA2-Personal or open security,
  band and channel;
- enables, disables, reapplies, and automatically expires the hotspot;
- creates an unsaved NetworkManager profile and removes it when stopped;
- remembers the Wi-Fi password in a private local preferences file. It is
  entered through a masked field, never included in the plugin repository, and
  never put in process arguments.

The Omarchy Wi-Fi Hotspot widget is designed for Omarchy Quattro and uses the shell's standard
`bar-widget` entry-point contract.

## Install

```sh
omarchy plugin add https://github.com/posander/omarchy-hotspot.git --enable
```

The widget is added to the right section by default. It can be moved with:

```sh
omarchy bar move posander.omarchy-hotspot --section right
```

Click the bar icon to open the settings panel. The timer countdown is shown
beside the icon while the hotspot is active.

## Requirements

- Omarchy Quattro with the Omarchy shell;
- NetworkManager (`nmcli`), with NetworkManager/Polkit permission to create
  and activate connections;
- `dnsmasq` (used by NetworkManager for DHCP/DNS in shared mode);
- `iw`;
- Python 3 and the `python-dbus` module.

When a required component is missing, the panel shows an **Install
dependencies** button. The button requires an explicit click and runs the
fixed official-package command through `pkexec`: `networkmanager`, `dnsmasq`,
`iw`, and `python-dbus`. It does not execute shell input or download code.
On systems where this is unavailable, install the packages manually with
`omarchy pkg add` or the system package manager.

The plugin runs its unprivileged Python helper as the logged-in user and uses
NetworkManager's system D-Bus API. The normal hotspot path does not call
`sudo` or `pkexec` and does not write system configuration files. `pkexec` is
used only after the user explicitly clicks **Install dependencies**, with a
fixed package list.

The saved settings, including the password, are stored locally at
`~/.config/omarchy/omarchy-hotspot/preferences.json`. The directory is `0700`
and the file is `0600`; the password is not encrypted by the plugin, so it is
protected by the user's filesystem permissions and should not be copied into a
repository or shared backup.

## Firewall and internet sharing

NetworkManager's `shared` IPv4 mode provides the hotspot interface with
`10.42.0.1/24` and starts its temporary DHCP/DNS service. If UFW is enabled
with its default deny policy, allow DHCP, DNS, and forwarding once for the
interfaces on the target machine:

```sh
AP_IFACE=wlo1
UPLINK_IFACE=enp3s0  # replace with the actual internet-facing interface

sudo ufw allow in on "$AP_IFACE" to any port 67 proto udp comment 'Omarchy Wi-Fi Hotspot DHCP'
sudo ufw allow in on "$AP_IFACE" to any port 53 proto udp comment 'Omarchy Wi-Fi Hotspot DNS'
sudo ufw allow in on "$AP_IFACE" to any port 53 proto tcp comment 'Omarchy Wi-Fi Hotspot DNS'
sudo ufw route allow in on "$AP_IFACE" out on "$UPLINK_IFACE" comment 'Omarchy Wi-Fi Hotspot forwarding'
sudo ufw reload
```

`UPLINK_IFACE` can be a VPN or proxy interface. On a system where forwarded
traffic is redirected through Mihomo, for example, use `UPLINK_IFACE=Mihomo`
as well as (or instead of) the physical Ethernet interface. If UFW is not
installed or inactive, these rules are not needed.

## Compatibility notes

Hardware and drivers differ in AP support. The panel hides unsupported bands
and channels and warns when the adapter cannot keep a station connection and
an AP at the same time. In that case, starting the hotspot may disconnect the
current Wi-Fi uplink.

Protected hotspots use WPA2-Personal with PMF disabled for compatibility.
WPA3-only and WPA2/WPA3 transition mode are intentionally not exposed because
some adapters and drivers fail while configuring the AP beacon.

## Development checks

From the repository root:

```sh
omarchy plugin validate .
qmllint -I "${OMARCHY_PATH:-/usr/share/omarchy}/shell" BarWidget.qml Panel.qml
python3 -m unittest discover -s tests -p 'test_*.py'
node tests/test_model.js
```

After editing an installed copy, force plugin discovery with:

```sh
omarchy-shell shell rescanPlugins
```

## Remove

```sh
omarchy plugin remove posander.omarchy-hotspot
```

The UFW rules are managed separately. If the hotspot will no longer be used,
list them with `sudo ufw status numbered` and remove the corresponding entries
with `sudo ufw delete <number>`.
