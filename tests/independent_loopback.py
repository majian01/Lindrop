"""Exercise the independent HTTPS service; no Xiaomi DLL is loaded.

Run with an explicitly built lindrop-transfer.exe as the only argument.
"""

import hashlib
import http.client
import json
import queue
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12


EXECUTABLE = Path(sys.argv.pop(1)).resolve()
ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / 'work' / 'independent-loopback'
SCRATCH.mkdir(parents=True, exist_ok=True)


def identity():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'Independent Share')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    return list(pkcs12.serialize_key_and_certificates(
        b'share', key, cert, None, serialization.NoEncryption()))


class Service:
    def __init__(self, files, identity_bytes, **options):
        self.events = []
        self.messages = queue.Queue()
        self.process = subprocess.Popen([str(EXECUTABLE), '--serve'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        self.process.stdin.write(json.dumps(dict(bind='127.0.0.1', port=0, user='123',
            task=456, identity=identity_bytes, identity_password='', files=files, **options)) + '\n')
        self.process.stdin.flush()
        try:
            event = self.messages.get(timeout=8)
            if event.get('kind') != 'ready':
                raise AssertionError(f'Independent server did not become ready: {event}')
            self.port = event['port']
        except BaseException:
            self.close()
            raise

    def read(self):
        for line in self.process.stdout:
            event = json.loads(line)
            self.events.append(event)
            self.messages.put(event)

    def client(self):
        return http.client.HTTPSConnection('127.0.0.1', self.port, timeout=4,
                                          context=ssl._create_unverified_context())

    def request(self, route, data=None, *, user='123', task='456', headers=None, method=None):
        with closing(self.client()) as client:
            client.request(method or ('GET' if data is None else 'POST'),
                f'/api/1.0/{route}?user={user}&task={task}',
                None if data is None else json.dumps({'status': 0, 'data': data}),
                headers or {'Content-Type': 'application/json'})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('stop\n')
                self.process.stdin.flush()
                self.process.wait(timeout=3)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=3)
        self.reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


class IndependentServiceTests(unittest.TestCase):
    def test_socket_buffer_experiment_rejects_unbounded_or_tiny_sizes(self):
        for size in (8, 16 * 1024 * 1024):
            with self.assertRaisesRegex(AssertionError, 'send buffer'):
                unexpected = Service([self.entry], self.identity_bytes, send_buffer_bytes=size)
                self.addCleanup(unexpected.close)

    @classmethod
    def setUpClass(cls):
        cls.identity_bytes = identity()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.directory.cleanup)
        self.content = b'independent transfer\x00' * 100_000
        self.source = Path(self.directory.name) / '文件.bin'
        self.source.write_bytes(self.content)
        self.digest = hashlib.md5(self.content, usedforsecurity=False).hexdigest()
        self.entry = dict(id='456_1', path=str(self.source), name=self.source.name,
            size=len(self.content), mimeType='application/octet-stream', md5=self.digest)
        self.service = Service([self.entry], self.identity_bytes)
        self.addCleanup(self.service.close)

    def manifest(self):
        status, _, body = self.service.request('fileinfo')
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_manifest_uses_protocol_fields_without_local_path(self):
        manifest = self.manifest()
        self.assertEqual(manifest['status'], 0)
        entry = manifest['data'][0]
        self.assertEqual(entry['name'], '文件.bin')
        self.assertEqual(entry['type'], 0)
        self.assertEqual(entry['md5'], self.digest)
        self.assertNotIn('path', entry)

    def test_download_streams_exact_bytes_and_reports_progress(self):
        self.manifest()
        status, headers, content = self.service.request('file&id=456_1')
        # A route with the id in the path must never be treated as a file query.
        self.assertEqual(status, 404)
        status, _, content = self.service.request('file', task='456&id=456_1')
        self.assertEqual(status, 200)
        self.assertEqual(content, self.content)
        time.sleep(0.05)
        progress = [e for e in self.service.events if e['kind'] == 'progress']
        self.assertTrue(progress)
        self.assertEqual(progress[-1]['current'], len(self.content))

    def test_wrong_session_and_ambiguous_query_are_rejected(self):
        for user, task in [('124', '456'), ('123', '457'), ('123&user=124', '456')]:
            self.assertEqual(self.service.request('fileinfo', user=user, task=task)[0], 404)
        self.assertFalse(any(e['kind'] == 'http_started' for e in self.service.events))

    def test_unknown_id_and_paths_are_rejected(self):
        self.manifest()
        for file_id in ('unknown', '../../private', '%2e%2e%2fprivate'):
            self.assertEqual(self.service.request('file', task=f'456&id={file_id}')[0], 400)

    def test_bad_checksum_cannot_complete_task(self):
        self.manifest()
        _, _, body = self.service.request('fileverify', {'id': '456_1', 'md5': '0' * 32})
        self.assertNotEqual(int(json.loads(body)['status']), 0)
        _, _, body = self.service.request('taskcomplete', {'reason': 'success'})
        self.assertEqual(int(json.loads(body)['status']), 0)
        time.sleep(0.05)
        self.assertTrue(any(e['kind'] == 'failed' for e in self.service.events))
        self.assertFalse(any(e['kind'] == 'complete' for e in self.service.events))

    def test_checksum_receipt_and_task_complete(self):
        self.manifest()
        self.assertEqual(self.service.request('file', task='456&id=456_1')[2], self.content)
        _, _, body = self.service.request('fileverify', {'id': '456_1', 'md5': self.digest.upper()})
        self.assertEqual(int(json.loads(body)['status']), 0)
        _, _, body = self.service.request('taskcomplete', {'reason': 'success'})
        reply = json.loads(body)
        self.assertEqual(int(reply['status']), 0)
        self.assertEqual(reply['data'], {'action': '0', 'nexttaskid': '', 'nexttaskfilecnt': '0'})
        time.sleep(0.05)
        self.assertTrue(any(e['kind'] == 'complete' for e in self.service.events))

    def test_a_failed_receipt_revokes_an_earlier_success(self):
        self.manifest()
        self.service.request('file', task='456&id=456_1')
        self.service.request('fileverify', {'id':'456_1', 'md5':self.digest})
        self.service.request('fileverify', {'id':'456_1', 'md5':'0'*32})
        self.service.request('taskcomplete', {'reason':'success'})
        time.sleep(0.05)
        self.assertFalse(any(e['kind'] == 'complete' for e in self.service.events))
        self.assertTrue(any(e['kind'] == 'failed' for e in self.service.events))

    def test_phone_adapter_requires_an_identity_explicitly(self):
        sys.path.insert(0, str(ROOT / 'transport'))
        from independent_server import IndependentServer
        with self.assertRaisesRegex(ValueError, 'identity'):
            IndependentServer(EXECUTABLE, '127.0.0.1', 0, 456, 123,
                              {'456_1': (self.source, self.entry)}, lambda _: None)

    def test_single_range_and_unsatisfiable_range(self):
        self.manifest()
        for value, expected in [('bytes=10-19', self.content[10:20]),
                                ('bytes=-7', self.content[-7:]),
                                (f'bytes={len(self.content)-4}-', self.content[-4:])]:
            status, headers, body = self.service.request('file', task='456&id=456_1',
                                                        headers={'Range': value})
            self.assertEqual(status, 206)
            self.assertEqual(body, expected)
            self.assertTrue(any(k.lower() == 'content-range' for k in headers))
        self.assertEqual(self.service.request('file', task='456&id=456_1',
            headers={'Range': f'bytes={len(self.content)}-'})[0], 416)

    def test_keep_alive_and_head(self):
        with closing(self.service.client()) as client:
            client.request('GET', '/api/1.0/fileinfo?user=123&task=456')
            response = client.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            client.request('GET', '/api/1.0/file?user=123&task=456&id=456_1')
            response = client.getresponse()
            self.assertEqual(response.read(), self.content)
        self.manifest()
        status, headers, body = self.service.request('file', task='456&id=456_1', method='HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')
        self.assertEqual(int(next(v for k, v in headers.items() if k.lower() == 'content-length')),
                         len(self.content))

    def test_stop_closes_listening_socket_promptly(self):
        port = self.service.port
        started = time.monotonic()
        self.service.close()
        self.assertLess(time.monotonic() - started, 2)
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', port), timeout=0.3)

    def test_existing_rust_receiver_can_finish_without_vendor_runtime(self):
        destination = Path(self.directory.name) / 'received'
        destination.mkdir()
        request = dict(target='127.0.0.1', source='127.0.0.1', port=self.service.port,
                       user='123', task=456, folder=str(destination))
        result = subprocess.run([str(EXECUTABLE)], input=json.dumps(request) + '\n',
            capture_output=True, encoding='utf-8', timeout=12)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(Path(events[-1]['files'][0]).read_bytes(), self.content)
        self.assertTrue(any(e['kind'] == 'complete' for e in self.service.events))

    def test_stop_interrupts_an_active_large_download(self):
        path = Path(self.directory.name) / 'large.bin'
        with path.open('wb') as output:
            for _ in range(32):
                output.write(self.content)
        entry = dict(self.entry, path=str(path), size=path.stat().st_size)
        service = Service([entry], self.identity_bytes)
        self.addCleanup(service.close)
        service.request('fileinfo')
        with closing(service.client()) as client:
            client.request('GET', '/api/1.0/file?user=123&task=456&id=456_1')
            response = client.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(len(response.read(1)), 1)
            started = time.monotonic()
            service.close()
            self.assertLess(time.monotonic() - started, 2)
            with self.assertRaises((http.client.IncompleteRead, OSError)):
                response.read()
        self.assertFalse(any(e['kind'] == 'complete' for e in service.events))

    def test_multiple_files_including_empty_file(self):
        empty = Path(self.directory.name) / 'empty.txt'
        empty.write_bytes(b'')
        entry = dict(id='456_2', path=str(empty), name=empty.name, size=0,
                     mimeType='text/plain', md5=hashlib.md5(b'', usedforsecurity=False).hexdigest())
        service = Service([self.entry, entry], self.identity_bytes)
        self.addCleanup(service.close)
        self.assertEqual(len(json.loads(service.request('fileinfo')[2])['data']), 2)
        for info in (self.entry, entry):
            status, _, body = service.request('file', task=f'456&id={info["id"]}')
            self.assertEqual(status, 200)
            self.assertEqual(len(body), info['size'])
            self.assertEqual(int(json.loads(service.request('fileverify', {
                'id': info['id'], 'md5': info['md5']})[2])['status']), 0)
        service.request('taskcomplete', {'reason': 'success'})
        time.sleep(0.05)
        self.assertTrue(any(e['kind'] == 'complete' for e in service.events))

    def test_malformed_and_oversized_json_are_rejected(self):
        self.manifest()
        for body in (b'{', b'x' * 65537):
            with closing(self.service.client()) as client:
                client.request('POST', '/api/1.0/fileverify?user=123&task=456', body,
                               {'Content-Type': 'application/json'})
                response = client.getresponse()
                self.assertEqual(response.status, 400)
                response.read()

    def test_idle_tls_connection_does_not_block_downloads(self):
        with socket.create_connection(('127.0.0.1', self.service.port), timeout=2) as raw:
            with ssl._create_unverified_context().wrap_socket(raw, server_hostname='localhost'):
                self.manifest()
                self.assertEqual(self.service.request('file', task='456&id=456_1')[2], self.content)

    def test_python_sender_adapter_uses_the_independent_service(self):
        sys.path.insert(0, str(ROOT / 'transport'))
        from independent_server import IndependentServer, new_identity
        self.assertNotEqual(new_identity(), new_identity())
        progress = []
        with IndependentServer(EXECUTABLE, '127.0.0.1', 0, 456, 123,
                {'456_1': (self.source, {k: v for k, v in self.entry.items() if k != 'path'})},
                lambda _: None, progress=lambda **data: progress.append(data), allow_test_identity=True) as service:
            with closing(http.client.HTTPSConnection('127.0.0.1', service.port, timeout=4,
                    context=ssl._create_unverified_context())) as client:
                for route, data in [('fileinfo', None), ('file&id=456_1', None),
                        ('fileverify', {'id': '456_1', 'md5': self.digest}),
                        ('taskcomplete', {'reason': 'success'})]:
                    path = route.replace('&id=', '?id=')
                    separator = '&' if '?' in path else '?'
                    client.request('GET' if data is None else 'POST',
                        f'/api/1.0/{path}{separator}user=123&task=456',
                        None if data is None else json.dumps({'status': 0, 'data': data}),
                        {'Content-Type': 'application/json'})
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
            self.assertTrue(service.http_started.wait(1))
            self.assertTrue(service.complete.wait(1))
            self.assertFalse(service.failed.is_set())
        self.assertEqual(progress[-1]['current'], len(self.content))


if __name__ == '__main__':
    unittest.main()
