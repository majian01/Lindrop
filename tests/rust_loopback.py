import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
bundle = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / 'dist' / 'Lindrop-win-x64' / 'app'
sys.path.insert(0, str(root / 'transport'))
from official_server import OfficialServer

scratch = root / 'work' / 'loopback'
scratch.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=scratch) as directory:
    base = Path(directory)
    data = b'MiShare protocol\x00' * 100_000
    source = base / 'source'
    source.write_bytes(data)
    for good in (True, False):
        dest = base / ('good' if good else 'bad')
        dest.mkdir()
        name = '往返验证.bin'
        digest = hashlib.md5(data, usedforsecurity=False).hexdigest()
        files = {'456_1': (source, dict(id='456_1', name=name, size=len(data),
                  mimeType='application/octet-stream', md5=digest if good else '0' * 32))}
        if good:
            (dest / f'01c8_{name}').write_text('existing', encoding='utf-8')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with OfficialServer(bundle / 'XiaomiRuntime', port, 456, 123, files, lambda _: None) as server:
            result = subprocess.run([str(bundle / 'lindrop-transfer.exe')], capture_output=True, text=True, encoding='utf-8', timeout=18,
                input=json.dumps(dict(target='127.0.0.1', source='127.0.0.1', port=port, user='123',
                                      task=456, folder=str(dest))) + '\n')
            events = [json.loads(line) for line in result.stdout.splitlines()]
            if good:
                assert result.returncode == 0, (events, result.stderr)
                saved = Path(events[-1]['files'][0])
                assert saved.name == f'01c8_1_{name}' and saved.read_bytes() == data
                assert (dest / f'01c8_{name}').read_text(encoding='utf-8') == 'existing'
                assert server.complete.is_set(), events
            else:
                assert result.returncode == 1 and events[-1]['text'] == 'File checksum mismatch', (events, result.stderr)
                assert not any(dest.iterdir()) and not server.complete.is_set()
    print('PASS Rust HTTPS transfer against Xiaomi DLL, source binding, non-ASCII filename, collision, checksum rejection, completion.')
