"""Where document content goes: language model and embedding endpoints outside the local
network are reported at start, because document text leaves the system there."""

import ipaddress

from papiq.composition.settings import Settings

# Host names that stay in the local network: Docker service names have no dot.
_LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".home.arpa", ".localhost")


def is_local_host(host: str) -> bool:
    """True for loopback, private and link-local addresses and local names."""
    host = host.strip("[]").lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or "." not in host or host.endswith(_LOCAL_SUFFIXES)
    return address.is_private or address.is_loopback or address.is_link_local


def external_endpoints(settings: Settings) -> dict[str, str]:
    """The configured endpoints that are not local: variable name to host."""
    found: dict[str, str] = {}
    for name in ("llm_base_url", "embedding_base_url"):
        url = getattr(settings, name)
        if url is not None and url.host is not None and not is_local_host(url.host):
            found[f"PAPIQ_{name.upper()}"] = url.host
    return found
