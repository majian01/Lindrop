# Adapted from Tandem's tandem/mtapc.py, revision
# 89274a33d0ef4a8ae9a68e5767c221883ed5e907.
# Copyright (c) 2026 Tandem contributors. MIT; see LICENSE.
# Modifications: Lindrop contributors; attribution is in README.md.
"""MiShare PC-mode (mfr-911 BLE) receiver — phone→PC transfers without GATT.

Reverse engineered from com.miui.mishare.connectivity (HyperOS 3).

Wire format (manufacturer data, company id 911 / 0x038F):
  ability packet (we advertise, subtype 0):
    [0]    total_len = 12 + nameLen
    [1,3]  0x11 0x00 0x11 magic
    [4]    version: 0x12 (or 0x02 when the "short" variant is used)
    [5]    (nameLen<<4)|3  -> TLV type-3 = device name (<=12 bytes utf-8)
    [..]   0x24           -> TLV type-4 len2 = device id u16 LE
    [..]   0x38           -> TLV type-8 len3 = app0 {caps0, caps1, devCode}
  transfer offer (phone advertises, app0 subtype 1):
    type-8 TLV whose first byte &7 == 1:
      [flags, fileCount, channel, toDevId(2B), taskId(2B), nonce(2B), name(6B)]
      flags bit4 = support2_0 (DIRECT- ssid) ; trailing [0x2F, ip2, ip3]
  credentials:
    ssid = "ap_mishare_%04x" % senderDevId   (2.0 -> "DIRECT-%04x")
    psk  = b64(AES-CBC(key,iv).encrypt([devIdLE,nonceLE,name4B]))[:8]

Then the phone hosts https://192.168.<ip2>.<ip3>:9999/api/1.0/* (NanoHTTPD,
self-signed) — fileinfo / file?id= / report / taskcomplete / fileverify.

Windows-only implementation.
"""

import base64
import ipaddress
import json
import os
import random
import re
import ssl
import subprocess
import threading
import time
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from Crypto.Cipher import AES  # pycryptodome already in deps (certs.py)

MFR_ID = 911
_PSK_KEY = bytes.fromhex("15c8761d9e36456b9af35e1d084665a5")
_PSK_IV = bytes.fromhex("02050b11171f292f3b43495361676d7f")


def derive_psk(dev_id: int, name: bytes, nonce: int) -> str:
    blk = bytes([dev_id & 0xFF, (dev_id >> 8) & 0xFF,
                 nonce & 0xFF, (nonce >> 8) & 0xFF]) + name[:4].ljust(4, b"\x00")
    # Android uses AES/CBC/PKCS5Padding -> pad the 8-byte block to 16
    blk += b"\x08" * 8
    ct = AES.new(_PSK_KEY, AES.MODE_CBC, _PSK_IV).encrypt(blk)
    return base64.b64encode(ct).decode()[:8]


def build_ability(name: str, dev_id: int, caps0: int = 0x40,
                  caps1: int = 0x0C, dev_code: int = 0x30) -> bytes:
    """d.a() — PC ability packet (subtype 0).

    Verified on-device: the phone parsed TLV 0x24 correctly (devId read back
    verbatim), so the canonical builder layout is the right one — no padding.
    caps0 bit3 (a) must be 0 — p079n1.b.n() drops the device otherwise.
    caps0 bit6 (d)=1 & b=c=0 -> phone marks us i6=0 (idle/available).
    caps1 bit4 (support2_0) stays 0 -> phone takes the legacy soft-AP path.
    """
    nb = name.encode("utf-8")[:12]
    d = bytearray()
    d.append(len(nb) + 12)
    d += b"\x11\x00\x11\x12"
    d.append((len(nb) << 4) | 3)
    d += nb
    d.append(0x24)
    d += bytes([dev_id & 0xFF, (dev_id >> 8) & 0xFF])
    d.append(0x38)
    d += bytes([caps0, caps1, dev_code])
    return bytes(d)


