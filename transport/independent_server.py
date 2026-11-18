"""Independent Rust HTTPS service behind the existing sender interface."""

import json
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone


def new_identity():
    """Generate our own short-lived identity; never reuse a vendor key."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'Independent Share')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    return list(pkcs12.serialize_key_and_certificates(
        b'share', key, cert, None, serialization.NoEncryption()))


class IndependentServer:
    """The caller owns lifetime/cancellation after startup via context exit."""
    def __init__(self, executable, address, port, task, peer, files, log, progress=None, cancel=None,
                 identity_store=None, allow_test_identity=False):
        if identity_store is None and not allow_test_identity:
            raise ValueError('Missing native identity_store; a test certificate cannot authenticate to the phone')
        self.executable, self.log = executable, log
        self.progress, self.cancel = progress, cancel
        self.request = dict(bind=address, port=port, task=task, user=str(peer),
            files=[dict(info, path=str(path.resolve())) for path, info in files.values()])
        self.complete = threading.Event()
        self.failed = threading.Event()
        self.http_started = threading.Event()
        self.ready = threading.Event()
        self.closing = threading.Event()
        self.failure = None
        self.worker = None
        self.reader = None
        self.identity_store = identity_store

    def read(self):
        try:
            for line in self.worker.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    self.log(f'Rust service: {line.strip()}')
                    continue
                kind = event.get('kind')
                if kind == 'ready':
                    self.port = event['port']
                    self.ready.set()
                elif kind == 'http_started':
                    self.http_started.set()
                    self.log('Phone reached the independent Rust HTTPS file service')
                elif kind == 'progress' and self.progress is not None:
                    self.progress(**{key: value for key, value in event.items() if key != 'kind'})
                elif kind == 'complete':
                    self.complete.set()
                elif kind in ('failed', 'error'):
                    self.failure = event.get('text', '手机已结束传输，文件未全部通过校验')
                    self.failed.set()
                elif kind == 'log':
                    self.log(event['text'])
        finally:
            if not self.closing.is_set() and not self.complete.is_set():
                self.failure = self.failure or 'Independent Rust service exited unexpectedly'
                self.failed.set()

    def __enter__(self):
        if not self.executable.is_file():
            raise FileNotFoundError('找不到独立 Rust 传输组件', str(self.executable))
        if self.identity_store is not None:
            from bks_identity import load_identity
            if not self.identity_store.is_file():
                raise FileNotFoundError('未配置小米原生发送身份')
            identity = load_identity(self.identity_store)
        else:
            identity = new_identity()
        self.request.update(identity=identity, identity_password='')
        self.worker = subprocess.Popen([str(self.executable), '--serve'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        try:
            self.worker.stdin.write(json.dumps(self.request) + '\n')
            self.worker.stdin.flush()
            self.request.pop('identity')
            deadline = time.monotonic() + 8
            while not self.ready.wait(0.05):
                if self.cancel is not None and self.cancel.is_set():
                    raise InterruptedError('Transfer stopped')
                if self.failed.is_set():
                    raise OSError(self.failure)
                if time.monotonic() >= deadline:
                    raise TimeoutError('Independent Rust service did not become ready')
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise
        self.log('Independent Rust HTTPS service ready; no Xiaomi runtime loaded')
        return self

    def __exit__(self, exception_type=None, *_):
        self.closing.set()
        if self.worker is not None:
            try:
                if self.worker.poll() is None:
                    self.worker.stdin.write('stop\n')
                    self.worker.stdin.flush()
                    self.worker.wait(timeout=2)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                if self.worker.poll() is None:
                    self.worker.kill()
                    self.worker.wait(timeout=2)
            if self.reader is not None:
                self.reader.join(timeout=1)
            try:
                self.worker.stdin.close()
            except OSError:
                pass
            self.worker.stdout.close()
            if self.worker.returncode != 0 and not (self.cancel is not None and self.cancel.is_set()):
                self.failure = self.failure or f'Independent service exited with code {self.worker.returncode}'
                self.failed.set()
                self.log(self.failure)
                if exception_type is None:
                    raise OSError(self.failure)
