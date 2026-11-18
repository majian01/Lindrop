"""The validated Lindrop transport, exposed to the .NET desktop over stdio."""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from contextlib import nullcontext

sys.path.insert(0, sys.argv[1])
from receiver import MtaPcReceiver
from sender import Packets, find_peers, send, watch_packets


class Transport:
    def __init__(self, identity):
        self.identity = identity
        self.output = threading.Lock()
        self.radio = threading.Lock()
        self.control = threading.Lock()
        self.cancelled = threading.Event()
        self.active = False
        self.worker = None
        self.decisions = {}
        self.inbox = None
        self.receiver = None
        self.scan_id = None
        self.discovery = None
        self.discovery_packets = Packets()

    def emit(self, kind, **payload):
        with self.output:
            print(json.dumps({'kind': kind, **payload}, ensure_ascii=True), flush=True)

    def log(self, message):
        self.emit('log', text=message)

    def start(self):
        if self.receiver is not None:
            return
        receiver = Receiver(self)
        try:
            receiver.start()
            deadline = time.monotonic() + 2
            while receiver._watch.status.name in ('CREATED', 'STOPPED') and time.monotonic() < deadline:
                time.sleep(0.05)
            if receiver._pub.status.name != 'STARTED' or receiver._watch.status.name != 'STARTED':
                raise OSError('Windows 蓝牙服务未进入监听状态')
        except Exception:
            receiver.stop()
            raise
        self.receiver = receiver
        self.emit('status', text='接收已开启')

    def stop(self):
        if self.receiver is not None:
            self.receiver.stop()
            self.receiver = None

    def run_send(self, packet):
        if not self.identity.is_file():
            self.emit('error', text='当前安装未配置小米原生发送身份。手机发到电脑仍可使用。')
            return
        if not self.begin():
            self.emit('error', text='正在处理另一笔传输')
            return
        try:
            self.stop()
            paths = [Path(value) for value in packet['files']]
            if not 1 <= len(paths) <= 250 or not all(path.is_file() for path in paths):
                raise ValueError('每批须包含 1 至 250 个有效文件')
            peer = int(packet['peer'])
            discovery = self.discovery
            observed, hint = (discovery[1].discovery_hint(peer)
                              if discovery and discovery[0] == packet.get('scan') else (False, None))
            self.scan_id = None
            if hint:
                self.log('GATT peripheral already discovered while scanning')
            elif observed:
                self.log('Scan observed this phone without a linked GATT peripheral; skipping repeated wait')
            with watch_packets() as packets:
                send(paths, peer, packets, '邻渡', 600, self.log,
                     prepared=packet['checksums'], gatt_wait=0 if observed else 2, gatt_hint=hint,
                     response_timeout=40, cancel=self.cancelled,
                     progress=lambda **data: self.emit('progress', direction='发送', **data),
                     independent_service=Path(__file__).resolve().with_name('lindrop-transfer.exe'),
                     independent_identity=self.identity)
            self.emit('done', direction='发送', peer=packet['name'], files=[str(path) for path in paths])
        except Exception as error:
            if not self.cancelled.is_set():
                self.emit('error', text=f'发送失败 · {error}')
        finally:
            try:
                self.start()
            except Exception as error:
                self.emit('error', text=f'无法恢复监听 · {error}')
            finally:
                self.finish()

    def begin(self):
        with self.control:
            if not self.radio.acquire(blocking=False):
                return False
            self.cancelled.clear()
            self.active = True
            self.emit('busy', value=True)
            return True

    def finish(self):
        with self.control:
            if self.cancelled.is_set():
                self.emit('status', text='已停止传输')
            self.active = False
            self.emit('busy', value=False)
            self.radio.release()

    def stop_transfer(self):
        with self.control:
            if not self.active:
                return
            self.cancelled.set()
            for pending in list(self.decisions.values()):
                pending[0].set()
            if self.worker is not None:
                self.signal_worker(self.worker)

    @staticmethod
    def signal_worker(worker):
        try:
            worker.stdin.write('stop\n')
            worker.stdin.flush()
        except (OSError, ValueError):
            pass

    def scan(self, serial):
        if self.scan_id != serial:
            return
        def report(peers, done):
            if self.scan_id == serial:
                self.emit('peers', id=serial, done=done,
                          items=[{'id': key, 'name': name} for key, name in peers.items()])
        try:
            receiver = self.receiver
            try:
                borrowed = (receiver is not None and receiver._watch is not None
                            and receiver._watch.status.name == 'STARTED')
            except (OSError, AttributeError):
                borrowed = False
            source = nullcontext(self.discovery_packets) if borrowed else watch_packets(self.discovery_packets)
            with source as packets:
                if self.scan_id == serial:
                    self.discovery = (serial, packets)
                recent = packets.recent_peers()
                if recent:
                    self.log(f'Discovery reused {len(recent)} freshly observed device(s)')
                peers = find_peers(packets, 7, changed=lambda found: report(found, False),
                                   cancel=lambda: self.scan_id != serial)
            report(peers, True)
        except Exception as error:
            self.emit('peers', id=serial, items=[], error=str(error))

    def commands(self):
        self.emit('ready')
        for line in sys.stdin:
            try:
                packet = json.loads(line)
                match packet['cmd']:
                    case 'listen':
                        self.inbox = Path(packet['folder'])
                        if self.receiver is not None:
                            self.receiver.out_dir = self.inbox
                        else:
                            self.start()
                    case 'decision':
                        pending = self.decisions.get(packet['id'])
                        if pending is not None:
                            pending[1] = bool(packet['accept'])
                            pending[0].set()
                    case 'scan':
                        self.scan_id = packet['id']
                        threading.Thread(target=self.scan, args=(packet['id'],), daemon=True).start()
                    case 'cancel_scan':
                        if self.scan_id == packet.get('id'):
                            self.scan_id = None
                    case 'send':
                        threading.Thread(target=self.run_send, args=(packet,), daemon=True).start()
                    case 'cancel':
                        self.stop_transfer()
                    case 'quit':
                        self.scan_id = None
                        self.stop_transfer()
                        break
            except Exception as error:
                self.emit('error', text=str(error))
        self.stop()