def build_response(our_dev_id: int, sender_dev_id: int, task_id: int,
                   accept: bool = True) -> bytes:
    """d.c() — subtype-3 response to a transfer offer.

    Packet: {0d,11,00,11,12, 24,<devLE>, 78, (accept?0x13:0x23),
             <senderLE>, <taskLE>}.  The sender scans with filter
    m(senderDevId, taskId) + mask n(): requires [9]&7==3, [10-11]=its own
    devId, [12-13]=taskId.  action byte &248: 16=accept, 32=refuse.
    """
    return bytes([
        0x0D, 0x11, 0x00, 0x11, 0x12, 0x24,
        our_dev_id & 0xFF, (our_dev_id >> 8) & 0xFF,
        0x78, (0x23 if not accept else 0x13),
        sender_dev_id & 0xFF, (sender_dev_id >> 8) & 0xFF,
        task_id & 0xFF, (task_id >> 8) & 0xFF,
    ])


def parse_tlv(payload: bytes):
    """Walk the TLV stream of a mfr-911 payload; return dict of finds."""
    out = {"name": None, "dev_id": None, "app0": None,
           "app15": None, "ok": False}
    if len(payload) < 6 or payload[1] != 0x11 or payload[3] != 0x11:
        return out
    if payload[4] not in (0x12, 0x02):
        return out
    out["ok"] = True
    i = 5
    guard = 0
    while i < len(payload) and guard < 10:
        guard += 1
        tag = payload[i]
        t = tag & 15
        if t == 0 or t == 1 or t == 2:
            i += 1
        elif t == 3:
            ln = (tag >> 4) & 15
            if i + 1 + ln <= len(payload):
                out["name"] = payload[i + 1:i + 1 + ln]
                i += 1 + ln
            else:
                break
        elif t == 4:
            if i + 2 < len(payload):
                out["dev_id"] = payload[i + 1] | (payload[i + 2] << 8)
            i += 3
        elif t == 8:
            ln = (tag >> 4) & 15
            if i + 1 + ln <= len(payload):
                out["app0"] = payload[i + 1:i + 1 + ln]
                i += 1 + ln
            else:
                break
        elif t == 15:
            ln = (tag >> 4) & 15
            if i + 1 + ln <= len(payload):
                out["app15"] = payload[i + 1:i + 1 + ln]
                i += 1 + ln
            else:
                break
        else:
            break
    return out


def parse_offer(payload: bytes):
    """Parse subtype-1 transfer packet -> dict or None."""
    tlv = parse_tlv(payload)
    app0 = tlv.get("app0")
    if not app0 or (app0[0] & 7) != 1 or len(app0) < 9:
        return None
    flags = app0[0]
    o = {
        "sender_dev_id": tlv["dev_id"],
        "flags": flags,
        "support2_0": bool(flags & 16),
        "file_count": app0[1],
        "channel": app0[2],
        "to_dev_id": app0[3] | (app0[4] << 8),
        "task_id": app0[5] | (app0[6] << 8),
        "nonce": app0[7] | (app0[8] << 8),
        "name": app0[9:].decode("utf-8", "replace"),
        "ip": None,
    }
    # type-15 TLV = server ip tail: 192.168.<a>.<b>
    tail = tlv.get("app15")
    if tail and len(tail) == 2:
        o["ip"] = f"192.168.{tail[0]}.{tail[1]}"
    return o


def _netsh(*args) -> str:
    return subprocess.run(["netsh"] + list(args), capture_output=True,
                          timeout=20, creationflags=subprocess.CREATE_NO_WINDOW).stdout.decode("gbk", "replace")


def current_ssid() -> str:
    """SSID we are currently associated with ("" if disconnected)."""
    m = re.search(r"(?m)^\s*SSID[^\S\r\n]*:[^\S\r\n]*(.+)",
                  _netsh("wlan", "show", "interfaces"))
    return m.group(1).strip() if m else ""


def _wlan_profile(ssid: str, psk: str, path: str) -> str:
    xml = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{ssid}</name>
  <SSIDConfig><SSID><name>{ssid}</name></SSID><nonBroadcast>true</nonBroadcast></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM>
    <security>
      <authEncryption>
        <authentication>WPA2PSK</authentication>
        <encryption>AES</encryption>
        <useOneX>false</useOneX>
      </authEncryption>
      <sharedKey>
        <keyType>passPhrase</keyType>
        <protected>false</protected>
        <keyMaterial>{psk}</keyMaterial>
      </sharedKey>
    </security>
  </MSM>
