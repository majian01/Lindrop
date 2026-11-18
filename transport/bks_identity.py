"""Import an externally supplied native BKS identity for interoperability.

Independent BKS v2 / PKCS#12 SHA-1 KDF implementation, using the published
Bouncy Castle store format and RFC 7292 Appendix B. Native private keys and
certificates are never embedded in this module or written as plaintext files.
"""

import hashlib
import hmac
import io
import struct

from Crypto.Cipher import DES3
from Crypto.Util.Padding import unpad
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

# The native PC resource's public wrapping password is not an authentication key.
STORE_PASSWORD = 'ih9gqpx'


class Reader:
    def __init__(self, data):
        self.stream = io.BytesIO(data)

    def read(self, count):
        if not 0 <= count <= 2_000_000:
            raise ValueError('Invalid BKS field length')
        value = self.stream.read(count)
        if len(value) != count:
            raise ValueError('Truncated BKS identity')
        return value

    def integer(self):
        return struct.unpack('>I', self.read(4))[0]

    def utf(self):
        return self.read(struct.unpack('>H', self.read(2))[0]).decode('utf-8')

    def certificate(self):
        if self.utf() != 'X.509':
            raise ValueError('Unsupported BKS certificate format')
        return x509.load_der_x509_certificate(self.read(self.integer()))


def derive(password, salt, iterations, purpose, count):
    if not 1 <= iterations <= 100_000 or not 1 <= len(salt) <= 1024:
        raise ValueError('Invalid BKS KDF parameters')
    def expand(value):
        size = 64 * ((len(value) + 63) // 64)
        return (value * ((size + len(value) - 1) // len(value)))[:size] if value else b''
    material = bytearray(expand(salt) + expand(password.encode('utf-16be') + b'\0\0'))
    output = b''
    while len(output) < count:
        digest = bytes([purpose]) * 64 + material
        for _ in range(iterations):
            digest = hashlib.sha1(digest).digest()
        output += digest
        addend = int.from_bytes((digest * 4)[:64], 'big') + 1
        for position in range(0, len(material), 64):
            block = int.from_bytes(material[position:position+64], 'big')
            material[position:position+64] = ((block + addend) % (1 << 512)).to_bytes(64, 'big')
    return output[:count]


def load_identity(path, password=STORE_PASSWORD):
    if path.stat().st_size > 2_000_000:
        raise ValueError('BKS identity is too large')
    original = path.read_bytes()
    reader = Reader(original)
    if reader.integer() != 2:
        raise ValueError('Only BKS v2 identities are supported')
    salt, iterations = reader.read(reader.integer()), reader.integer()
    body = reader.stream.tell()
    entries = []
    entry_count = 0
    while True:
        kind = reader.read(1)[0]
        if kind == 0:
            break
        if kind not in (1, 4):
            raise ValueError('Unsupported BKS entry')
        entry_count += 1
        if entry_count > 32:
            raise ValueError('BKS entry limit exceeded')
        reader.utf()  # Alias is not used for selecting the file-transfer identity.
        reader.read(8)
        count = reader.integer()
        if count > 8:
            raise ValueError('BKS certificate chain is too long')
        chain = [reader.certificate() for _ in range(count)]
        if kind == 1:
            reader.certificate()
        else:
            entries.append((reader.read(reader.integer()), chain))
    end = reader.stream.tell()
    authentication = reader.read(20)
    if reader.stream.read(1) or not hmac.compare_digest(authentication,
            hmac.new(derive(password, salt, iterations, 3, 20), original[body:end], hashlib.sha1).digest()):
        raise ValueError('BKS integrity check failed')
    if len(entries) != 1 or len(entries[0][1]) != 1:
        raise ValueError('BKS must contain one file-transfer identity')
    blob, chain = entries[0]
    sealed = Reader(blob)
    salt, iterations = sealed.read(sealed.integer()), sealed.integer()
    cipher = DES3.new(derive(password, salt, iterations, 1, 24), DES3.MODE_CBC,
                     derive(password, salt, iterations, 2, 8))
    clear = Reader(unpad(cipher.decrypt(sealed.stream.read()), 8))
    if (clear.read(1)[0], clear.utf(), clear.utf()) != (0, 'PKCS#8', 'RSA'):
        raise ValueError('Unsupported BKS private-key encoding')
    key = serialization.load_der_private_key(clear.read(clear.integer()), None)
    if clear.stream.read(1) or key.public_key().public_numbers() != chain[0].public_key().public_numbers():
        raise ValueError('BKS private key and certificate do not match')
    return list(pkcs12.serialize_key_and_certificates(
        b'share', key, chain[0], None, serialization.NoEncryption()))
