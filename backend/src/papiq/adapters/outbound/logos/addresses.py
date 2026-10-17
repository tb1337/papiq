"""Where Papiq may connect when it fetches on behalf of data learned from documents: public
addresses only, as a protection against server-side request forgery (SSRF).

A domain learned from a document may name any host, also one that resolves to the local
network, the machine itself or a cloud's metadata service. An address is public if Python
counts it as globally reachable (`is_global`) and it is none of these besides: private,
loopback, link-local, multicast, reserved, unspecified, shared (100.64.0.0/10, carrier-grade
NAT), the 6to4 relays (192.88.99.0/24), unique local (fc00::/7), site-local, with a zone,
IPv4-compatible (::a.b.c.d), 6to4 or Teredo. An IPv4 address inside an IPv6 one, mapped
(::ffff:a.b.c.d) or behind the well-known NAT64 prefix (64:ff9b::/96), counts as that IPv4
address.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence

type Resolver = Callable[[str], Awaitable[Sequence[str]]]
"""The addresses (as text) a host name resolves to; OSError if it resolves to none."""

_SHARED = ipaddress.IPv4Network("100.64.0.0/10")
_SIXTOFOUR_RELAY = ipaddress.IPv4Network("192.88.99.0/24")
_UNIQUE_LOCAL = ipaddress.IPv6Network("fc00::/7")
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.IPv6Network("64:ff9b:1::/48")
_IPV4_COMPATIBLE = ipaddress.IPv6Network("::/96")


def is_public(address: str) -> bool:
    """Whether Papiq may connect to `address` (text, IPv4 or IPv6); False for what is no
    address."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return _public(parsed)


def _public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        if address.scope_id:
            return False
        if address.ipv4_mapped is not None:
            return _public(address.ipv4_mapped)
        if address in _NAT64:
            return _public(ipaddress.IPv4Address(int(address) & 0xFFFF_FFFF))
        if (
            address in _UNIQUE_LOCAL
            or address in _NAT64_LOCAL
            or address in _IPV4_COMPATIBLE
            or address.sixtofour is not None
            or address.teredo is not None
            or address.is_site_local
        ):
            return False
    elif address in _SHARED or address in _SIXTOFOUR_RELAY:
        return False
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def literal(host: str) -> str | None:
    """`host` if it is an address itself (no name to resolve), else None."""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


async def system_resolver(host: str) -> list[str]:
    """The addresses the system's resolver gives `host` for HTTPS over TCP."""
    loop = asyncio.get_running_loop()
    found = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    addresses = list(dict.fromkeys(str(info[4][0]) for info in found))
    if not addresses:
        raise OSError(f"{host} resolves to no address")
    return addresses