</WLANProfile>"""
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(xml)
    return path


def hotspot_interface(wlan_output, ssid):
    interfaces = []
    fields = None
    for line in wlan_output.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key, value = key.strip().casefold(), value.strip()
        if key in {"name", "名称"}:
            fields = {"name": value}
            interfaces.append(fields)
        elif fields is not None:
            fields[key] = value

    for fields in interfaces:
        state = fields.get("state", fields.get("状态", "")).casefold()
        profile = fields.get("profile", fields.get("配置文件"))
        if state in {"connected", "已连接"} and (fields.get("ssid") or profile) == ssid:
            return fields["name"]
    return None


def hotspot_source(addresses, phone_ip):
    target = ipaddress.IPv4Address(phone_ip)
    for row in addresses:
        if row["AddressState"] != 4:
            continue
        interface = ipaddress.IPv4Interface(f"{row['IPAddress']}/{row['PrefixLength']}")
        if target in interface.network and target != interface.ip:
            return str(interface.ip)
    return None


def wlan_addresses(interface, include_gateway=False):
    command = (
        "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | "
        "Where-Object InterfaceAlias -eq $env:MISHARE_WLAN_ALIAS | "
        "Select-Object IPAddress, PrefixLength, @{n='AddressState';e={[int]$_.AddressState}} | "
        "ConvertTo-Json -Compress"
    )
    if include_gateway:
        command = (
            "$gateway = (Get-NetIPConfiguration -InterfaceAlias $env:MISHARE_WLAN_ALIAS "
            "-ErrorAction SilentlyContinue).IPv4DefaultGateway.NextHop | Select-Object -First 1; "
            + command.replace("PrefixLength,", "PrefixLength, @{n='Gateway';e={$gateway}},")
        )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        env={**os.environ, "MISHARE_WLAN_ALIAS": interface},
        capture_output=True, timeout=6, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError(f"Could not read IPv4 configuration for {interface}")
    rows = json.loads(result.stdout or b"[]")
    return rows if isinstance(rows, list) else [rows]


def recent_hotspot_interface(ssid, started):
    # Use structured WLAN events when netsh only prints a partial interface.
    command = r"""
    $since = [DateTimeOffset]::FromUnixTimeMilliseconds([long]$env:MISHARE_STARTED).LocalDateTime
    $events = Get-WinEvent -FilterHashtable @{
        LogName='Microsoft-Windows-WLAN-AutoConfig/Operational'
        Id=8001,8003
        StartTime=$since
    } -ErrorAction SilentlyContinue
    foreach ($event in $events) {
        $data = ([xml]$event.ToXml()).Event.EventData.Data
        $ssid = ($data | Where-Object Name -eq SSID).'#text'
        if ($ssid -ne $env:MISHARE_SSID) { continue }
        if ($event.Id -eq 8003) { break }
        $guid = ($data | Where-Object Name -eq InterfaceGuid).'#text'
        Get-NetAdapter | Where-Object { $_.InterfaceGuid -eq $guid -and $_.Status -eq 'Up' } |
            Select-Object -ExpandProperty Name -First 1
        break
    }
    """
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        env={**os.environ, "MISHARE_SSID": ssid, "MISHARE_STARTED": str(int(started * 1000))},
        capture_output=True, timeout=6, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.stdout.decode("gbk", "replace").strip() or None


def hotspot_endpoint(addresses, advertised_ip):
    for row in addresses:
        gateway = row.get("Gateway")
        if gateway and (source := hotspot_source([row], gateway)):
            return source, gateway
    if advertised_ip and (source := hotspot_source(addresses, advertised_ip)):
        return source, advertised_ip
    return None


def wait_for_hotspot_ip(interface, phone_ip, log, timeout=22):
    log(f"Wi-Fi associated on {interface}. Waiting for hotspot IPv4 (BLE address: {phone_ip}).")
    deadline = time.monotonic() + timeout
    addresses = []
    while time.monotonic() < deadline:
        addresses = wlan_addresses(interface, include_gateway=True)
        endpoint = hotspot_endpoint(addresses, phone_ip)
        if endpoint:
            source, target = endpoint
            log(f"Hotspot IPv4 ready: {source} -> {target}")
            return endpoint
        time.sleep(0.5)
    raise TimeoutError(f"No usable hotspot IPv4 for {phone_ip}; adapter addresses: {addresses}")


def join_phone_ap(offer: dict, log=print, timeout: float = 30.0) -> tuple[str, str]:
    sid = offer["sender_dev_id"]
    if sid is None:
        raise RuntimeError("offer missing sender device id")
    ssid = ("DIRECT-%04x" if offer["support2_0"] else "ap_mishare_%04x") % sid
    name_b = offer["name"].encode("utf-8")
    psk = derive_psk(sid, b"" if offer["support2_0"] else name_b, offer["nonce"])
    log(f"Connecting to temporary Wi-Fi: {ssid}")
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
        path = f.name
    _wlan_profile(ssid, psk, path)
    _netsh("wlan", "add", "profile", f"filename={path}", "user=current")
    try:
        os.unlink(path)
    except OSError:
        pass
    started = time.time()
    _netsh("wlan", "connect", f"name={ssid}")
    deadline = time.monotonic() + timeout
    out = ""
    while time.monotonic() < deadline:
        out = _netsh("wlan", "show", "interfaces")
        interface = hotspot_interface(out, ssid) or recent_hotspot_interface(ssid, started)
        if interface:
            return ssid, interface
        time.sleep(0.5)

    status = [
        line.strip() for line in out.splitlines()
        if line.partition(":")[0].strip().casefold()
        in {"name", "名称", "state", "状态", "ssid", "profile", "配置文件"}
    ]
    raise TimeoutError(f"Could not confirm connection to {ssid}. WLAN status: {status or out.strip()}")


def _safe_name(name: str) -> str:
    """Strip characters illegal in Windows paths."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "file"


