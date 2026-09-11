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


if __name__ == "__main__":
    unittest.main()
