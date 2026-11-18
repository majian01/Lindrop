"""File service adapter for the x64 MiDropTransfer DLL from Xiaomi 5.8.1.130."""

import ctypes as C
import hashlib
import json
import os
import threading
import time


RUNTIME_HASHES = {
    'MiDropTransfer.dll': 'fac9fde6d7b86a83555ea51fc7523dd3d03ec23c55611625ef5c1537ee8b95aa',
    'LIBEAY32.dll': '0dfd5ed5265a014213c71b12df30141cdebadf3410c58ba9cf85d1c7ff6bbef0',
    'ssleay32.dll': '73d263a26866fe2a6d163e9bc955e60227bbe7560d6465a21cddcab5e2fd3655',
}
PTR = C.c_void_p


class OfficialServer:
    def __init__(self, runtime, port, task, peer, files, log, progress=None):
        runtime = runtime.resolve()
        for name, digest in RUNTIME_HASHES.items():
            with (runtime / name).open('rb') as file:
                if hashlib.file_digest(file, 'sha256').hexdigest() != digest:
                    raise ValueError(f'Unsupported Xiaomi component version: {name}')
        with os.add_dll_directory(str(runtime)):
            self.dll = C.CDLL(str(runtime / 'MiDropTransfer.dll'))
        self.start = getattr(self.dll, '?start_server@http@@YAXGAEBUServerDelegate@1@_N@Z')
        self.start.argtypes = (C.c_ushort, PTR, C.c_bool)
        self.start.restype = None
        self.stop = getattr(self.dll, '?stop_server@http@@YAXG@Z')
        self.stop.argtypes = (C.c_ushort,)
        self.stop.restype = None
        self.crt = C.CDLL('ucrtbase.dll')
        self.crt.malloc.argtypes = (C.c_size_t,)
        self.crt.malloc.restype = PTR
        self.port, self.task, self.peer = port, str(task).encode(), str(peer).encode()
        self.files = {key.encode(): (os.fsencode(path.resolve()), info) for key, (path, info) in files.items()}
        self.manifest = json.dumps({'status': 0, 'data': [
            {**info, 'type': 0} for _, info in self.files.values()
        ]}).encode()
        self.log = log
        self.verified = set()
        self.download_started = {}
        self.progress = progress
        self.transferred = {}
        self.progress_started = None
        self.lock = threading.Lock()
        self.complete = threading.Event()
        self.failed = threading.Event()
        self.http_started = threading.Event()
        self.delegate = (C.c_uint64 * (0x440 // 8))()
        self.callbacks = []
        self.copy = C.CFUNCTYPE(PTR, PTR, PTR)(self._copy_callable)
        self.destroy = C.CFUNCTYPE(None, PTR, C.c_bool)(lambda *_: None)
        self._register(0, None, 1, self._log)
        self._register(0x40, PTR, 4, self._fileinfo)
        self._register(0x80, C.c_bool, 3, self._can_download)
        self._register(0xc0, None, 7, self._report)
        self._register(0x100, C.c_int, 6, self._task_complete)
        self._register(0x140, C.c_bool, 4, self._file_verify)
        self._register(0x180, C.c_bool, 2, self._is_cancelled)
        self._register(0x1c0, PTR, 2, self._file_path)
        self._register(0x400, None, 4, lambda *_: None)

    def _register(self, offset, result, arguments, method):
        # This DLL uses x64 MSVC's release std::function layout.
        callback = C.CFUNCTYPE(result, PTR, *([PTR] * arguments))(method)
        vtable = (PTR * 5)(C.cast(self.copy, PTR), C.cast(self.copy, PTR),
                           C.cast(callback, PTR), None, C.cast(self.destroy, PTR))
        self.callbacks.append((callback, vtable))
        self.delegate[offset // 8] = C.addressof(vtable)
        self.delegate[(offset + 0x38) // 8] = C.addressof(self.delegate) + offset

    @staticmethod
    def _copy_callable(source, target):
        PTR.from_address(target).value = PTR.from_address(source).value
        return target

    @staticmethod
    def _arg(pointer):
        address = PTR.from_address(pointer).value
        return C.string_at(address) if address else b''

    def _session(self, user, task):
        return self._arg(user) == self.peer and self._arg(task) == self.task

    def _string(self, target, value):
        C.memset(target, 0, 32)
        C.c_size_t.from_address(target + 24).value = 15
        if len(value) <= 15:
            C.memmove(target, value, len(value))
        else:
            # Match MSVC's aligned allocation for buffers of 4096 bytes or larger.
            size = len(value) + 1
            allocation = self.crt.malloc(size + 39 if size >= 4096 else size)
            if not allocation:
                return target
            address = allocation
            if size >= 4096:
                address = (allocation + 39) & ~31
                PTR.from_address(address - 8).value = allocation
            C.memmove(address, value + b'\0', size)
            PTR.from_address(target).value = address
            C.c_size_t.from_address(target + 24).value = len(value)
        C.c_size_t.from_address(target + 16).value = len(value)
        return target

    def _log(self, _, message):
        text = PTR.from_address(message).value
        if text:
            message = C.wstring_at(text)
            if 'load_server_certificate' in message or 'OnHandle' in message and 'method =' in message:
                self.log(message)

    def _fileinfo(self, _, target, user, task, _device):
        if not self._session(user, task):
            return self._string(target, b'')
        if not self.http_started.is_set():
            self.http_started.set()
            self.log('Phone reached the HTTPS file service')
        return self._string(target, self.manifest)

    def _can_download(self, _, user, task, file_id):
        return self._session(user, task) and self._arg(file_id) in self.files

    def _file_path(self, _, target, file_id):
        key = self._arg(file_id)
        file = self.files.get(key)
        if file:
            with self.lock:
                self.download_started.setdefault(key, time.monotonic())
                if self.progress_started is None:
                    self.progress_started = time.monotonic()
        return self._string(target, file[0] if file else b'')

    def _is_cancelled(self, _, user, task):
        return not self._session(user, task)

    def _report(self, _, code, _name, user, task, file_id, current, _total):
        key = self._arg(file_id)
        if C.c_int.from_address(code).value != 4 or not self._session(user, task) or key not in self.files:
            return
        self._emit_progress(key, C.c_uint64.from_address(current).value)

    def _emit_progress(self, key, current):
        if self.progress is None:
            return
        with self.lock:
            now = time.monotonic()
            if self.progress_started is None:
                self.progress_started = now
            self.transferred[key] = max(self.transferred.get(key, 0), min(current, self.files[key][1]['size']))
            done = sum(self.transferred.values())
            total = sum(file[1]['size'] for file in self.files.values())
            elapsed = now - self.progress_started
            payload = dict(name=self.files[key][1]['name'], current=done, total=total,
                           speed=done / elapsed if elapsed >= 0.25 else 0)
        self.progress(**payload)

    def _file_verify(self, _, user, task, file_id, md5):
        key = self._arg(file_id)
        file = self.files.get(key)
        if not self._session(user, task) or not file or self._arg(md5).lower() != file[1]['md5'].encode():
            return False
        with self.lock:
            self.verified.add(key)
            started = self.download_started.pop(key, None)
        if started is not None:
            seconds = max(time.monotonic() - started, 0.001)
            size = file[1]['size']
            self.log(f'Download to verification: {size} bytes in {seconds:.3f} s, {size / seconds / 1e6:.2f} MB/s')
        self.log(f'Phone verified MD5 for {file[1]["name"]}')
        self._emit_progress(key, file[1]['size'])
        return True

    def _task_complete(self, _, user, task, _reason, _length, _next_task, success):
        with self.lock:
            if self._session(user, task) and not self.complete.is_set() and not self.failed.is_set():
                if C.c_bool.from_address(success).value and self.verified == self.files.keys():
                    self.complete.set()
                else:
                    self.failed.set()
        return 0

    def __enter__(self):
        self.start(self.port, C.byref(self.delegate), True)
        return self

    def __exit__(self, *_):
        self.stop(self.port)
