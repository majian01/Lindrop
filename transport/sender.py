"""Send files to HyperOS Xiaomi Share over a Windows Wi-Fi Direct group."""

import argparse
import hashlib
import ipaddress
import json
import mimetypes
import queue
import secrets
import ssl
import subprocess
import threading
import time
from contextlib import contextmanager
from collections import deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from gatt import ADVERT_UUID, push_group_info
from group_owner import group_response, publish_group
from receiver import MFR_ID, MtaPcReceiver, current_ssid, derive_psk, parse_offer, parse_tlv
from windows_ip import preferred_ipv4


def build_offer(device, peer, task, nonce, name, count, address):
    label = name.encode()[:6].decode('utf-8', 'ignore').encode()
    ip = ipaddress.IPv4Address(address).packed
    fields = b''.join(value.to_bytes(2, 'little') for value in (peer, task, nonce))
    # Verified against a Xiaomi 15: the phone's transfer scan accepts this
    # layout and shows the receive prompt.
    return (bytes([17 + len(label)]) + bytes.fromhex('1100110224')
            + device.to_bytes(2, 'little') + bytes([(9 + len(label)) << 4 | 8, 0x11, count, 0])
            + fields + label + b'\x2f' + ip[2:])


def build_server_ready(device, peer, task, address):
    """Announce the sender's HTTPS endpoint after the receiver joins."""
    ip = ipaddress.IPv4Address(address).packed
    return (bytes.fromhex('0f1100110224') + device.to_bytes(2, 'little')
            + bytes.fromhex('7804') + peer.to_bytes(2, 'little')
            + task.to_bytes(2, 'little') + ip[2:])


def parse_peer(packet):
    if len(packet) >= 6 and packet[5] & 15 == 2:
        packet = packet[:5] + bytes([packet[5] | 1]) + packet[6:]
    tlv = parse_tlv(packet)
    app = tlv['app0']
    if tlv['name'] and tlv['dev_id'] is not None and app and len(app) == 3 and app[0] & 7 == 0 and app[2] == 0:
        return tlv['dev_id'], tlv['name'].decode('utf-8', 'replace')
    return None


def response_action(packet, peer, device, task):
    if (len(packet) >= 14 and packet[1:4] == bytes.fromhex('110011')
            and packet[4] in {2, 18} and packet[5] == 0x24
            and packet[6:8] == peer.to_bytes(2, 'little') and packet[9] & 7 == 3
            and packet[10:14] == device.to_bytes(2, 'little') + task.to_bytes(2, 'little')):
        return packet[9] & 0xF8
    return None


def response_verdict(packet, peer, device, task):
    """Map the phone's response packet to 'accept' or 'decline'.

    PCBlePacketHelper.c(deviceId, remoteDeviceId, taskId, true) emits 0x23
    (accept); the same builder with false emits 0x13 (decline).
    """
    return {0x20: 'accept', 0x10: 'decline'}.get(
        response_action(packet, peer, device, task))


