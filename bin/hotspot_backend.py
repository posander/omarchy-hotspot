#!/usr/bin/env python3
"""NetworkManager backend for the Omarchy temporary hotspot plugin.

The process speaks one JSON request and one JSON response per line. It runs as
the logged-in user, so NetworkManager/Polkit remains the authority for actions.
No secret is accepted as a command-line argument; the UI sends it over this
process' stdin and the backend passes it to NetworkManager over D-Bus. The
connection profile is unsaved and temporary.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import dbus
except ImportError:  # The error is reported only if the user tries to start it.
    dbus = None


PROFILE_NAME = "Omarchy Hotspot (temporary)"
PROFILE_PREFIX = "Omarchy Hotspot (temporary)"
MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$", re.IGNORECASE)
CHANNEL_RE = re.compile(r"^\s*\*\s+(\d+(?:\.\d+)?)\s+MHz\s+\[(\d+)\]")
REQUIRED_DEPENDENCIES: tuple[dict[str, str], ...] = (
    {"package": "networkmanager", "label": "NetworkManager", "command": "nmcli"},
    {"package": "dnsmasq", "label": "dnsmasq", "command": "dnsmasq"},
    {"package": "iw", "label": "iw", "command": "iw"},
    {"package": "python-dbus", "label": "Python D-Bus", "module": "dbus"},
)


def runtime_directory() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if not base or not os.path.isdir(base):
        base = f"/tmp/omarchy-hotspot-{os.getuid()}"
    path = Path(base) / "omarchy-hotspot"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


STATE_PATH = runtime_directory() / "state.json"


def preferences_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    config_root = Path(base) if base else Path.home() / ".config"
    return config_root / "omarchy" / "omarchy-hotspot" / "preferences.json"


PREFERENCES_PATH = preferences_path()
PREFERENCE_DEFAULTS: dict[str, Any] = {
    "iface": "",
    "ssid": "Omarchy Wi-Fi Hotspot",
    "bssid": "",
    "hidden": False,
    "security": "wpa-psk",
    "password": "",
    "band": "",
    "channel": "",
    "duration": "3600",
}


def reply(request_id: Any, ok: bool, data: Any = None, error: str = "") -> None:
    payload = {"id": str(request_id or ""), "ok": bool(ok)}
    if ok:
        payload["data"] = data if data is not None else {}
    else:
        payload["error"] = error or "Unknown error"
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def clean_error(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip()
    return text.splitlines()[-1] if text else f"Command failed with exit code {result.returncode}"


def run_command(args: list[str], input_text: str | None = None, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    return subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def command_output(args: list[str], timeout: int = 20) -> str:
    try:
        result = run_command(args, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""


def dependency_status() -> dict[str, Any]:
    """Report runtime components needed by the NetworkManager backend."""

    missing: list[dict[str, str]] = []
    for dependency in REQUIRED_DEPENDENCIES:
        available = True
        command = dependency.get("command")
        module = dependency.get("module")
        if command and shutil.which(command) is None:
            available = False
        if module == "dbus" and dbus is None:
            available = False
        if not available:
            missing.append({
                "package": dependency["package"],
                "label": dependency["label"],
            })
    return {
        "ready": not missing,
        "missing": missing,
    }


def split_nmcli(line: str) -> list[str]:
    """Split nmcli terse output while unescaping its colon separator."""

    values: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line.rstrip("\n"):
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            values.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    values.append("".join(current))
    return values


def parse_iw_dev(text: str) -> list[dict[str, str]]:
    devices: list[dict[str, str]] = []
    phy = ""
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        phy_match = re.match(r"phy#(\d+)", stripped)
        if phy_match:
            phy = "phy" + phy_match.group(1)
            current = None
            continue
        phy_match = re.match(r"phy\s+(phy\S+)", stripped)
        if phy_match:
            phy = phy_match.group(1)
            current = None
            continue
        interface_match = re.match(r"Interface\s+(\S+)", stripped)
        if interface_match:
            current = {"iface": interface_match.group(1), "phy": phy, "mac": "", "type": ""}
            devices.append(current)
            continue
        if current is None:
            continue
        if stripped.startswith("addr "):
            current["mac"] = stripped.split(None, 1)[1]
        elif stripped.startswith("type "):
            current["type"] = stripped.split(None, 1)[1]
    return devices


def band_for_frequency(frequency: int) -> tuple[str, str] | None:
    if 2400 <= frequency < 2500:
        return "bg", "2.4 GHz"
    if 4900 <= frequency < 5925:
        return "a", "5 GHz"
    if 5925 <= frequency < 7125:
        return "6GHz", "6 GHz"
    return None


def parse_phy_info(text: str) -> dict[str, Any]:
    modes: list[str] = []
    combinations: list[str] = []
    bands: dict[str, dict[str, Any]] = {}
    section = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped == "Supported interface modes:":
            section = "modes"
            continue
        if stripped == "valid interface combinations:":
            section = "combinations"
            continue
        if re.match(r"Band\s+\d+:$", stripped):
            section = "bands"
            continue
        if not stripped:
            if section in ("modes", "combinations"):
                section = ""
            continue

        if section == "modes":
            match = re.match(r"\*\s*(.+)$", stripped)
            if match:
                modes.append(match.group(1).strip())
            elif not stripped.startswith("*"):
                section = ""
        elif section == "combinations":
            if stripped.startswith("*"):
                combinations.append(stripped[1:].strip())
            elif stripped.startswith("total ") or stripped.startswith("#channels"):
                # `iw` wraps one combination over two lines. The second line
                # is still part of the preceding combination; keep scanning
                # until the next combination or heading.
                continue
            elif not stripped.startswith("*"):
                section = ""

        channel_match = CHANNEL_RE.match(raw)
        if channel_match:
            frequency = int(float(channel_match.group(1)))
            channel = int(channel_match.group(2))
            band = band_for_frequency(frequency)
            if band is None:
                continue
            band_value, band_label = band
            lowered = stripped.lower()
            if "disabled" in lowered or "no ir" in lowered:
                continue
            entry = bands.setdefault(band_value, {"value": band_value, "label": band_label, "channels": []})
            if channel not in entry["channels"]:
                entry["channels"].append(channel)

    for entry in bands.values():
        entry["channels"].sort()
    return {
        "modes": modes,
        "concurrent": any("managed" in value and re.search(r"\bAP\b", value) for value in combinations),
        "bands": list(bands.values()),
    }


def interface_driver(iface: str) -> str:
    path = Path(f"/sys/class/net/{iface}/device/driver")
    try:
        return Path(os.path.realpath(path)).name
    except OSError:
        return ""


def interface_mac(iface: str) -> str:
    try:
        return Path(f"/sys/class/net/{iface}/address").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return ""


def network_manager_devices() -> list[dict[str, str]]:
    result = command_output(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
    devices: list[dict[str, str]] = []
    for line in result.splitlines():
        fields = split_nmcli(line)
        if len(fields) < 4 or fields[0] == "lo" or fields[0] == "--":
            continue
        devices.append({"iface": fields[0], "type": fields[1], "state": fields[2], "connection": fields[3]})
    return devices


def default_route_iface() -> str:
    result = command_output(["ip", "-o", "route", "show", "default"])
    tokens = result.split()
    for token_index, token in enumerate(tokens):
        if token == "dev" and token_index + 1 < len(tokens):
            return tokens[token_index + 1]
    return ""


def inspect_adapters() -> dict[str, Any]:
    iw_text = command_output(["iw", "dev"])
    raw_devices = parse_iw_dev(iw_text)
    adapters: list[dict[str, Any]] = []
    errors: list[str] = []
    if not iw_text:
        errors.append("iw could not inspect wireless interfaces")

    for raw in raw_devices:
        iface = raw["iface"]
        phy = raw.get("phy", "")
        phy_text = command_output(["iw", "phy", phy, "info"]) if phy else ""
        parsed = parse_phy_info(phy_text)
        if phy and not phy_text:
            errors.append(f"Could not inspect {phy}")
        adapters.append({
            "iface": iface,
            "phy": phy,
            "mac": raw.get("mac") or interface_mac(iface),
            "driver": interface_driver(iface),
            "type": raw.get("type", ""),
            "modes": parsed["modes"],
            "apSupported": "AP" in parsed["modes"],
            "concurrent": bool(parsed["concurrent"]),
            "bands": parsed["bands"],
        })

    nm_devices = network_manager_devices()
    default_iface = default_route_iface()
    uplinks: list[dict[str, Any]] = []
    for device in nm_devices:
        if device["type"] not in ("ethernet", "wifi", "wwan", "bluetooth"):
            continue
        uplinks.append({**device, "default": device["iface"] == default_iface})

    return {
        "adapters": adapters,
        "uplinks": uplinks,
        "defaultUplink": default_iface,
        "networkManager": command_output(["nmcli", "-t", "-f", "RUNNING", "general"]).strip().lower()
        in ("running", "yes", "true"),
        "errors": errors,
    }


def read_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(value: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="state.", dir=STATE_PATH.parent, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, STATE_PATH)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def clear_state() -> None:
    try:
        STATE_PATH.unlink()
    except FileNotFoundError:
        pass


def normalize_preferences(settings: dict[str, Any]) -> dict[str, Any]:
    value = settings if isinstance(settings, dict) else {}
    security = str(value.get("security", PREFERENCE_DEFAULTS["security"]))
    # This adapter/driver advertises AP mode but can leave the supplicant
    # stuck while configuring WPA3/transition-mode beacons. Use WPA2-Personal
    # with PMF disabled for a reliable temporary AP.
    if security in ("sae", "wpa-psk sae"):
        security = "wpa-psk"
    if security not in ("open", "wpa-psk"):
        security = str(PREFERENCE_DEFAULTS["security"])
    try:
        duration = max(0, int(value.get("duration", PREFERENCE_DEFAULTS["duration"]) or 0))
    except (TypeError, ValueError):
        duration = int(PREFERENCE_DEFAULTS["duration"])
    hidden_value = value.get("hidden", PREFERENCE_DEFAULTS["hidden"])
    if isinstance(hidden_value, str):
        hidden = hidden_value.strip().lower() in ("1", "true", "yes", "on")
    else:
        hidden = bool(hidden_value)
    return {
        "iface": str(value.get("iface", PREFERENCE_DEFAULTS["iface"])).strip(),
        "ssid": str(value.get("ssid", PREFERENCE_DEFAULTS["ssid"])),
        "bssid": str(value.get("bssid", PREFERENCE_DEFAULTS["bssid"])).strip().lower(),
        "hidden": hidden,
        "security": security,
        "password": str(value.get("password", PREFERENCE_DEFAULTS["password"])),
        "band": str(value.get("band", PREFERENCE_DEFAULTS["band"])).strip(),
        "channel": str(value.get("channel", PREFERENCE_DEFAULTS["channel"])).strip(),
        "duration": str(duration),
    }


def read_preferences() -> dict[str, Any]:
    try:
        value = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(PREFERENCE_DEFAULTS)
    raw_settings = value.get("settings") if isinstance(value, dict) else None
    if not isinstance(raw_settings, dict):
        raw_settings = value if isinstance(value, dict) else {}
    normalized = normalize_preferences(raw_settings)
    # Keep the password in the private local preferences file so the masked
    # field is restored after a shell restart. It is never part of the plugin
    # repository or a process argument.
    if str(raw_settings.get("security", "")) in ("sae", "wpa-psk sae"):
        try:
            write_preferences(normalized)
        except OSError:
            pass
    return normalized


def write_preferences(settings: dict[str, Any]) -> None:
    path = PREFERENCES_PATH
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    safe_settings = normalize_preferences(settings)
    # The file and its parent are private to this user. The password is stored
    # here for convenience, but never in the plugin source or process args.
    fd, temporary = tempfile.mkstemp(prefix="preferences.", dir=path.parent, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "settings": safe_settings}, handle,
                      ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def owned_profiles() -> list[dict[str, str]]:
    result = command_output(["nmcli", "-t", "-f", "NAME,UUID,TYPE", "connection", "show"])
    profiles: list[dict[str, str]] = []
    for line in result.splitlines():
        fields = split_nmcli(line)
        if len(fields) >= 3 and fields[0].startswith(PROFILE_PREFIX):
            profiles.append({"name": fields[0], "uuid": fields[1], "type": fields[2]})
    return profiles


def active_owned_profiles() -> list[dict[str, str]]:
    result = command_output(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"])
    device_states = {item["iface"]: item["state"] for item in network_manager_devices()}
    profiles: list[dict[str, str]] = []
    for line in result.splitlines():
        fields = split_nmcli(line)
        if len(fields) >= 4 and fields[0].startswith(PROFILE_PREFIX):
            # NM exposes an ActiveConnection path while it is still merely
            # activating. Only report a hotspot as active after the device
            # itself reaches the connected state.
            if device_states.get(fields[3]) not in ("connected", "connected (externally)"):
                continue
            profiles.append({"name": fields[0], "uuid": fields[1], "type": fields[2], "device": fields[3]})
    return profiles


def delete_dbus_profile(profile_uuid: str) -> tuple[bool, str]:
    if dbus is None:
        return False, "python-dbus is not installed"
    try:
        bus = dbus.SystemBus()
        settings_object = bus.get_object(
            "org.freedesktop.NetworkManager",
            "/org/freedesktop/NetworkManager/Settings",
        )
        settings_iface = dbus.Interface(settings_object, "org.freedesktop.NetworkManager.Settings")
        for path in settings_iface.ListConnections(timeout=20):
            connection_object = bus.get_object("org.freedesktop.NetworkManager", path)
            connection_iface = dbus.Interface(
                connection_object,
                "org.freedesktop.NetworkManager.Settings.Connection",
            )
            values = connection_iface.GetSettings(timeout=20)
            connection = values.get("connection", {})
            if str(connection.get("uuid", "")) == profile_uuid:
                connection_iface.Delete(timeout=20)
                return True, ""
        return False, "Connection profile not found"
    except Exception as error:
        return False, clean_dbus_error(error)


def delete_profile(profile_uuid: str) -> tuple[bool, str]:
    result = run_command(["nmcli", "connection", "delete", "uuid", profile_uuid])
    if result.returncode == 0:
        return True, ""
    ok, error = delete_dbus_profile(profile_uuid)
    if ok:
        return True, ""
    return False, clean_error(result) if not error else error


def stop_hotspot() -> tuple[bool, str]:
    errors: list[str] = []
    for profile in active_owned_profiles():
        result = run_command(["nmcli", "connection", "down", "uuid", profile["uuid"]])
        if result.returncode != 0 and "not active" not in clean_error(result).lower():
            errors.append(clean_error(result))
    for profile in owned_profiles():
        ok, error = delete_profile(profile["uuid"])
        if not ok and "not found" not in error.lower():
            errors.append(error)
    clear_state()
    return (not errors), "; ".join(errors)


def keyfile_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


def profile_keyfile(settings: dict[str, Any], profile_uuid: str) -> str:
    iface = str(settings.get("iface", "")).strip()
    ssid = str(settings.get("ssid", ""))
    band = str(settings.get("band", "")).strip()
    channel = str(settings.get("channel", "")).strip()
    bssid = str(settings.get("bssid", "")).strip().lower()
    security = str(settings.get("security", "wpa-psk"))
    hidden = "true" if bool(settings.get("hidden", False)) else "false"

    lines = [
        "[connection]",
        f"id={keyfile_escape(PROFILE_NAME)}",
        f"uuid={profile_uuid}",
        "type=wifi",
        f"interface-name={keyfile_escape(iface)}",
        "autoconnect=false",
        "permissions=",
        "",
        "[wifi]",
        f"ssid={keyfile_escape(ssid)}",
        "mode=ap",
        f"hidden={hidden}",
    ]
    if band:
        lines.append(f"band={keyfile_escape(band)}")
    if channel:
        lines.append(f"channel={keyfile_escape(channel)}")
    if bssid:
        lines.append(f"cloned-mac-address={keyfile_escape(bssid)}")
    if security != "open":
        key_mgmt = "wpa-psk" if security in ("sae", "wpa-psk sae") else security
        lines.extend([
            "",
            "[wifi-security]",
            "auth-alg=open",
            f"key-mgmt={keyfile_escape(key_mgmt)};",
            f"psk={keyfile_escape(str(settings.get('password', '')))}",
            "proto=rsn;",
            "pmf=1",
        ])
    lines.extend([
        "",
        "[ipv4]",
        "method=shared",
        "never-default=true",
        "",
        "[ipv6]",
        "method=disabled",
        "",
        "[proxy]",
        "",
    ])
    return "\n".join(lines) + "\n"


def dbus_byte_array(value: bytes) -> Any:
    """Return a D-Bus byte array without requiring dbus in test imports."""

    if dbus is None:
        raise RuntimeError("python-dbus is required to start a hotspot")
    return dbus.ByteArray(value)


def dbus_settings(settings: dict[str, Any], profile_uuid: str) -> Any:
    """Build NetworkManager's org.freedesktop.NetworkManager.Settings value."""

    if dbus is None:
        raise RuntimeError("python-dbus is required to start a hotspot")

    def section(values: dict[str, Any]) -> Any:
        return dbus.Dictionary(values, signature="sv")

    iface = str(settings.get("iface", "")).strip()
    ssid = str(settings.get("ssid", "")).encode("utf-8")
    band = str(settings.get("band", "")).strip()
    channel = str(settings.get("channel", "")).strip()
    bssid = str(settings.get("bssid", "")).strip().lower()
    security = str(settings.get("security", "wpa-psk"))

    wireless: dict[str, Any] = {
        "ssid": dbus_byte_array(ssid),
        "mode": dbus.String("ap"),
        "hidden": dbus.Boolean(bool(settings.get("hidden", False))),
    }
    if band:
        wireless["band"] = dbus.String(band)
    if channel:
        wireless["channel"] = dbus.UInt32(int(channel))
    if bssid:
        # For AP mode this is the interface's requested/cloned MAC. The
        # `bssid` property itself is a client-side BSSID lock in NM.
        wireless["cloned-mac-address"] = dbus_byte_array(bytes.fromhex(bssid.replace(":", "")))

    values: dict[str, Any] = {
        "connection": section({
            "id": dbus.String(PROFILE_NAME),
            "uuid": dbus.String(profile_uuid),
            "type": dbus.String("802-11-wireless"),
            "interface-name": dbus.String(iface),
            "autoconnect": dbus.Boolean(False),
        }),
        "802-11-wireless": section(wireless),
        "ipv4": section({
            "method": dbus.String("shared"),
            "never-default": dbus.Boolean(True),
        }),
        "ipv6": section({"method": dbus.String("disabled")}),
    }
    if security != "open":
        key_mgmt = "wpa-psk" if security in ("sae", "wpa-psk sae") else security
        values["802-11-wireless-security"] = section({
            "auth-alg": dbus.String("open"),
            "key-mgmt": dbus.String(key_mgmt),
            "psk": dbus.String(str(settings.get("password", ""))),
            "proto": dbus.Array([dbus.String("rsn")], signature="s"),
            # Do not inherit a global PMF default that can add SAE to this
            # profile. WPA2/PMF-off is the reliable AP mode for this adapter.
            "pmf": dbus.Int32(1),
        })
    return dbus.Dictionary(values, signature="sa{sv}")


