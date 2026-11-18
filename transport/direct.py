import asyncio
import ipaddress

from winrt.windows import networking  # Registers HostName projection.
from winrt.windows.devices.enumeration import DeviceInformation, DeviceInformationKind
from winrt.windows.devices.wifidirect import WiFiDirectDevice, WiFiDirectDeviceSelectorType


def matching_peers(peers, offer):
    expected = f"DIRECT-{offer['sender_dev_id']:04x}".casefold()
    sender = offer["name"].strip().casefold()
    names = {expected, sender} - {""}
    return [peer for peer in peers if peer.name.strip().casefold() in names]


async def find_sender(offer, log, timeout=22):
    selector = WiFiDirectDevice.get_device_selector(
        WiFiDirectDeviceSelectorType.ASSOCIATION_ENDPOINT
    )
    watcher = DeviceInformation.create_watcher(
        selector, None, DeviceInformationKind.ASSOCIATION_ENDPOINT
    )
    loop = asyncio.get_running_loop()
    peers = {}
    changed = asyncio.Event()

    def added(peer):
        peers[peer.id] = peer
        changed.set()

    def updated(update):
        peer = peers.get(update.id)
        if peer is not None:
            peer.update(update)
        changed.set()

    def removed(update):
        peers.pop(update.id, None)
        changed.set()

    watcher.add_added(lambda _, peer: loop.call_soon_threadsafe(added, peer))
    watcher.add_updated(lambda _, update: loop.call_soon_threadsafe(updated, update))
    watcher.add_removed(lambda _, update: loop.call_soon_threadsafe(removed, update))
    watcher.add_enumeration_completed(lambda *_: loop.call_soon_threadsafe(changed.set))
    watcher.start()
    deadline = loop.time() + timeout
    try:
        while loop.time() < deadline:
            matches = matching_peers(peers.values(), offer)
            if len(matches) == 1:
                return matches[0]
            changed.clear()
            try:
                await asyncio.wait_for(changed.wait(), min(1, deadline - loop.time()))
            except TimeoutError:
                pass
        names = sorted({repr(peer.name) for peer in peers.values()})
        log(f"Wi-Fi Direct discovery: {len(peers)} peer(s), names: {', '.join(names) or '(none)'}")
        if not peers:
            raise ConnectionError("Windows saw no Wi-Fi Direct peer during the transfer window")
        raise ConnectionError("No unique Wi-Fi Direct peer matched the sender")
    finally:
        watcher.stop()


async def receive(offer, receive_files, log):
    peer = await find_sender(offer, log)
    log(f"Wi-Fi Direct peer matched: {peer.name!r}; paired={peer.pairing.is_paired}")
    if not peer.pairing.is_paired:
        raise ConnectionError(f"Wi-Fi Direct peer {peer.name!r} is visible but not paired in Windows")
    device = await asyncio.wait_for(WiFiDirectDevice.from_id_async(peer.id), 12)
    if device is None:
        raise ConnectionError("Windows could not open a Wi-Fi Direct session")
    try:
        for pair in device.get_connection_endpoint_pairs():
            try:
                local = str(ipaddress.IPv4Address(pair.local_host_name.canonical_name))
                remote = str(ipaddress.IPv4Address(pair.remote_host_name.canonical_name))
            except (ipaddress.AddressValueError, AttributeError):
                continue
            log(f"Wi-Fi Direct session: {local} -> {remote}")
            return receive_files(local, remote)
        raise ConnectionError("Wi-Fi Direct session has no IPv4 endpoint pair")
    finally:
        device.close()


if __name__ == "__main__":
    try:
        asyncio.run(find_sender({"sender_dev_id": 0, "name": ""}, print, timeout=4))
    except ConnectionError as error:
        print(error)
