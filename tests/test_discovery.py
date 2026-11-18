"""Discovery freshness and handshake scheduling without wireless hardware."""
import inspect
import queue
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'transport'))
import receiver
from sender import Packets, find_peers

ABILITY = bytes.fromhex('1511001112935869616f6d692031352419e438c87800')
ACCEPT = bytes.fromhex('0d110011122419e45823c2675e92')


class DiscoveryTests(unittest.TestCase):
    def test_recent_devices_are_available_without_waiting_for_another_advertisement(self):
        packets = Packets()
        with patch('sender.time.monotonic', return_value=100):
            packets.add(ABILITY, 456, 1)
            self.assertTrue(hasattr(packets, 'recent_peers'), 'No reusable discovery snapshot')
            self.assertEqual(packets.recent_peers(), {0xE419: 'Xiaomi 15'})
            self.assertIn('cancel', inspect.signature(find_peers).parameters)
            changes = []
            found = find_peers(packets, 7, changed=lambda peers: changes.append(dict(peers)),
                               cancel=lambda: True)
            self.assertEqual(found, {0xE419: 'Xiaomi 15'})
            self.assertEqual(changes, [found])

    def test_stale_devices_are_not_replayed_from_a_background_queue(self):
        packets = Packets()
        self.assertTrue(hasattr(packets, 'recent_peers'), 'No discovery freshness check')
        with patch('sender.time.monotonic', return_value=100):
            packets.add(ABILITY, 456, 1)
        with patch('sender.time.monotonic', return_value=103):
            self.assertEqual(packets.recent_peers(), {})
            with self.assertRaises(queue.Empty):
                packets.get(timeout=0, max_age=2)

    def test_busy_ability_invalidates_the_cached_ready_device(self):
        packets = Packets()
        self.assertTrue(hasattr(packets, 'recent_peers'), 'No discovery availability state')
        packets.add(ABILITY, 456, 1)
        busy = bytearray(ABILITY)
        busy[-3] |= 1
        packets.add(bytes(busy), 456, 1)
        self.assertEqual(packets.recent_peers(), {})

    def test_a_full_advertisement_queue_cannot_drop_the_selected_accept(self):
        packets = Packets()
        self.assertTrue(hasattr(packets, 'expect_response'), 'No protected handshake mailbox')
        packets.expect_response(0xE419, 0x67C2, 0x925E)
        for _ in range(300):
            packets.add(ABILITY, 456, 1)
        packets.add(ACCEPT, 456, 1)
        self.assertEqual(packets.get(timeout=0), ACCEPT)

    def test_another_task_cannot_replace_the_expected_response(self):
        packets = Packets()
        self.assertTrue(hasattr(packets, 'expect_response'), 'No task-specific handshake mailbox')
        packets.expect_response(0xE419, 0x67C2, 0x925F)
        packets.add(ABILITY, 456, 1)
        packets.add(ACCEPT, 456, 1)
        self.assertEqual(packets.get(timeout=0), ABILITY)


class RadioStartupTests(unittest.TestCase):
    def test_started_radio_has_no_fixed_half_second_sleep(self):
        self.assertTrue(hasattr(receiver, 'wait_for_publisher'), 'No state-driven radio startup')
        with patch('receiver.time.sleep') as sleep:
            receiver.wait_for_publisher(SimpleNamespace(status=SimpleNamespace(name='STARTED')))
        sleep.assert_not_called()

    def test_aborted_radio_is_reported_immediately(self):
        self.assertTrue(hasattr(receiver, 'wait_for_publisher'), 'No state-driven radio startup')
        with self.assertRaises(ConnectionError):
            receiver.wait_for_publisher(SimpleNamespace(status=SimpleNamespace(name='ABORTED')))


if __name__ == '__main__':
    unittest.main()