class Receiver(MtaPcReceiver):
    def _on_adv(self, watcher, args):
        try:
            self.transport.discovery_packets.observe_advertisement(args)
        except Exception as error:
            self.transport.log(f'Discovery observation failed: {error}')
        super()._on_adv(watcher, args)

    def __init__(self, transport):
        super().__init__('邻渡', transport.inbox, log=transport.log, group_owner=True,
                         decide=self.ask, completed=self.saved,
                         failed=self.error)
        self.transport = transport

    def transfer_cancelled(self):
        return self.transport.cancelled.is_set() or super().transfer_cancelled()

    def error(self, error):
        if not self.transfer_cancelled():
            self.transport.emit('error', text=f'接收失败 · {error}')

    def pull(self, offer, out_dir, log, user=None, source_ip=None):
        executable = Path(__file__).resolve().with_name('lindrop-transfer.exe')
        if not executable.is_file():
            if (executable.parent / 'backend').is_dir():
                raise FileNotFoundError('找不到安装包中的 Rust 接收组件')
            return super().pull(offer, out_dir, log, user, source_ip)
        request = {
            'target': offer['ip'] or '192.168.43.1', 'source': source_ip,
            'user': user or str(offer['to_dev_id']), 'task': offer['task_id'], 'folder': str(out_dir),
        }
        log('Starting Rust HTTPS receiver')
        files, failure = None, None
        with subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW) as worker:
            worker.stdin.write(json.dumps(request) + '\n')
            worker.stdin.flush()
            with self.transport.control:
                self.transport.worker = worker
                if self.transfer_cancelled():
                    self.transport.signal_worker(worker)
            try:
                for line in worker.stdout:
                    event = json.loads(line)
                    if event['kind'] == 'log':
                        log(event['text'])
                    elif event['kind'] == 'progress':
                        self.transport.emit('progress', direction='接收', **{key: value for key, value in event.items() if key != 'kind'})
                    elif event['kind'] == 'error':
                        failure = event['text']
                    elif event['kind'] == 'done':
                        files = event['files']
            finally:
                with self.transport.control:
                    self.transport.worker = None
        if worker.returncode != 0 or files is None:
            raise OSError(failure or f'Rust receiver exited with code {worker.returncode}')
        return [Path(file) for file in files]

    def _handle(self, offer):
        if not self.transport.begin():
            return
        try:
            super()._handle(offer)
        finally:
            self.transport.finish()

    def ask(self, offer):
        ident = f'{offer["sender_dev_id"]}-{offer["task_id"]}'
        pending = [threading.Event(), False]
        with self.transport.control:
            if self.transfer_cancelled():
                return False
            self.transport.decisions[ident] = pending
        try:
            self.transport.emit('offer', id=ident, name=offer['name'] or '小米设备', count=offer['file_count'])
            pending[0].wait(19)
            return pending[1] and not self.transfer_cancelled()
        finally:
            with self.transport.control:
                self.transport.decisions.pop(ident, None)
            self.transport.emit('offer_closed', id=ident)

    def saved(self, offer, paths):
        self.transport.emit('done', direction='接收', peer=offer['name'] or '小米设备', files=[str(path) for path in paths])


if __name__ == '__main__':
    Transport(Path(sys.argv[2])).commands()