class Packets:
    """Advertisement queue plus the BLE address each device id was seen from."""

    def __init__(self):
        self.queue = deque(maxlen=256)
        self.changed = threading.Condition()
        self.expected_response = None
        self.response = None
        self.addresses = {}
        self.peripherals = {}
        self.peer_seen = {}
        self.peer_names = {}

    def get(self, timeout=0.25, max_age=None):
        deadline = time.monotonic() + timeout
        with self.changed:
            while True:
                if self.response is not None:
                    packet, self.response = self.response, None
                    return packet
                while self.queue:
                    observed, packet = self.queue.popleft()
                    if max_age is None or time.monotonic() - observed <= max_age:
                        return packet
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                self.changed.wait(remaining)

    def expect_response(self, peer, device, task):
        with self.changed:
            self.expected_response = peer, device, task
            self.response = None

    def recent_peers(self, max_age=2):
        now = time.monotonic()
        with self.changed:
            return {peer: name for peer, name in self.peer_names.items()
                    if now - self.peer_seen[peer][1] <= max_age}

    def note_peripheral(self, address, address_type):
        with self.changed:
            key = address, address_type
            self.peripherals.pop(key, None)
            self.peripherals[key] = time.monotonic()
            if len(self.peripherals) > 256:
                self.peripherals.pop(next(iter(self.peripherals)))

    def add(self, packet, address, address_type):
        peer = parse_peer(packet)
        offer = parse_offer(packet)
        now = time.monotonic()
        with self.changed:
            if self.expected_response and response_action(packet, *self.expected_response) in (0x10, 0x20):
                self.response = packet
            else:
                self.queue.append((now, packet))
            peer_id = None
            if peer:
                peer_id = peer[0]
                first, last = self.peer_seen.get(peer_id, (now, now))
                self.peer_seen[peer_id] = (first if now - last <= 2 else now), now
                self.peer_names[peer_id] = peer[1]
                if len(self.peer_seen) > 128:
                    oldest = min(self.peer_seen, key=lambda key: self.peer_seen[key][1])
                    self.peer_seen.pop(oldest)
                    self.peer_names.pop(oldest, None)
            else:
                # Only a full ability advert can invalidate readiness. A transfer
                # response or unrelated packet must not erase a discovered peer.
                tlv = parse_tlv(packet)
                if tlv['name'] and tlv['app0'] and len(tlv['app0']) == 3:
                    self.peer_names.pop(tlv['dev_id'], None)
            if offer and offer['sender_dev_id'] is not None:
                peer_id = offer['sender_dev_id']
            if peer_id is not None:
                self.addresses.pop(peer_id, None)
                self.addresses[peer_id] = address, address_type
                if len(self.addresses) > 256:
                    self.addresses.pop(next(iter(self.addresses)))
            self.changed.notify_all()

    def peripheral_for(self, peer):
        with self.changed:
            address = self.addresses.get(peer)
            last = self.peripherals.get(address)
            return address if last is not None and time.monotonic() - last <= 10 else None

    def discovery_hint(self, peer):
        with self.changed:
            first, last = self.peer_seen.get(peer, (0, 0))
            if not last or time.monotonic() - last > 2 or peer not in self.peer_names:
                return False, None
            return last - first >= 0.8, self.peripheral_for(peer)

    def observe_advertisement(self, args):
        advertisement = args.advertisement
        if ADVERT_UUID in advertisement.service_uuids:
            self.note_peripheral(args.bluetooth_address, int(args.bluetooth_address_type))
        sections = getattr(advertisement, 'manufacturer_data', ())
        for section in sections:
            if section.company_id == MFR_ID:
                self.add(bytes(memoryview(section.data)), args.bluetooth_address, int(args.bluetooth_address_type))


@contextmanager
def watch_packets(packets=None):
    from winrt.windows.devices.bluetooth.advertisement import (
        BluetoothLEAdvertisementWatcher, BluetoothLEScanningMode)

    packets = packets if packets is not None else Packets()
    watcher = BluetoothLEAdvertisementWatcher()
    watcher.scanning_mode = BluetoothLEScanningMode.ACTIVE

    def received(_, args):
        packets.observe_advertisement(args)

    token = watcher.add_received(received)
    try:
        watcher.start()
        deadline = time.monotonic() + 2
        while watcher.status.name == 'CREATED' and time.monotonic() < deadline:
            time.sleep(0.05)
        if watcher.status.name != 'STARTED':
            raise ConnectionError(f'BLE scanner status: {watcher.status.name}')
        yield packets
    finally:
        watcher.stop()
        watcher.remove_received(token)


def find_peers(packets, timeout, changed=None, cancel=None):
    deadline = time.monotonic() + timeout
    peers = packets.recent_peers()
    if peers and changed:
        changed(peers)
    while (remaining := deadline - time.monotonic()) > 0:
        if cancel is not None and cancel():
            break
        try:
            packets.get(timeout=min(remaining, 0.25), max_age=2)
        except queue.Empty:
            pass
        latest = packets.recent_peers()
        if latest != peers:
            peers = latest
            if changed:
                changed(peers)
    return peers


def direct_addresses():
    try:
        return {address for description, address in preferred_ipv4()
                if 'wi-fi direct' in description.casefold() and address.startswith('192.168.')}
    except OSError:
        return direct_addresses_powershell()


def direct_addresses_powershell():
    command = (
        "$adapters = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' "
        "-and $_.InterfaceDescription -like '*Wi-Fi Direct*' }; "
        "$adapters | Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Where-Object AddressState -eq Preferred | Select-Object -ExpandProperty IPAddress"
    )
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
        capture_output=True, text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.startswith('192.168.')}


