"""SSRF protection for the URL-fetching feature (brief section 7's Method A).

Accepting an arbitrary URL from any (anonymous) visitor and having the
server fetch it is a classic SSRF vector — without this, someone could
point the "product URL" field at http://169.254.169.254/ (a cloud
metadata endpoint), http://localhost:5432/, an internal admin panel, etc.
and use our server as a proxy into a network it can't otherwise reach.

Call assert_safe_url() before *every* outbound request this feature makes
— the initial fetch and every redirect hop (see http_fetcher.py; redirects
are followed manually, never automatically, specifically so each hop gets
re-checked here rather than trusting the first check to cover them all).

HONEST LIMITATION: this resolves the hostname and checks the result at
call time, which is normal, practical SSRF protection — but a hostname
could theoretically be reconfigured to resolve to a private IP *between*
this check and the moment httpx actually connects (DNS rebinding). A
fully airtight fix pins the checked IP and forces the HTTP connection to
use exactly that address (a custom transport), which httpx doesn't make
trivial and which this milestone doesn't implement. Worth doing before
this ever handles untrusted traffic at real scale.
"""

import ipaddress
import socket
from urllib.parse import urlparse

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
    """Raises ProductExtractionError if `url` is not safe to fetch server-side."""
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

    for family, _, _, _, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise ProductExtractionError(GENERIC_BLOCKED_MESSAGE)
        if _is_disallowed(ip):
            raise ProductExtractionError(GENERIC_BLOCKED_MESSAGE)


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
