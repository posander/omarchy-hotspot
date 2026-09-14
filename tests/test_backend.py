import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


BACKEND_PATH = Path(__file__).parents[1] / "bin" / "hotspot_backend.py"
# The normal desktop runtime directory is intentionally not writable in the
# restricted test environment. Keep test state in a private temporary dir.
TEST_RUNTIME = tempfile.mkdtemp(prefix="omarchy-hotspot-test-")
os.environ["XDG_RUNTIME_DIR"] = TEST_RUNTIME
SPEC = importlib.util.spec_from_file_location("hotspot_backend", BACKEND_PATH)
backend = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(backend)


class BackendTests(unittest.TestCase):
    def test_iw_device_parser(self):
        devices = backend.parse_iw_dev(
            """phy#0
\tInterface wlan0
\t\tifindex 3
\t\taddr 02:11:22:33:44:55
\t\ttype managed
"""
        )
        self.assertEqual(devices[0]["iface"], "wlan0")
        self.assertEqual(devices[0]["phy"], "phy0")
        self.assertEqual(devices[0]["mac"], "02:11:22:33:44:55")

    def test_phy_capabilities_parser(self):
        parsed = backend.parse_phy_info(
            """Supported interface modes:
\t\t * managed
\t\t * AP

valid interface combinations:
\t\t * #{ managed } <= 1, #{ AP } <= 1,

Band 1:
\t\t* 2412.0 MHz [1] (20.0 dBm)
\t\t* 2437.0 MHz [6] (20.0 dBm)
\t\t* 2462.0 MHz [11] (20.0 dBm)
Band 2:
\t\t* 5180.0 MHz [36] (20.0 dBm)
\t\t* 5260.0 MHz [52] (20.0 dBm) (no IR)
"""
        )
        self.assertEqual(parsed["modes"], ["managed", "AP"])
        self.assertTrue(parsed["concurrent"])
        self.assertEqual(parsed["bands"][0]["value"], "bg")
        self.assertEqual(parsed["bands"][0]["channels"], [1, 6, 11])
        self.assertEqual(parsed["bands"][1]["channels"], [36])

    def test_keyfile_contains_requested_properties(self):
        keyfile = backend.profile_keyfile(
            {
                "iface": "wlan0",
                "ssid": "Temporary network",
                "bssid": "02:11:22:33:44:55",
                "hidden": True,
                "security": "wpa-psk",
                "password": "password123",
                "band": "bg",
                "channel": "6",
            },
            "12345678-1234-1234-1234-123456789abc",
        )
        self.assertIn("mode=ap", keyfile)
        self.assertIn("hidden=true", keyfile)
        self.assertIn("cloned-mac-address=02:11:22:33:44:55", keyfile)
        self.assertIn("channel=6", keyfile)
        self.assertIn("psk=password123", keyfile)

    def test_dbus_settings_contains_requested_properties(self):
        if backend.dbus is None:
            self.skipTest("python-dbus is not installed")
        values = backend.dbus_settings(
            {
                "iface": "wlan0",
                "ssid": "Temporary network",
                "bssid": "02:11:22:33:44:55",
                "hidden": True,
                "security": "wpa-psk",
                "password": "password123",
                "band": "bg",
                "channel": "6",
            },
            "12345678-1234-1234-1234-123456789abc",
        )
        self.assertEqual(str(values["connection"]["type"]), "802-11-wireless")
        self.assertEqual(bytes(values["802-11-wireless"]["ssid"]), b"Temporary network")
        self.assertEqual(
            bytes(values["802-11-wireless"]["cloned-mac-address"]),
            bytes.fromhex("021122334455"),
        )
        self.assertEqual(str(values["802-11-wireless-security"]["key-mgmt"]), "wpa-psk")

    def test_preferences_round_trip_is_private(self):
        original_path = backend.PREFERENCES_PATH
        backend.PREFERENCES_PATH = Path(TEST_RUNTIME) / "preferences" / "preferences.json"
        try:
            backend.write_preferences({
                "iface": "wlan0",
                "ssid": "Saved network",
                "security": "wpa-psk",
                "password": "saved-password",
                "duration": 0,
            })
            loaded = backend.read_preferences()
            self.assertEqual(loaded["ssid"], "Saved network")
            self.assertEqual(loaded["password"], "saved-password")
            self.assertEqual(loaded["duration"], "0")
            self.assertIn("saved-password", backend.PREFERENCES_PATH.read_text(encoding="utf-8"))
            self.assertEqual(backend.PREFERENCES_PATH.stat().st_mode & 0o777, 0o600)
            self.assertEqual(backend.PREFERENCES_PATH.parent.stat().st_mode & 0o777, 0o700)
        finally:
            backend.PREFERENCES_PATH = original_path

    def test_settings_validation(self):
        adapter = {
            "apSupported": True,
            "bands": [{"value": "bg", "channels": [1, 6, 11]}],
        }
        settings = {
            "ssid": "Temporary network",
            "security": "wpa-psk",
            "password": "password123",
            "band": "bg",
            "channel": "6",
        }
        self.assertEqual(backend.validate_settings(settings, adapter), "")
        settings["password"] = "short"
        self.assertIn("Password", backend.validate_settings(settings, adapter))

    def test_start_requires_dnsmasq(self):
        original_which = backend.shutil.which
        try:
            backend.shutil.which = lambda name: None if name == "dnsmasq" else original_which(name)
            started, error = backend.start_hotspot({"iface": "wlan0"}, 0)
            self.assertFalse(started)
            self.assertIn("dnsmasq", error)
        finally:
            backend.shutil.which = original_which

    def test_dependency_status_reports_missing_component(self):
        original_which = backend.shutil.which
        try:
            backend.shutil.which = lambda name: None if name == "dnsmasq" else "/usr/bin/" + name
            status = backend.dependency_status()
            self.assertFalse(status["ready"])
            self.assertEqual(status["missing"], [{"package": "dnsmasq", "label": "dnsmasq"}])
        finally:
            backend.shutil.which = original_which

    def test_ipv4_forwarding_status_is_boolean(self):
        self.assertIsInstance(backend.ipv4_forwarding_enabled(), bool)

    def test_configure_forwarding_uses_fixed_ufw_commands(self):
        original_values = {
            "dropin": backend.FORWARDING_DROPIN,
            "which": backend.shutil.which,
            "devices": backend.network_manager_devices,
            "default_route": backend.default_route_iface,
            "ufw_enabled": backend.ufw_enabled,
            "run_privileged": backend.run_privileged,
            "forwarding": backend.ipv4_forwarding_enabled,
        }
        calls = []
        backend.FORWARDING_DROPIN = Path(TEST_RUNTIME) / "sysctl.d" / "99-omarchy-hotspot.conf"
        backend.FORWARDING_DROPIN.parent.mkdir(parents=True, exist_ok=True)
        backend.shutil.which = lambda name: {
            "pkexec": "/usr/bin/pkexec",
            "tee": "/usr/bin/tee",
            "sysctl": "/usr/bin/sysctl",
            "ufw": "/usr/bin/ufw",
        }.get(name)
        backend.network_manager_devices = lambda: [{"iface": "wlan0", "type": "wifi"}]
        backend.default_route_iface = lambda: "enp3s0"
        backend.ufw_enabled = lambda: True
        backend.run_privileged = lambda args, input_text=None, timeout=20: (
            calls.append((args, input_text)),
            backend.subprocess.CompletedProcess(args, 0, "", ""),
        )[1]
        backend.ipv4_forwarding_enabled = lambda: True
        try:
            ok, data = backend.configure_forwarding("wlan0", "")
            self.assertTrue(ok)
            self.assertEqual(data, {"forwarding": True, "ufw": True, "uplink": "enp3s0"})
            self.assertEqual(calls[0][0], ["/usr/bin/tee", str(backend.FORWARDING_DROPIN)])
            self.assertEqual(calls[0][1], "net.ipv4.ip_forward=1\n")
            self.assertEqual(calls[1][0], ["/usr/bin/sysctl", "-w", "net.ipv4.ip_forward=1"])
            self.assertEqual(calls[-1][0], ["/usr/bin/ufw", "reload"])
            self.assertEqual(len(calls), 7)
        finally:
            backend.FORWARDING_DROPIN = original_values["dropin"]
            backend.shutil.which = original_values["which"]
            backend.network_manager_devices = original_values["devices"]
            backend.default_route_iface = original_values["default_route"]
            backend.ufw_enabled = original_values["ufw_enabled"]
            backend.run_privileged = original_values["run_privileged"]
            backend.ipv4_forwarding_enabled = original_values["forwarding"]


if __name__ == "__main__":
    unittest.main()