def pause_publisher(radio):
    radio._pub.stop()
    deadline = time.monotonic() + 2
    while radio._pub.status.name not in {'STOPPED', 'CREATED', 'ABORTED'}:
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)


def resume_publisher(radio):
    radio._pub.start()
    deadline = time.monotonic() + 2
    while radio._pub.status.name != 'STARTED':
        if radio._pub.status.name == 'ABORTED' or time.monotonic() >= deadline:
            break
        time.sleep(0.05)


def wait_for_group_address(previous, cancel=None):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        addresses = direct_addresses() - previous
        if len(addresses) == 1:
            return addresses.pop()
        if cancel is None:
            time.sleep(0.1)
        elif cancel.wait(0.1):
            raise InterruptedError('Transfer stopped')
    raise TimeoutError('Windows did not assign a unique IPv4 address to the Wi-Fi Direct group')


def server_tls():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'MiShare-PC')])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
                   .sign(private_key, hashes.SHA256()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    with TemporaryDirectory(prefix='mishare-tls-') as folder:
        pem = Path(folder) / 'server.pem'
        pem.write_bytes(certificate.public_bytes(serialization.Encoding.PEM) + private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        context.load_cert_chain(pem)
    return context


def file_manifest(paths, task, prepared=None):
    if prepared is not None and len(prepared) != len(paths):
        raise ValueError('File list and checksums do not match')
    files = {}
    for index, path in enumerate(paths, 1):
        if prepared is None:
            with path.open('rb') as source:
                digest = hashlib.file_digest(source, lambda: hashlib.md5(usedforsecurity=False)).hexdigest()
            size = path.stat().st_size
        else:
            item = prepared[index - 1]
            size, digest = item['size'], item['md5']
            if path.stat().st_size != size:
                raise OSError(f'File changed after checksum: {path.name}')
        file_id = f'{task}_{index}'
        mime_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        files[file_id] = path, {
            'id': file_id, 'name': path.name, 'size': size,
            'mimeType': mime_type, 'md5': digest,
        }
    return files


class TransferServer(ThreadingHTTPServer):
    def __init__(self, address, task, peer, files, context, log):
        self.task, self.peer = str(task), str(peer)
        self.files, self.log = files, log
        self.client = None
        self.verified = set()
        self.complete = threading.Event()
        self.lock = threading.Lock()
        self.context = context
        super().__init__(address, TransferHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        try:
            return self.context.wrap_socket(connection, server_side=True, do_handshake_on_connect=False), address
        except OSError as error:
            self.log(f'HTTPS handshake from {address[0]} failed: {error}')
            connection.close()
            raise


class TransferHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, data, status=0):
        content = json.dumps({'status': status, 'data': data}, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def request_path(self):
        target = urlsplit(self.path)
        query = parse_qs(target.query)
        if query.get('task') != [self.server.task] or query.get('user') != [self.server.peer]:
            self.server.log(f'Rejected {self.command} {target.path}: task={query.get("task")!r}, user={query.get("user")!r}')
            self.send_error(403)
            return '', {}
        with self.server.lock:
            if self.server.client is None and target.path == '/api/1.0/fileinfo' and self.command == 'GET':
                self.server.client = self.client_address[0]
            valid_client = self.server.client == self.client_address[0]
        if not valid_client:
            self.send_error(403)
            return '', {}
        return target.path, query

    def do_GET(self):
        route, query = self.request_path()
        if route == '/api/1.0/fileinfo':
            self.server.log(f'Phone {self.client_address[0]} requested file information')
            self.reply([entry for _, entry in self.server.files.values()])
        elif route == '/api/1.0/file':
            file = self.server.files.get(query.get('id', [''])[0])
            if not file:
                self.send_error(404)
                return
            path, info = file
            with path.open('rb') as source:
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(info['size']))
                self.end_headers()
                remaining = info['size']
                while remaining and (data := source.read(min(remaining, 1024 * 1024))):
                    self.wfile.write(data)
                    remaining -= len(data)
            self.server.log(f'Phone downloaded {info["name"]} ({info["size"] - remaining} bytes)')
        elif route:
            self.send_error(404)

    def do_POST(self):
        route, _ = self.request_path()
        if not route:
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise ValueError('Invalid body length')
            body = json.loads(self.rfile.read(length))
            data = body['data']
            if not isinstance(data, dict):
                raise ValueError('Invalid data')
        except (KeyError, ValueError, TypeError):
            self.send_error(400)
            return

        if route == '/api/1.0/report':
            self.reply({})
        elif route == '/api/1.0/fileverify':
            file_id = data.get('id')
            file = self.server.files.get(file_id) if isinstance(file_id, str) else None
            if body.get('status') != 0 or not file or data.get('md5') != file[1]['md5']:
                self.reply({}, status=1)
                return
            with self.server.lock:
                self.server.verified.add(file_id)
            self.server.log(f'Phone verified MD5 for {file[1]["name"]}')
            self.reply({})
        elif route == '/api/1.0/taskcomplete':
            with self.server.lock:
                verified = self.server.verified == self.server.files.keys()
            if body.get('status') == 0 and verified:
                self.reply({})
                self.server.complete.set()
            else:
                self.reply({}, status=1)
        else:
            self.send_error(404)


def send(paths, peer, packets, name, timeout, log, official_runtime=None, *,
         prepared=None, gatt_wait=10, gatt_hint=None, response_timeout=None, cancel=None, progress=None,
         independent_service=None, independent_identity=None):
    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Transfer stopped')

    setup_started = time.monotonic()
    task = secrets.randbelow(65535) + 1
    files = file_manifest(paths, task, prepared)
    original_network = current_ssid()
    original_addresses = direct_addresses()
    radio = MtaPcReceiver(name, Path('.'), log, group_owner=True)
    packets.expect_response(peer, radio.dev_id, task)
    try:
        gatt_deadline = time.monotonic() + gatt_wait
        radio.start(watch=False)
        with publish_group(radio.dev_id, derive_psk, log) as (ssid, nonce):
            check_cancel()
            address = wait_for_group_address(original_addresses, cancel)
            log(f'Wi-Fi Direct IPv4 ready after {time.monotonic() - setup_started:.3f} s')
            managed_service = independent_service is not None or official_runtime is not None
            if independent_service is not None:
                from independent_server import IndependentServer
                service = IndependentServer(independent_service, address, 9999, task, peer, files, log,
                                            progress=progress, cancel=cancel, identity_store=independent_identity)
            elif official_runtime is not None:
                from official_server import OfficialServer
                service = OfficialServer(official_runtime, 9999, task, peer, files, log, progress=progress)
            else:
                service = TransferServer((address, 9999), task, peer, files, server_tls(), log)
            with service as server:
                thread = None
                if not managed_service:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                try:
                    radio.wait_for_discovery()
                    # Give the phone a moment to see the ability advert, then
                    # push the group's P2P info over GATT while the phone is
                    # still idle: its peripheral is not connectable during a
                    # transfer, and Windows cannot connect while we advertise.
                    while gatt_hint is None and packets.peripheral_for(peer) is None and time.monotonic() < gatt_deadline:
                        check_cancel()
                        time.sleep(min(0.1, max(0, gatt_deadline - time.monotonic())))
                    peripheral = packets.peripheral_for(peer) or gatt_hint
                    if peripheral is None:
                        log('No GATT peripheral linked to this phone; using the BLE offer')
                    else:
                        phone_address, phone_type = peripheral
                        pause_publisher(radio)
                        try:
                            push_group_info(phone_address, phone_type, radio.dev_id, ssid,
                                            derive_psk(radio.dev_id, b'', nonce), 9999, log, cancel=cancel)
                        finally:
                            resume_publisher(radio)
                    check_cancel()
                    radio._set_payload(build_offer(radio.dev_id, peer, task, nonce, name, len(paths), address))
                    log(f'Offer {task} to {peer:04x}; P2P {ssid} at {address}; setup {time.monotonic() - setup_started:.3f} s')
                    deadline = time.monotonic() + timeout
                    response_deadline = (time.monotonic() + response_timeout
                                         if response_timeout is not None else deadline)
                    accepted = False
                    accepted_at = 0.0
                    announced = False
                    while not server.complete.is_set() and time.monotonic() < deadline:
                        check_cancel()
                        if managed_service and server.failed.is_set():
                            raise ConnectionAbortedError(getattr(server, 'failure', None)
                                or '手机已结束传输，文件未全部通过校验')
                        if not accepted and time.monotonic() >= response_deadline:
                            if not managed_service or not server.http_started.is_set():
                                raise TimeoutError('手机未确认接收。请结束手机上正在进行的小米互传任务后重试。')
                        if accepted and not announced and time.monotonic() - accepted_at >= 6:
                            announced = True
                            if not managed_service or not server.http_started.is_set():
                                radio._set_payload(build_server_ready(radio.dev_id, peer, task, address))
                                log('Announced file service; waiting for HTTPS download')
                        try:
                            packet = packets.get(timeout=0.25)
                        except queue.Empty:
                            continue
                        verdict = response_verdict(packet, peer, radio.dev_id, task)
                        if verdict == 'decline':
                            raise ConnectionAbortedError('Phone declined the offer')
                        if verdict == 'accept' and not accepted:
                            accepted = True
                            accepted_at = time.monotonic()
                            log('Phone accepted via BLE; publishing group parameters')
                            # Mirror the receive path: the group owner publishes
                            # the group parameters (nonce, channel) so the guest
                            # can associate.
                            radio._set_payload(group_response(radio.dev_id, peer, task, nonce))
                            log(f'Group parameters published in {time.monotonic() - accepted_at:.3f} s')
                    if not server.complete.is_set():
                        raise TimeoutError('Phone did not confirm that all files were received and verified')
                    if official_runtime is not None:
                        # The native callback runs just before the HTTP completion reply.
                        time.sleep(0.5)
                    log(f'Phone completed transfer of {len(paths)} file(s)')
                finally:
                    if thread is not None:
                        server.shutdown()
                        thread.join()
    finally:
        radio.stop()
        log(f'P2P group ended; router before {original_network!r}, now {current_ssid()!r}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', type=Path, nargs='+', help='Files to send')
    parser.add_argument('--device-id', type=lambda value: int(value, 16), help='Recipient BLE ID in hex')
    parser.add_argument('--name', default='邻渡', help='Sender name, up to six UTF-8 bytes')
    parser.add_argument('--scan-seconds', type=float, default=12)
    parser.add_argument('--timeout', type=float, default=180, help='Transfer timeout in seconds')
    parser.add_argument('--official-runtime', type=Path, help='Folder containing the Xiaomi 5.8.1.130 transfer DLLs')
    parser.add_argument('--independent-service', type=Path, help='Experimental independent Rust HTTPS executable')
    parser.add_argument('--identity-store', type=Path, help='Native PC identity BKS, supplied separately')
    args = parser.parse_args()
    if args.independent_service is not None and args.identity_store is None:
        parser.error('--independent-service requires --identity-store for native phone compatibility')
    if not 0 < len(args.name.encode()) <= 6:
        parser.error('--name must be 1 to 6 UTF-8 bytes')
    if len(args.files) > 250 or any(not path.is_file() for path in args.files):
        parser.error('Specify up to 250 existing files')
    if args.device_id is not None and not 0 <= args.device_id <= 0xFFFF:
        parser.error('--device-id must be a 16-bit hexadecimal ID')

    def log(message):
        print(f'{time.strftime("%H:%M:%S")} {message}', flush=True)

    try:
        with watch_packets() as packets:
            peer = args.device_id
            if peer is None:
                log('Looking for phones with Xiaomi Share enabled and visible')
                peers = sorted(find_peers(packets, args.scan_seconds).items())
                if not peers:
                    raise ConnectionError('No nearby phone advertising Xiaomi Share was found')
                for index, (device, name) in enumerate(peers, 1):
                    print(f'{index}. {name} [{device:04x}]')
                selected = int(input('Select recipient number: '))
                if not 1 <= selected <= len(peers):
                    raise ValueError('Invalid recipient number')
                peer = peers[selected - 1][0]
            send(args.files, peer, packets, args.name, args.timeout, log, args.official_runtime,
                 independent_service=args.independent_service, independent_identity=args.identity_store)
    except (OSError, ValueError, EOFError) as error:
        log(f'Send failed: {error}')
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