def is_addressed_offer(offer, device_id):
    return offer["sender_dev_id"] is not None and offer["to_dev_id"] == device_id


def check_integrity(received, expected, digest, expected_digest):
    if expected is not None and received != expected:
        raise ValueError(f"Size mismatch: received {received}, expected {expected}")
    if expected_digest and digest.lower() != expected_digest.lower():
        raise ValueError("File checksum mismatch")


def pull_files(offer: dict, out_dir: Path, log=print,
               user: str = None, source_ip: str = None) -> list:
    """HTTPS pull from the phone's NanoHTTPD :9999.

    Mirrors the real receiver (pc/receive/d.java): fileinfo -> per-file
    download with progress reports -> fileverify(md5) -> taskcomplete.
    The taskcomplete response may carry "nexttaskid" for a queued batch —
    loop until the phone has nothing left.
    """
    import hashlib
    import http.client
    from functools import partial
    import urllib.request

    ip = offer["ip"] or "192.168.43.1"
    uid = user or str(offer["to_dev_id"])
    base = f"https://{ip}:9999/api/1.0"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    class HotspotHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, request):
            connect = partial(http.client.HTTPSConnection, source_address=(source_ip, 0))
            return self.do_open(connect, request, context=ctx)

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        HotspotHTTPSHandler(context=ctx),
    )

    def get(path, timeout=15):
        url = f"{base}/{path}"
        log(f"[mtapc] GET {url}")
        req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "Connection": "keep-alive"})
        return opener.open(req, timeout=timeout)

    def post(path, body: dict, timeout=15):
        url = f"{base}/{path}"
        if "report" not in path:  # progress posts every 2s would spam
            log(f"[mtapc] POST {url} {body}")
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json", "Connection": "keep-alive"})
        return opener.open(req, timeout=timeout)

    def report(task: str, data: dict):
        try:
            with post(f"report?user={uid}&task={task}",
                      {"status": 0, "data": data}, timeout=2):
                pass
        except Exception:
            pass  # progress reporting is best-effort

    saved = []
    # task id on the wire is the *decimal* string (p079n1.b.u = toString)
    task = str(offer["task_id"])
    visited_tasks = set()
    while task and task not in visited_tasks:
        visited_tasks.add(task)
        path = f"fileinfo?user={uid}&task={task}"
        for attempt in range(3):
            try:
                with get(path, timeout=4) as response:
                    info = json.load(response)
                break
            except (TimeoutError, urllib.error.URLError) as error:
                if isinstance(error, urllib.error.HTTPError) or attempt == 2:
                    raise
                log(f"File service not reachable from {source_ip}: {error}; retrying.")
                time.sleep(0.5)
        files = info.get("data") or []
        if not files:
            break
        for fm in files:
            fid = str(fm.get("id", ""))
            name = _safe_name(fm.get("name") or "file")
            size = fm.get("size")
            total = int(size) if size is not None else None
            report(task, {"code": 3, "id": fid, "dstname": name,
                          "dstpath": ""})
            from urllib.parse import quote

            md5 = hashlib.md5(usedforsecurity=False)
            got, last_rep = 0, time.monotonic()
            temporary = out_dir / f".{uuid.uuid4().hex}.part"
            # Windows tempfile retries ACL errors as if they were name collisions.
            with temporary.open("xb"):
                pass
            try:
                download_start = time.monotonic()
                pending = None
                with ThreadPoolExecutor(max_workers=1) as progress:
                    with get(f"file?user={uid}&task={task}&id={quote(fid, safe='')}") as resp, temporary.open("wb") as fp:
                        while chunk := resp.read(65536):
                            fp.write(chunk)
                            md5.update(chunk)
                            got += len(chunk)
                            now = time.monotonic()
                            if now - last_rep > 2.0 and (pending is None or pending.done()):
                                last_rep = now
                                pending = progress.submit(report, task, {"code": 4, "id": fid, "cur": got,
                                                                         "total": total or 0, "dstname": name})
                    download_seconds = max(time.monotonic() - download_start, 0.001)
                digest = md5.hexdigest()
                check_integrity(got, total, digest, fm.get("md5"))
                log(f"Download: {got} bytes in {download_seconds:.3f} s, {got / download_seconds / 1e6:.2f} MB/s")
                out = out_dir / f"{offer['task_id']:04x}_{name}"
                suffix = 1
                while True:
                    try:
                        temporary.rename(out)
                        break
                    except FileExistsError:
                        out = out_dir / f"{offer['task_id']:04x}_{suffix}_{name}"
                        suffix += 1
            finally:
                temporary.unlink(missing_ok=True)
            log(f"Saved {out.name} ({got} bytes)")
            saved.append(out)
            try:
                post(f"fileverify?user={uid}&task={task}",
                     {"status": 0, "data": {
                         "code": 5, "id": fid, "md5": digest,
                         "dstname": name, "dstpath": ""}})
            except Exception as e5:
                log(f"[mtapc] fileverify err: {e5}")
        try:
            r = json.loads(post(f"taskcomplete?user={uid}&task={task}",
                                {"status": 0, "data": {}}).read().decode())
            nxt = (r.get("data") or {}).get("nexttaskid")
            if nxt:
                log(f"[mtapc] next task queued: {nxt}")
                task = str(int(nxt))
                continue
        except Exception as e5:
            log(f"[mtapc] taskcomplete err: {e5}")
        break
    return saved


