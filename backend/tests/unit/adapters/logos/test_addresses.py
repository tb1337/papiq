"""Only public addresses: what the guard against SSRF lets through."""

import pytest

from papiq.adapters.outbound.logos import is_public, system_resolver


@pytest.mark.parametrize(
    "address",
    [
        "10.1.2.3",  # private
        "172.16.0.1",
        "192.168.178.1",
        "127.0.0.1",  # loopback
        "127.255.255.254",
        "169.254.169.254",  # link-local: a cloud's metadata service
        "100.64.0.1",  # shared (carrier-grade NAT)
        "100.127.255.254",
        "0.0.0.0",
        "0.1.2.3",
        "255.255.255.255",
        "224.0.0.1",  # multicast
        "239.255.255.250",
        "240.0.0.1",  # reserved
        "192.0.0.8",
        "192.0.2.1",  # documentation
        "198.18.0.1",  # benchmarking
        "::1",
        "::",
        "fe80::1",  # link-local
        "fe80::1%eth0",
        "fc00::1",  # unique local
        "fd12:3456:789a::1",
        "fec0::1",  # site-local
        "ff02::1",  # multicast
        "ff0e::1",
        "::ffff:10.0.0.1",  # IPv4-mapped
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "::ffff:100.64.0.1",
        "::127.0.0.1",  # IPv4-compatible
        "::93.184.216.34",
        "64:ff9b::a00:1",  # NAT64 of 10.0.0.1
        "64:ff9b::7f00:1",
        "64:ff9b:1::1",  # local NAT64
        "2002:a00:1::1",  # 6to4
        "2002:5db8:d822::1",
        "2001::1",  # Teredo
        "2001:db8::1",  # documentation
        "100::1",  # discard
        "192.88.99.1",  # 6to4 relays
        "localhost",
        "example.org",
        "",
        "999.1.1.1",
    ],
)
def test_refuses_what_is_not_public(address: str) -> None:
    assert not is_public(address)


@pytest.mark.parametrize(
    "address",
    [
        "93.184.216.34",
        "1.1.1.1",
        "8.8.8.8",
        "100.63.255.255",  # just below the shared range
        "100.128.0.1",  # just above it
        "2606:4700:4700::1111",
        "2a00:1450:4001:80b::200e",
        "::ffff:93.184.216.34",  # a mapped public address is that address
        "64:ff9b::5db8:d822",  # NAT64 of 93.184.216.34
    ],
)
def test_lets_public_addresses_through(address: str) -> None:
    assert is_public(address)


async def test_the_system_resolver_gives_an_address_as_it_is() -> None:
    """Numeric hosts only: no name server is asked."""
    assert await system_resolver("127.0.0.1") == ["127.0.0.1"]
    assert await system_resolver("::1") == ["::1"]