def add_unsaved_dbus_connection(settings: dict[str, Any], profile_uuid: str) -> tuple[Any, str]:
    """Create a transient NM profile and return its bus plus object path."""

    if dbus is None:
        raise RuntimeError("python-dbus is required to start a hotspot")
    bus = dbus.SystemBus()
    settings_object = bus.get_object(
        "org.freedesktop.NetworkManager",
        "/org/freedesktop/NetworkManager/Settings",
    )
    settings_iface = dbus.Interface(settings_object, "org.freedesktop.NetworkManager.Settings")
    connection_path = settings_iface.AddConnectionUnsaved(dbus_settings(settings, profile_uuid), timeout=20)
    return bus, str(connection_path)


def activate_dbus_connection(bus: Any, connection_path: str, iface: str) -> str:
    """Activate a transient profile on the selected wireless device."""

    nm_object = bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
    nm_iface = dbus.Interface(nm_object, "org.freedesktop.NetworkManager")
    device_path = nm_iface.GetDeviceByIpIface(dbus.String(iface), timeout=20)
    active_path = nm_iface.ActivateConnection(
        dbus.ObjectPath(connection_path), device_path, dbus.ObjectPath("/"), timeout=45
    )
    return str(active_path)


def active_connection_properties(bus: Any, active_path: str) -> tuple[int, int]:
    """Return NetworkManager ActiveConnection state and reason."""

    if dbus is None:
        raise RuntimeError("python-dbus is required to inspect activation")
    active_object = bus.get_object("org.freedesktop.NetworkManager", active_path)
    properties = dbus.Interface(active_object, "org.freedesktop.DBus.Properties")
    state = int(properties.Get(
        "org.freedesktop.NetworkManager.Connection.Active", "State", timeout=5
    ))
    reason = 0
    try:
        state_reason = properties.Get(
            "org.freedesktop.NetworkManager.Connection.Active", "StateReason", timeout=5
        )
        if len(state_reason) > 1:
            reason = int(state_reason[1])
    except Exception:
        pass
    return state, reason


