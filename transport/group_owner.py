import queue
import secrets
import time
from contextlib import contextmanager


def group_response(device_id, sender_id, task_id, nonce, channel=0):
    fields = b"".join(value.to_bytes(2, "little") for value in (sender_id, task_id, nonce))
    return (bytes.fromhex("101100111224") + device_id.to_bytes(2, "little")
            + bytes.fromhex("8823") + fields + bytes([channel]))


def server_ready_ip(packet, device_id, task_id):
    if (len(packet) != 16 or packet[:4] != bytes.fromhex("0f110011")
            or packet[4] not in {2, 18} or packet[5] != 0x24
            or packet[8:10] != bytes.fromhex("7804")
            or int.from_bytes(packet[10:12], "little") != device_id
            or int.from_bytes(packet[12:14], "little") != task_id):
        return None
    return f"192.168.{packet[14]}.{packet[15]}"


def xiaomi_group_information_element():
    """Advertise the group capability used by Xiaomi's PC receiver."""
    from winrt.windows.devices.wifidirect import WiFiDirectInformationElement
    from winrt.windows.storage.streams import DataWriter

    oui, payload = DataWriter(), DataWriter()
    oui.write_bytes(bytes.fromhex("80ad16"))
    payload.write_bytes(bytes.fromhex("0100000030"))
    element = WiFiDirectInformationElement()
    element.oui = oui.detach_buffer()
    element.oui_type = 1
    element.value = payload.detach_buffer()
    return element


@contextmanager
def publish_group(device_id, derive_psk, log):
    from winrt.windows.devices import enumeration
    from winrt.windows.devices.wifidirect import (
        WiFiDirectAdvertisementListenStateDiscoverability, WiFiDirectAdvertisementPublisher,
        WiFiDirectConfigurationMethod, WiFiDirectConnectionListener,
    )
    from winrt.windows.security.credentials import PasswordCredential

    nonce = secrets.randbelow(65536)
    ssid = f"DIRECT-{device_id:04x}"
    publisher = WiFiDirectAdvertisementPublisher()
    publisher.advertisement.is_autonomous_group_owner_enabled = True
    publisher.advertisement.listen_state_discoverability = WiFiDirectAdvertisementListenStateDiscoverability.INTENSIVE
    publisher.advertisement.supported_configuration_methods.append(WiFiDirectConfigurationMethod.PUSH_BUTTON)
    publisher.advertisement.information_elements.append(xiaomi_group_information_element())
    legacy = publisher.advertisement.legacy_settings
    legacy.ssid = ssid
    legacy.passphrase = PasswordCredential("MiShare", "receiver", derive_psk(device_id, b"", nonce))
    legacy.is_enabled = True
    listener = WiFiDirectConnectionListener()

    requests = queue.Queue()

    def incoming(_, args):
        request = args.get_connection_request()
        peer = request.device_information
        log(f"Native Wi-Fi Direct request: {peer.name!r}, paired={peer.pairing.is_paired}")
        requests.put(request)

    token = listener.add_connection_requested(incoming)
    try:
        publisher.start()
        deadline = time.monotonic() + 4
        while publisher.status.name not in {"STARTED", "ABORTED"} and time.monotonic() < deadline:
            time.sleep(0.1)
        if publisher.status.name != "STARTED":
            raise ConnectionError(f"Windows Wi-Fi Direct group status: {publisher.status.name}")
        yield ssid, nonce
    finally:
        publisher.stop()
        listener.remove_connection_requested(token)
        while not requests.empty():
            requests.get_nowait().close()
