import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "transport" / "receiver.py"
spec = importlib.util.spec_from_file_location("receiver", path)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)

class NetworkTests(unittest.TestCase):
    def test_waits_for_a_preferred_address_on_hotspot_subnet(self):
        rows = [
            {"IPAddress": "10.155.63.185", "PrefixLength": 24, "AddressState": 3},
            {"IPAddress": "192.168.63.25", "PrefixLength": 24, "AddressState": 2},
            {"IPAddress": "192.168.63.26", "PrefixLength": 24, "AddressState": 4},
        ]
        self.assertEqual(receiver.hotspot_source(rows, "192.168.63.174"), "192.168.63.26")
        self.assertIsNone(receiver.hotspot_source(rows[:2], "192.168.63.174"))

    def test_uses_hotspot_gateway_when_bluetooth_address_is_on_another_network(self):
        row = {"IPAddress": "10.155.63.185", "PrefixLength": 24,
               "AddressState": 4, "Gateway": "10.155.63.174"}
        self.assertEqual(receiver.hotspot_endpoint([row], "192.168.31.33"),
                         ("10.155.63.185", "10.155.63.174"))
        self.assertIsNone(receiver.hotspot_endpoint([{**row, "AddressState": 3}],
                                                    "192.168.31.33"))
    def test_selects_connected_interface_by_exact_ssid(self):
        wlan = "    Name : WLAN 2\n    State : disconnected\n    SSID : ap_mishare_fb4f-old\n    Name : Wi-Fi\n    SSID : ap_mishare_fb4f\n    State : connected\n"
        self.assertEqual(receiver.hotspot_interface(wlan, "ap_mishare_fb4f"), "Wi-Fi")
        self.assertIsNone(receiver.hotspot_interface(wlan, "ap_mishare_fb4f-old"))

if __name__ == "__main__":
    unittest.main()
