"""Push the group's P2P info into the phone's Xiaomi Share GATT server."""

import asyncio
import base64
import json
import subprocess
import time
import uuid

ADVERT_UUID = uuid.UUID('00003331-0000-1000-8000-008123456789')
SERVICE_UUID = uuid.UUID('00009955-0000-1000-8000-00805f9b34fb')
STATUS_UUID = uuid.UUID('00009954-0000-1000-8000-00805f9b34fb')
INFO_UUID = uuid.UUID('00009953-0000-1000-8000-00805f9b34fb')
IV = b'0102030405060708'


def wifi_direct_mac():
    """The group interface address the phone associates with (locally administered)."""
    command = (
        "Get-NetAdapter | Where-Object { $_.InterfaceDescription -like '*Wi-Fi Direct*' "
        "-and $_.Status -eq 'Up' } | Select-Object -First 1 -ExpandProperty MacAddress"
    )
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
        capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    mac = result.stdout.strip().lower().replace('-', ':')
    return mac or None


def encrypt_fields(public_key_b64, values):
    """Encrypt strings with an ephemeral ECDH key, as BleSecurity does."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    peer = serialization.load_der_public_key(base64.b64decode(public_key_b64))
    ephemeral = ec.generate_private_key(ec.SECP256R1())
    shared = ephemeral.exchange(ec.ECDH(), peer)
    fields = {}
    for name, text in values.items():
        encryptor = Cipher(algorithms.AES(shared), modes.CTR(IV)).encryptor()
        fields[name] = base64.b64encode(
            encryptor.update(text.encode()) + encryptor.finalize()).decode()
    public = ephemeral.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return fields, base64.b64encode(public).decode()


def build_p2p_info(device_id, mac, ssid, psk, port, public_key_b64, freq=0):
    fields, ephemeral = encrypt_fields(
        public_key_b64, {'ssid': ssid, 'psk': psk, 'mac': mac})
    return json.dumps({
        'id': f'{device_id:04x}',
        'mac': fields['mac'],
        'port': port,
        'freq': freq,
        'ssid': fields['ssid'],
        'psk': fields['psk'],
        'key': ephemeral,
        'transfer_mode': 0,
    }, separators=(',', ':')).encode('utf-8')


async def _push(address, address_type, device_id, mac, ssid, psk, port, log, timeout=20):
    from winrt.windows.devices.bluetooth import BluetoothAddressType, BluetoothLEDevice
    from winrt.windows.devices.bluetooth.genericattributeprofile import GattCommunicationStatus
    from winrt.windows.storage.streams import DataReader

    device = await asyncio.wait_for(
        BluetoothLEDevice.from_bluetooth_address_async(
            address, BluetoothAddressType(address_type)), timeout)
    if device is None:
        raise ConnectionError('Windows could not open the BLE device')
    try:
        services = await asyncio.wait_for(
            device.get_gatt_services_for_uuid_async(SERVICE_UUID), timeout)
        if services.status != GattCommunicationStatus.SUCCESS or not services.services:
            raise ConnectionError(f'GATT service lookup: {services.status}')
        service = services.services[0]
        status_chars = await asyncio.wait_for(
            service.get_characteristics_for_uuid_async(STATUS_UUID), timeout)
        if (status_chars.status != GattCommunicationStatus.SUCCESS
                or not status_chars.characteristics):
            raise ConnectionError(f'GATT status characteristic: {status_chars.status}')
        read = await asyncio.wait_for(status_chars.characteristics[0].read_value_async(), timeout)
        if read.status != GattCommunicationStatus.SUCCESS:
            raise ConnectionError(f'GATT status read: {read.status}')
        raw = bytearray(read.value.length)
        DataReader.from_buffer(read.value).read_bytes(raw)
        status = json.loads(raw.decode('utf-8'))
        public_key = status.get('key')
        if not public_key:
            raise ConnectionError('Phone did not publish a public key')
        payload = build_p2p_info(device_id, mac, ssid, psk, port, public_key)
        info_chars = await asyncio.wait_for(
            service.get_characteristics_for_uuid_async(INFO_UUID), timeout)
        if info_chars.status != GattCommunicationStatus.SUCCESS or not info_chars.characteristics:
            raise ConnectionError(f'GATT info characteristic: {info_chars.status}')
        result = await asyncio.wait_for(
            info_chars.characteristics[0].write_value_with_result_async(payload), timeout)
        if result.status != GattCommunicationStatus.SUCCESS:
            raise ConnectionError(f'GATT info write: {result.status}')
        log(f'Pushed P2P info to phone GATT ({len(payload)} bytes)')
    finally:
        device.close()


def push_group_info(address, address_type, device_id, ssid, psk, port, log, *, cancel=None):
    """Synchronous one-shot exchange used before the offer is advertised."""
    async def runner():
        mac = wifi_direct_mac()
        if not mac:
            raise ConnectionError('No Wi-Fi Direct interface found for the group')
        log(f'GATT push: phone={address:#x}, type={address_type}, group mac={mac}')
        task = asyncio.create_task(_push(address, address_type, device_id, mac, ssid, psk, port, log))
        while cancel is not None and not task.done():
            if cancel.is_set():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                return
            await asyncio.wait({task}, timeout=0.1)
        await task

    try:
        asyncio.run(runner())
        return True
    except Exception as error:
        log(f'GATT push failed: {error}')
        return False


def push_info(address, address_type, device_id, ssid, psk, port, log,
              pause=None, resume=None, finish=None, delay=5.0):
    """Run the GATT exchange on a worker thread.

    Windows cannot start a GATT connection while the same adapter is
    advertising, so the caller pauses its publisher for the exchange.
    """
    time.sleep(delay)

    async def runner():
        mac = wifi_direct_mac()
        if not mac:
            raise ConnectionError('No Wi-Fi Direct interface found for the group')
        log(f'GATT push: phone={address:#x}, type={address_type}, group mac={mac}')
        await _push(address, address_type, device_id, mac, ssid, psk, port, log)

    if pause:
        pause()
    try:
        asyncio.run(runner())
    except Exception as error:
        log(f'GATT push failed: {error}')
    finally:
        if resume:
            resume()
        if finish:
            try:
                finish()
            except Exception as error:
                log(f'GATT publish failed: {error}')
