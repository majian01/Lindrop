import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "transport"))


class DirectGroupTests(unittest.TestCase):
    def test_xiaomi_group_advertises_vendor_information_element(self):
        from group_owner import xiaomi_group_information_element

        element = xiaomi_group_information_element()
        self.assertEqual(bytes(memoryview(element.oui)), bytes.fromhex("80ad16"))
        self.assertEqual(element.oui_type, 1)
        self.assertEqual(bytes(memoryview(element.value)), bytes.fromhex("0100000030"))

    def test_accepts_only_server_ready_for_current_recipient_and_task(self):
        from group_owner import server_ready_ip

        packet = bytes.fromhex("0f1100110224476e78042f5d19d989a4")
        self.assertEqual(server_ready_ip(packet, 0x5d2f, 55577), "192.168.137.164")
        self.assertIsNone(server_ready_ip(packet, 0x5d2f, 55578))
        self.assertIsNone(server_ready_ip(packet, 0x1234, 55577))
        self.assertIsNone(server_ready_ip(packet[:-1], 0x5d2f, 55577))
