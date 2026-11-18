"""Read Windows IPv4 interface addresses without launching PowerShell."""

import ctypes as C
import socket
from ctypes import wintypes as W


class SocketAddress(C.Structure):
    _fields_ = [('pointer', C.c_void_p), ('length', C.c_int)]


class Unicast(C.Structure):
    pass


Unicast._fields_ = [
    ('alignment', C.c_uint64), ('next', C.POINTER(Unicast)),
    ('address', SocketAddress), ('prefix_origin', W.DWORD),
    ('suffix_origin', W.DWORD), ('dad_state', W.DWORD),
]


class Adapter(C.Structure):
    pass


Adapter._fields_ = [
    ('alignment', C.c_uint64), ('next', C.POINTER(Adapter)),
    ('name', C.c_char_p), ('unicast', C.POINTER(Unicast)),
    ('anycast', C.c_void_p), ('multicast', C.c_void_p), ('dns_servers', C.c_void_p),
    ('dns_suffix', C.c_wchar_p), ('description', C.c_wchar_p), ('friendly_name', C.c_wchar_p),
    ('physical_address', C.c_ubyte * 8), ('physical_address_length', W.DWORD),
    ('flags', W.DWORD), ('mtu', W.DWORD), ('if_type', W.DWORD), ('oper_status', W.DWORD),
]


def preferred_ipv4():
    get_adapters = C.WinDLL('iphlpapi').GetAdaptersAddresses
    get_adapters.argtypes = (W.ULONG, W.ULONG, C.c_void_p, C.c_void_p, C.POINTER(W.ULONG))
    get_adapters.restype = W.ULONG
    size = W.ULONG(15 * 1024)
    for _ in range(3):
        buffer = C.create_string_buffer(size.value)
        # Skip anycast, multicast and DNS entries. Keep unicast addresses.
        result = get_adapters(socket.AF_INET, 0xE, None, buffer, C.byref(size))
        if result != 111:  # ERROR_BUFFER_OVERFLOW
            break
    if result == 232:  # ERROR_NO_DATA
        return
    if result:
        raise C.WinError(result)
    cursor = C.cast(buffer, C.POINTER(Adapter))
    while cursor:
        item = cursor.contents
        cursor = item.next
        if item.oper_status != 1:  # IfOperStatusUp
            continue
        address = item.unicast
        while address:
            entry = address.contents
            address = entry.next
            raw = entry.address
            if entry.dad_state == 4 and raw.pointer and raw.length >= 16:  # IpDadStatePreferred
                if C.c_ushort.from_address(raw.pointer).value == socket.AF_INET:
                    yield item.description or '', socket.inet_ntoa(C.string_at(raw.pointer + 4, 4))