def wait_for_activation(bus: Any, active_path: str, timeout: int = 30) -> None:
    """Wait until NM really activates the AP instead of accepting a queued request."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state, reason = active_connection_properties(bus, active_path)
        if state == 2:  # NM_ACTIVE_CONNECTION_STATE_ACTIVATED
            return
        if state in (3, 4):  # deactivating/deactivated
            detail = f" (reason {reason})" if reason else ""
            raise RuntimeError(f"NetworkManager failed to activate the hotspot{detail}")
        time.sleep(0.25)
    raise RuntimeError("NetworkManager timed out while activating the hotspot")


def reset_wifi_radio() -> tuple[bool, str]:
    """Reset the Wi-Fi radio after a failed AP transition."""

    off = run_command(["nmcli", "radio", "wifi", "off"], timeout=15)
    if off.returncode != 0:
        return False, clean_error(off)
    time.sleep(1)
    on = run_command(["nmcli", "radio", "wifi", "on"], timeout=15)
    if on.returncode != 0:
        return False, clean_error(on)
    time.sleep(1)
    return True, ""


def clean_dbus_error(error: Exception) -> str:
    text = str(error).strip()
    if text.startswith("org.freedesktop.") and ":" in text:
        text = text.split(":", 1)[1].strip()
    return text or error.__class__.__name__


def validate_settings(settings: dict[str, Any], adapter: dict[str, Any] | None) -> str:
    if not adapter:
        return "No Wi-Fi adapter was detected"
    if not adapter.get("apSupported"):
        return "This adapter does not report AP mode support"
    ssid = str(settings.get("ssid", ""))
    if not ssid or len(ssid.encode("utf-8")) > 32 or "\n" in ssid or "\r" in ssid:
        return "SSID must be 1–32 bytes and cannot contain line breaks"
    bssid = str(settings.get("bssid", "")).strip()
    if bssid:
        if not MAC_RE.fullmatch(bssid):
            return "BSSID must look like 02:11:22:33:44:55"
        if int(bssid[:2], 16) & 1:
            return "BSSID must be a unicast MAC address"
    security = str(settings.get("security", "wpa-psk"))
    if security not in ("open", "wpa-psk"):
        return "Unsupported security method"
    password = str(settings.get("password", ""))
    if security != "open" and not 8 <= len(password) <= 63:
        return "Password must contain 8–63 characters"
    band = str(settings.get("band", ""))
    allowed_bands = {str(item.get("value")) for item in adapter.get("bands", [])}
    if band and band not in allowed_bands:
        return "Selected band is not available on this adapter"
    channel = str(settings.get("channel", "")).strip()
    if channel:
        available_channels = set()
        for item in adapter.get("bands", []):
            if str(item.get("value")) == band:
                available_channels = {str(value) for value in item.get("channels", [])}
        if channel not in available_channels:
            return "Selected channel is not available for this band"
    return ""


def start_hotspot(settings: dict[str, Any], duration: int) -> tuple[bool, dict[str, Any] | str]:
    settings = normalize_preferences(settings)
    dependencies = dependency_status()
    if not dependencies["ready"]:
        names = ", ".join(item["label"] for item in dependencies["missing"])
        return False, f"Missing required components: {names}. Install them and try again."
    caps = inspect_adapters()
    adapter = next((item for item in caps["adapters"] if item["iface"] == settings.get("iface")), None)
    error = validate_settings(settings, adapter)
    if error:
        return False, error

    stop_ok, stop_error = stop_hotspot()
    if not stop_ok:
        return False, f"Could not clean up the previous hotspot: {stop_error}"

    persisted = dict(settings)
    persisted["duration"] = duration
    write_preferences(persisted)

    device = next((item for item in network_manager_devices()
                   if item["iface"] == settings.get("iface")), None)
    was_connected = bool(device and device.get("state") in ("connected", "connected (externally)"))
    last_error = ""
    for attempt in range(2):
        profile_uuid = str(uuid.uuid4())
        try:
            bus, connection_path = add_unsaved_dbus_connection(settings, profile_uuid)
            active_path = activate_dbus_connection(bus, connection_path, str(settings.get("iface", "")))
            wait_for_activation(bus, active_path)

            expires_at = time.time() + duration if duration > 0 else 0
            write_state({
                "uuid": profile_uuid,
                "expiresAt": expires_at,
                "iface": settings.get("iface", ""),
                "ssid": settings.get("ssid", ""),
                "band": settings.get("band", ""),
                "channel": settings.get("channel", ""),
                "security": settings.get("security", ""),
            })
            return True, status()
        except Exception as error:
            delete_profile(profile_uuid)
            last_error = clean_dbus_error(error) if dbus is not None else str(error)
            if attempt == 0 and not was_connected:
                reset_ok, reset_error = reset_wifi_radio()
                if reset_ok:
                    continue
                if reset_error:
                    last_error = f"{last_error}; Wi-Fi reset failed: {reset_error}"
            break
    return False, last_error or "Could not activate the hotspot"


def interface_ipv4(iface: str) -> str:
    output = command_output(["ip", "-o", "-4", "addr", "show", "dev", iface])
    match = re.search(r"\binet\s+(\S+)", output)
    return match.group(1) if match else ""


def client_count(iface: str) -> int:
    output = command_output(["iw", "dev", iface, "station", "dump"])
    return sum(1 for line in output.splitlines() if line.strip().startswith("Station "))


def status() -> dict[str, Any]:
    state = read_state()
    expires_at = float(state.get("expiresAt", 0) or 0)
    if expires_at and time.time() >= expires_at:
        stop_hotspot()
        return {"active": False, "expired": True, "expiresAt": 0}

    active = active_owned_profiles()
    if not active:
        if state:
            clear_state()
        return {"active": False, "expiresAt": 0}

    profile = active[0]
    iface = profile.get("device") or str(state.get("iface", ""))
    return {
        "active": True,
        "profile": profile.get("name", PROFILE_NAME),
        "uuid": profile.get("uuid", state.get("uuid", "")),
        "iface": iface,
        "ssid": state.get("ssid", ""),
        "band": state.get("band", ""),
        "channel": state.get("channel", ""),
        "security": state.get("security", ""),
        "bssid": interface_mac(iface),
        "ip": interface_ipv4(iface),
        "clients": client_count(iface),
        "expiresAt": expires_at,
    }


def handle(request: dict[str, Any]) -> tuple[bool, Any]:
    command = str(request.get("command", ""))
    if command == "loadPreferences":
        return True, read_preferences()
    if command == "savePreferences":
        settings = request.get("settings")
        if not isinstance(settings, dict):
            return False, "Missing hotspot preferences"
        write_preferences(settings)
        return True, {}
    if command in ("inspect", "capabilities"):
        return True, inspect_adapters()
    if command == "dependencies":
        return True, dependency_status()
    if command == "status":
        return True, status()
    if command == "stop":
        ok, error = stop_hotspot()
        return (True, {"active": False}) if ok else (False, error)
    if command == "start":
        settings = request.get("settings")
        if not isinstance(settings, dict):
            return False, "Missing hotspot settings"
        try:
            duration = max(0, int(request.get("duration", 0) or 0))
        except (TypeError, ValueError):
            return False, "Invalid timer duration"
        return start_hotspot(settings, duration)
    return False, "Unknown command"


def main() -> None:
    # The runtime directory is intentionally created before reading requests;
    # its permissions protect timer state.
    runtime_directory()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request is not an object")
            request_id = request.get("id", "")
            ok, data = handle(request)
            reply(request_id, ok, data if ok else None, data if not ok else "")
        except (ValueError, json.JSONDecodeError) as error:
            reply("", False, error=str(error))
        except Exception as error:  # Keep the JSON bridge alive after one bad request.
            print(f"hotspot backend: {error}", file=sys.stderr, flush=True)
            reply(request.get("id", "") if isinstance(locals().get("request"), dict) else "", False, error=str(error))


if __name__ == "__main__":
    main()