def wait_for_publisher(publisher, timeout=2):
    """Advance on actual radio readiness rather than a fixed startup sleep."""
    deadline = time.monotonic() + timeout
    while True:
        state = publisher.status.name
        if state == 'STARTED':
            return
        if state == 'ABORTED' or time.monotonic() >= deadline:
            raise ConnectionError(f'BLE publisher failed to start: {state}')
        time.sleep(0.02)


class MtaPcReceiver:
    """Ability advert + offer watcher + transfer runner."""

    pull = staticmethod(pull_files)

    def transfer_cancelled(self):
        return self._stopping.is_set()

    def __init__(self, name: str, out_dir: Path, log=print, wifi_direct=False, group_owner=False,
                 decide=None, completed=None, failed=None):
        self.group_owner = group_owner
        self.wifi_direct = wifi_direct or group_owner
        self.name = name[:12]
        self.out_dir = Path(out_dir)
        # stable per machine so the phone sees a consistent identity
        self.dev_id = 0x4000 | (zlib.crc32(name.encode()) & 0x3FFF)
        self.log = log
        self.decide = decide or self._console_decision
        self.completed = completed
        self.failed = failed
        self._pub = None
        self._watch = None
        self._seen = {}  # (task, nonce) -> first-seen timestamp
        self._busy = threading.Lock()
        self._stopping = threading.Event()
        self._transferring = threading.Event()
        self._go_ready = None

    # ---------------- advertising ----------------
    def _set_payload(self, data: bytes):
        """Swap the manufacturer payload of the running publisher."""
        from winrt.windows.devices.bluetooth.advertisement import (
            BluetoothLEManufacturerData)
        from winrt.windows.storage.streams import DataWriter
        self._pub.stop()
        deadline = time.monotonic() + 2
        while self._pub.status.name not in {"STOPPED", "CREATED", "ABORTED"}:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"BLE publisher did not stop: {self._pub.status.name}")
            time.sleep(0.05)
        adv = self._pub.advertisement
        adv.manufacturer_data.clear()
        md = BluetoothLEManufacturerData()
        md.company_id = MFR_ID
        w = DataWriter()
        w.write_bytes(data)
        md.data = w.detach_buffer()
        adv.manufacturer_data.append(md)
        self._pub.start()
        deadline = time.monotonic() + 2
        while self._pub.status.name != "STARTED":
            if self._pub.status.name == "ABORTED" or time.monotonic() >= deadline:
                raise ConnectionError(f"BLE publisher failed to start: {self._pub.status.name}")
            time.sleep(0.05)

    def start(self, watch=True, handle_offers=True):
        from winrt.windows.devices.bluetooth.advertisement import (
            BluetoothLEAdvertisement,
            BluetoothLEManufacturerData,
            BluetoothLEAdvertisementPublisher,
            BluetoothLEAdvertisementWatcher,
        )
        from winrt.windows.storage.streams import DataWriter

        self._ability = build_ability(self.name, self.dev_id, caps1=0x1C if self.wifi_direct else 0x0C)
        adv = BluetoothLEAdvertisement()
        md = BluetoothLEManufacturerData()
        md.company_id = MFR_ID
        w = DataWriter()
        w.write_bytes(self._ability)
        md.data = w.detach_buffer()
        adv.manufacturer_data.append(md)
        self._pub = BluetoothLEAdvertisementPublisher(adv)
        self._pub.start()
        wait_for_publisher(self._pub)
        self._ability_since = time.monotonic()
        self.log(f"[mtapc] ability advert status={self._pub.status} "
                 f"devId={self.dev_id:04x} name={self.name} pkt="
                 f"{self._ability.hex()}")

        if watch:
            w2 = BluetoothLEAdvertisementWatcher()
            w2.add_received(self._on_adv if handle_offers else lambda *_: None)
            w2.start()
            self._watch = w2
            self.log("[mtapc] watching for transfer offers")

    def wait_for_discovery(self, minimum=0.5):
        # Preserve the native phone's initial discovery opportunity while P2P
        # creation and HTTPS startup run during the same advertising interval.
        deadline = self._ability_since + minimum
        while (remaining := deadline - time.monotonic()) > 0:
            if self.transfer_cancelled():
                raise InterruptedError('Transfer stopped')
            time.sleep(min(remaining, 0.05))

    def stop(self):
        self._stopping.set()
        for o in (self._pub, self._watch):
            try:
                o and o.stop()
            except Exception:
                pass

    # ---------------- scan ----------------
    def _on_adv(self, _w, args):
        try:
            adv = args.advertisement
            sections = getattr(adv, "manufacturer_data_sections", None)
            if sections is None:
                sections = getattr(adv, "manufacturer_data", [])
            for sec in sections:
                if sec.company_id != MFR_ID:
                    continue
                payload = bytes(memoryview(sec.data))
                ready = self._go_ready
                if ready is not None:
                    from group_owner import server_ready_ip
                    task, ready_event, targets = ready
                    address = server_ready_ip(payload, self.dev_id, task)
                    if address:
                        targets.append(address)
                        ready_event.set()
                        continue
                offer = parse_offer(payload)
                if self._stopping.is_set() or not offer or not is_addressed_offer(offer, self.dev_id):
                    continue
                key = (offer["task_id"], offer["nonce"])
                now = time.time()
                self._seen = {k: t for k, t in self._seen.items()
                              if now - t < 120}
                if key in self._seen:
                    continue
                self._seen[key] = now
                threading.Thread(target=self._handle, args=(offer,),
                                 daemon=True).start()
        except Exception as e5:
            self.log(f"[mtapc] scan err {e5}")

    def _respond_thread(self, offer: dict, seconds: float = 12.0):
        """Swap the publisher to the subtype-3 accept packet for a while."""
        try:
            resp = build_response(self.dev_id, offer["sender_dev_id"],
                                  offer["task_id"], accept=True)
            self._set_payload(resp)
            self.log(f"[mtapc] advertising ACCEPT resp {resp.hex()}")
            time.sleep(seconds)
        except Exception as e5:
            self.log(f"[mtapc] resp err {e5}")
        finally:
            try:
                if not self._stopping.is_set():
                    self._set_payload(self._ability)
            except Exception as e5:
                self.log(f"[mtapc] restore err {e5}")

    @staticmethod
    def _console_decision(offer):
        sender = repr(offer['name'] or 'Xiaomi')
        answer = input(f"Request from {sender}, {offer['file_count']} file(s). Accept? [y/N] ")
        return answer.strip().lower() == 'y'

    def _handle(self, offer: dict):
        if not self._busy.acquire(blocking=False):
            return
        attempted = False
        joined = False
        home_ssid = None
        try:
            if not self.decide(offer) or self.transfer_cancelled():
                return
            self._transferring.set()
            if self.group_owner:
                files = self._receive_as_group_owner(offer)
                if files and self.completed:
                    self.completed(offer, files)
                return
            if self.wifi_direct:
                self._receive_direct(offer)
                return
            attempted = True
            # remember the network we are about to leave so we can come back
            home_ssid = current_ssid() or None
            # 1) tell the phone we accept (it scans ~30s for this)
            threading.Thread(target=self._respond_thread, args=(offer,),
                             daemon=True).start()
            # 2) join the phone's AP and pull the files over HTTPS
            ssid, interface = join_phone_ap(offer, self.log)
            joined = True
            phone_ip = offer["ip"] or "192.168.43.1"
            source_ip, target_ip = wait_for_hotspot_ip(interface, phone_ip, self.log)
            files = self.pull({**offer, "ip": target_ip}, self.out_dir, self.log,
                               user=str(self.dev_id), source_ip=source_ip)
            self.log(f"[mtapc] done: {[f.name for f in files]}")
        except Exception as e5:
            self.log(f"[mtapc] transfer failed: {e5}")
            if self.failed:
                self.failed(e5)
        finally:
            self._restore_network(offer, attempted, joined, home_ssid)

    def _receive_as_group_owner(self, offer):
        from group_owner import group_response, publish_group

        if not offer["support2_0"]:
            raise ConnectionError("The phone did not request the P2P protocol")
        original = current_ssid()
        ready_event = threading.Event()
        targets = []
        self._go_ready = (offer["task_id"], ready_event, targets)
        try:
            with publish_group(self.dev_id, derive_psk, self.log) as (ssid, nonce):
                if self.transfer_cancelled():
                    return []
                started = time.time()
                payload = group_response(self.dev_id, offer["sender_dev_id"], offer["task_id"], nonce)
                self._set_payload(payload)
                self.log(f"P2P group {ssid} started; group parameters advertised; router {current_ssid()!r}")
                deadline = time.monotonic() + 26
                while not ready_event.wait(0.25):
                    if self.transfer_cancelled():
                        return []
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Phone did not broadcast file service readiness")
                target = targets[-1]
                self.log(f"P2P file service ready: {target}")
                interface = recent_hotspot_interface(ssid, started - 5)
                if not interface:
                    raise ConnectionError(f"No Windows interface found for P2P group {ssid}")
                source = hotspot_source(wlan_addresses(interface), target)
                if not source:
                    raise ConnectionError(f"No direct IPv4 route from {interface} to {target}")
                if self.transfer_cancelled():
                    return []
                self.log(f"P2P file link: {source} -> {target}; router {current_ssid()!r}")
                files = self.pull({**offer, "ip": target}, self.out_dir, self.log,
                                   user=str(self.dev_id), source_ip=source)
                self.log(f"Wi-Fi Direct finished: {[file.name for file in files]}")
                return files
        finally:
            self._go_ready = None
            self.log(f"P2P group ended; router before {original!r}, now {current_ssid()!r}")
            if not self._stopping.is_set():
                self._set_payload(self._ability)

    def _receive_direct(self, offer):
        import asyncio
        from direct import receive

        if not offer["support2_0"]:
            raise ConnectionError("The phone offered a legacy hotspot instead of Wi-Fi Direct")
        original = current_ssid()
        threading.Thread(target=self._respond_thread, args=(offer,), daemon=True).start()

        def receive_files(source, target):
            self.log(f"Router before: {original!r}; during: {current_ssid()!r}")
            return self.pull({**offer, "ip": target}, self.out_dir, self.log,
                              user=str(self.dev_id), source_ip=source)

        expected = f"DIRECT-{offer['sender_dev_id']:04x}"
        self.log(f"Wi-Fi Direct offer: expected SSID {expected}, sender {offer['name']!r}, router {original!r}")
        try:
            files = asyncio.run(receive(offer, receive_files, self.log))
        finally:
            scan = _netsh("wlan", "show", "networks", "mode=bssid")
            names = re.findall(r"(?mi)^\s*SSID\s+\d+\s*:\s*(DIRECT-[^\r\n]+)", scan)
            self.log(f"Infrastructure scan: expected SSID visible={expected.casefold() in {name.strip().casefold() for name in names}}; DIRECT SSIDs={names!r}")
            self.log(f"Router after attempt: {current_ssid()!r}")
        self.log(f"Wi-Fi Direct finished: {[f.name for f in files]}")
        self.log(f"Router after: {current_ssid()!r}")

    def _restore_network(self, offer, attempted, joined, home_ssid):
        try:
            # 3) leave the temporary AP / come back home — always, even
            #    if the transfer blew up halfway
            if joined or (attempted and current_ssid().startswith(("ap_mishare_", "DIRECT-"))):
                if home_ssid and not home_ssid.startswith(("ap_mishare", "DIRECT-")):
                    self.log(f"[mtapc] rejoining {home_ssid}")
                    _netsh("wlan", "connect", f"name={home_ssid}")
                else:
                    _netsh("wlan", "disconnect")
            # drop the throwaway WLAN profile we added for the phone AP
            sid = offer.get("sender_dev_id")
            if attempted and sid is not None:
                _netsh("wlan", "delete", "profile",
                       "name=" + (("DIRECT-%04x" if offer["support2_0"]
                                   else "ap_mishare_%04x") % sid))
        finally:
            self._transferring.clear()
            self._busy.release()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="邻渡")
    ap.add_argument("--log", type=Path, help="Append receiver diagnostics to a file")
    ap.add_argument("--group-owner", action="store_true", help="Use the experimental MiShare group owner")
    ap.add_argument("--wifi-direct", action="store_true", help="Experimental native Wi-Fi Direct")
    ap.add_argument("--probe", action="store_true", help="Check BLE without accepting files")
    ap.add_argument("--out", default=str(
        Path.home() / "Downloads" / "Lindrop"))
    a = ap.parse_args()
    out = Path(a.out)
    if len(a.name.encode("utf-8")) > 12 or not a.name.strip():
        ap.error("--name must contain 1 to 12 UTF-8 bytes")
    if a.probe:
        from winrt.windows.devices.bluetooth.advertisement import (
            BluetoothLEAdvertisementPublisherStatus, BluetoothLEAdvertisementWatcherStatus)
        probe = MtaPcReceiver(a.name, out, wifi_direct=a.wifi_direct, group_owner=a.group_owner)
        try:
            probe.start(handle_offers=False)
            time.sleep(1.5)
            print(f"WinRT statuses: {probe._pub.status.name}, {probe._watch.status.name}")
            publisher_ok = probe._pub.status == BluetoothLEAdvertisementPublisherStatus.STARTED
            watcher_ok = probe._watch.status == BluetoothLEAdvertisementWatcherStatus.STARTED
            print(f"BLE publisher: {publisher_ok}, BLE scanner: {watcher_ok}")
            return 0 if publisher_ok and watcher_ok else 1
        finally:
            probe.stop()
    out.mkdir(parents=True, exist_ok=True)
    print(f"Inbox: {out}")
    def log(message):
        print(message, flush=True)
        if a.log:
            with a.log.open("a", encoding="utf-8") as stream:
                print(time.strftime("%H:%M:%S"), message, file=stream)

    r = MtaPcReceiver(a.name, out, log=log, wifi_direct=a.wifi_direct, group_owner=a.group_owner)
    print("[mtapc] running — send a file to this PC from 小米互传 share sheet")
    try:
        r.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        r.stop()
        if r._busy.locked() and r._transferring.is_set():
            print("Waiting for the current request to finish and Wi-Fi to be restored.")
            with r._busy:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
