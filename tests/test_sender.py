import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'transport'))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from gatt import build_p2p_info
from group_owner import server_ready_ip
from receiver import parse_offer
from sender import Packets, build_offer, build_server_ready, parse_peer, response_action, response_verdict


class PeripheralSelectionTest(unittest.TestCase):
    def test_peripheral_must_match_selected_phone(self):
        packets = Packets()
        packets.note_peripheral(123, 1)
        packets.add(bytes.fromhex('1511001112935869616f6d692031352419e438c87800'), 456, 1)
        self.assertIsNone(packets.peripheral_for(0xE419))
        packets.note_peripheral(456, 1)
        self.assertEqual(packets.peripheral_for(0xE419), (456, 1))


class SenderProtocolTest(unittest.TestCase):
    def test_offer_uses_xiaomi_length_and_address(self):
        # This layout is the one a Xiaomi 15 accepts (the receive prompt
        # appears); a name-first variant was rejected on real hardware.
        packet = build_offer(0x5D2F, 0xFB65, 0x1234, 0x5678, 'MiSend', 2, '192.168.137.1')
        self.assertEqual(packet.hex(), '1711001102242f5df811020065fb341278564d6953656e642f8901')
        offer = parse_offer(packet)
        self.assertEqual(offer['to_dev_id'], 0xFB65)
        self.assertEqual(offer['ip'], '192.168.137.1')
        self.assertTrue(offer['support2_0'])

    def test_p2p_info_round_trip(self):
        phone = ec.generate_private_key(ec.SECP256R1())
        public = base64.b64encode(phone.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        payload = json.loads(build_p2p_info(
            0x67C2, 'a2:d3:65:66:5e:1d', 'DIRECT-67c2', 'psk12345', 9999, public))
        self.assertEqual(payload['id'], '67c2')
        self.assertEqual(payload['port'], 9999)
        ephemeral = serialization.load_der_public_key(base64.b64decode(payload['key']))
        shared = phone.exchange(ec.ECDH(), ephemeral)
        expected = {'ssid': 'DIRECT-67c2', 'psk': 'psk12345', 'mac': 'a2:d3:65:66:5e:1d'}
        for name, text in expected.items():
            decryptor = Cipher(algorithms.AES(shared), modes.CTR(b'0102030405060708')).decryptor()
            self.assertEqual(
                (decryptor.update(base64.b64decode(payload[name]))
                 + decryptor.finalize()).decode(), text)

    def test_server_ready_announces_sender_endpoint(self):
        packet = build_server_ready(0x5D2F, 0xE419, 0x1234, '192.168.137.1')
        self.assertEqual(packet.hex(), '0f11001102242f5d780419e434128901')
        self.assertEqual(server_ready_ip(packet, 0xE419, 0x1234), '192.168.137.1')

    def test_mobile_ability_uses_tag_two(self):
        self.assertEqual(parse_peer(bytes.fromhex('101100111242546573742465fb38401000')),
                         (0xFB65, 'Test'))
        self.assertEqual(parse_peer(bytes.fromhex('1511001112935869616f6d692031352419e438c87800')),
                         (0xE419, 'Xiaomi 15'))
        self.assertIsNone(parse_peer(bytes.fromhex('101100111243546573742465fb38401030')))

    def test_response_accept_and_decline(self):
        # Captured from a Xiaomi 15 after the user tapped accept.
        accept = bytes.fromhex('0d110011122419e45823c2675e92')
        self.assertEqual(response_action(accept, 0xE419, 0x67C2, 0x925E), 0x20)
        decline = bytes.fromhex('0d110011122419e45813c2675e92')
        self.assertEqual(response_action(decline, 0xE419, 0x67C2, 0x925E), 0x10)

    def test_captured_phone_packet_means_accept(self):
        # 0x23 is the accept action emitted by PCBlePacketHelper.c(..., true).
        accept = bytes.fromhex('0d110011122419e45823c2675e92')
        self.assertEqual(response_verdict(accept, 0xE419, 0x67C2, 0x925E), 'accept')
        decline = bytes.fromhex('0d110011122419e45813c2675e92')
        self.assertEqual(response_verdict(decline, 0xE419, 0x67C2, 0x925E), 'decline')

    def test_response_only_matches_selected_session(self):
        accept = bytes.fromhex('0d110011122419e45823c2675e92')
        self.assertIsNone(response_action(accept, 0xE419, 0x67C2, 0x925F))
        self.assertIsNone(response_action(accept, 0xE419, 0x67C3, 0x925E))
        self.assertIsNone(response_action(accept, 0xE418, 0x67C2, 0x925E))
