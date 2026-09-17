"""SSRF protection for the URL-fetching feature (brief section 7's Method A).

Accepting an arbitrary URL from any (anonymous) visitor and having the
server fetch it is a classic SSRF vector — without this, someone could
point the "product URL" field at http://169.254.169.254/ (a cloud
metadata endpoint), http://localhost:5432/, an internal admin panel, etc.
and use our server as a proxy into a network it can't otherwise reach.

Two layers, both required:
  - assert_safe_url() — a fast-fail check callers may use early (e.g.
    before constructing a robots.txt URL), but NOT the actual security
    boundary on its own; see below.
  - resolve_pinned_connect_url() — the real boundary. http_fetcher.py
    calls this immediately before every actual network connection (the
    initial fetch and every redirect hop; redirects are followed
    manually, never automatically, specifically so each hop gets
    re-validated here rather than trusting the first check to cover a
    chain that could end up somewhere different).

DNS-rebinding gap, closed: resolving a hostname and checking the result at
call time, then letting the HTTP client re-resolve and connect
*separately* moments later, leaves a window where the hostname could
resolve differently in between (a hostname's DNS record changed, a
malicious authoritative server returning a different answer to the
second lookup). resolve_pinned_connect_url() resolves once, validates
that address, and returns a URL with the hostname replaced by that exact
validated IP — the caller connects to precisely the address that was
checked, never a second, independent resolution. The original hostname
must still be sent as the Host header and used as the TLS SNI name (via
httpx's `extensions={"sni_hostname": ...}`) so virtual-hosting and
certificate validation still work correctly against the real domain.
"""

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

from .base import ProductExtractionError

GENERIC_BLOCKED_MESSAGE = (
    "We couldn't process that link. Please upload a product photo or screenshot instead."
)

_ALLOWED_SCHEMES = {"http", "https"}

# RFC 6052 "Well-Known Prefix" for NAT64/DNS64: an IPv6-only network (common
# for cellular carriers, and — found the hard way, via a real 400 on a real
# public test URL during Milestone 8 verification — some cloud/CI egress
# setups) synthesizes addresses in this block to reach ordinary public IPv4
# hosts. Python's ipaddress module marks the whole prefix `.is_reserved`,
# which is technically correct (IANA reserves the block for this mechanism)
# but wrong for our purposes: the address still names a real, checkable
# IPv4 destination, embedded in its low 32 bits. Unwrap it and check that
# instead of blanket-rejecting every NAT64-synthesized address.
_NAT64_WELL_KNOWN_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def assert_safe_url(url: str) -> None:
    """Raises ProductExtractionError if `url` is not safe to fetch server-side.

    A fast-fail convenience check only — see module docstring.
    resolve_pinned_connect_url() is what actually guards every real
    network connection.
    """
    resolve_pinned_connect_url(url)


def resolve_pinned_connect_url(url: str) -> tuple[str, str]:
    """Validates `url` exactly like assert_safe_url, and returns
    (connect_url, hostname): connect_url has the hostname replaced by the
    single already-validated IP address to actually connect to, and
    hostname is the original hostname the caller must still send as the
    Host header and use as the TLS SNI name. See module docstring for why.
    """
    parsed = urlparse(url)

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ProductExtractionError(GENERIC_BLOCKED_MESSAGE)

    hostname = parsed.hostname
    if not hostname:
        raise ProductExtractionError(GENERIC_BLOCKED_MESSAGE)

    if hostname.lower() in ("localhost", "localhost.localdomain"):
        raise ProductExtractionError(GENERIC_BLOCKED_MESSAGE)

    try:
        # AF_UNSPEC + all results checked: covers both IPv4 and IPv6, and a
        # hostname that resolves to multiple addresses (only one needs to be
        # unsafe for us to refuse the whole request).
        addr_infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        raise ProductExtractionError(GENERIC_BLOCKED_MESSAGE)

    pinned_ip: "ipaddress.IPv4Address | ipaddress.IPv6Address | None" = None
    for family, _, _, _, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise ProductExtractionError(GENERIC_BLOCKED_MESSAGE)
        if _is_disallowed(ip):
            raise ProductExtractionError(GENERIC_BLOCKED_MESSAGE)
        if pinned_ip is None:
            pinned_ip = ip  # pin to the first resolved, already-validated address

    if pinned_ip is None:
        raise ProductExtractionError(GENERIC_BLOCKED_MESSAGE)

    host_part = f"[{pinned_ip}]" if pinned_ip.version == 6 else str(pinned_ip)
    port_part = f":{parsed.port}" if parsed.port else ""
    connect_url = urlunparse(parsed._replace(netloc=f"{host_part}{port_part}"))
    return connect_url, hostname


def _is_disallowed(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_WELL_KNOWN_PREFIX:
        embedded_v4 = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        return _is_disallowed(embedded_v4)

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # covers 169.254.0.0/16, including the 169.254.169.254 cloud metadata endpoint
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
