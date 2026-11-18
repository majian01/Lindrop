"""Local identity interoperability check against a separately supplied native BKS.

Usage: python native_identity_loopback.py rust-executable pc.bks pc-public-cert.pem
No private key is written to disk or printed.
"""
import hashlib
import http.client
import json
import ssl
import sys
import tempfile
import struct
import hmac
import unittest
from contextlib import closing
from pathlib import Path

root = Path(__file__).resolve().parents[1]
executable, store, certificate = [Path(sys.argv.pop(1)).resolve() for _ in range(3)]
sys.path.insert(0, str(root / 'transport'))


class NativeIdentityTests(unittest.TestCase):
    def test_wrong_password_is_rejected(self):
        from bks_identity import load_identity
        with self.assertRaises(ValueError):
            load_identity(store, 'incorrect-test-password')

    def test_corrupt_truncated_and_oversized_fields_are_rejected(self):
        from bks_identity import load_identity
        original = store.read_bytes()
        modified = bytearray(original)
        modified[-1] ^= 1
        invalid_length = original[:4] + b'\xff\xff\xff\xff' + original[8:]
        directory = root / 'work' / 'native-identity-invalid'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as folder:
            path = Path(folder) / 'invalid.bks'
            for value in (b'', original[:20], original[:-5], modified, invalid_length):
                path.write_bytes(value)
                with self.assertRaises(ValueError):
                    load_identity(path)

    def test_certificate_entries_also_count_towards_the_limit(self):
        from bks_identity import derive, load_identity, STORE_PASSWORD
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        der = x509.load_pem_x509_certificate(certificate.read_bytes()).public_bytes(serialization.Encoding.DER)
        def utf(value):
            return struct.pack('>H', len(value)) + value
        cert = utf(b'X.509') + struct.pack('>I',len(der)) + der
        entry = b'\x01' + utf(b'cert') + bytes(12) + cert
        body = entry * 33 + b'\0'
        salt = b's' * 20
        key = derive(STORE_PASSWORD, salt, 1, 3, 20)
        data = struct.pack('>II',2,20) + salt + struct.pack('>I',1) + body + hmac.new(key,body,hashlib.sha1).digest()
        directory = root / 'work' / 'native-identity-invalid'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as folder:
            path = Path(folder) / 'many-entries.bks'
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'entry limit'):
                load_identity(path)

    def test_native_identity_is_supported(self):
        self.assertTrue((root / 'transport' / 'bks_identity.py').is_file(),
                        'Native BKS identity import is not implemented')
        from bks_identity import load_identity
        from independent_server import IndependentServer
        identity = load_identity(store)
        self.assertTrue(identity)
        folder = root / 'work' / 'native-identity-loopback'
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder) as directory:
            path = Path(directory) / 'sample.txt'
            path.write_bytes(b'Native identity, independent implementation\n')
            info = dict(id='456_1', name=path.name, size=path.stat().st_size,
                        md5=hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest(), mimeType='text/plain')
            context = ssl.create_default_context(cafile=str(certificate))
            context.check_hostname = False  # Native PC certificate has no DNS/IP name.
            with IndependentServer(executable, '127.0.0.1', 0, 456, 123,
                    {'456_1': (path, info)}, lambda _: None, identity_store=store) as server:
                with closing(http.client.HTTPSConnection('127.0.0.1', server.port, timeout=4,
                                                        context=context)) as client:
                    client.request('GET', '/api/1.0/fileinfo?user=123&task=456')
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read())['data'][0]['md5'], info['md5'])
                    client.request('GET', '/api/1.0/file?user=123&task=456&id=456_1')
                    response = client.getresponse()
                    self.assertEqual(response.read(), path.read_bytes())
            with IndependentServer(executable, '127.0.0.1', 0, 456, 123,
                    {'456_1': (path, info)}, lambda _: None, allow_test_identity=True) as server:
                with closing(http.client.HTTPSConnection('127.0.0.1', server.port, timeout=4,
                                                        context=context)) as client:
                    with self.assertRaises(ssl.SSLCertVerificationError):
                        client.request('GET', '/api/1.0/fileinfo?user=123&task=456')


if __name__ == '__main__':
    unittest.main()
