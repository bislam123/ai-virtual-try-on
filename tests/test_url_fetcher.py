"""Milestone 8 tests for Method A (product URL) — fetchers/.

Two layers, tested separately on purpose:
  - ssrf_guard: real DNS resolution, no network beyond that. Blocking
    private/loopback/link-local ranges is the actual security property —
    these assertions are the ones that matter most in this file. Also
    covers resolve_pinned_connect_url() (the DNS-rebinding fix — see its
    module docstring) with a mocked resolver, so the pinning logic itself
    is tested deterministically without depending on real DNS.
  - HttpProductPageFetcher's fetch/parse orchestration: SSRF-blocked hosts
    (localhost etc.) can't double as "a fake external site" for testing the
    parsing logic, so those tests monkeypatch both assert_safe_url and
    resolve_pinned_connect_url to no-ops/passthroughs and use
    httpx.MockTransport (injected via the constructor) to intercept the
    actual HTTP call — no real network access, fully deterministic. The
    guard itself is never weakened in the real code path, only in these
    specific test doubles. test_request_connects_to_pinned_ip_* below is
    the one exception: it deliberately does NOT bypass pinning, to prove
    the real mechanism wires through _request end-to-end.
"""

from urllib.parse import urlparse

import httpx
import pytest
from PIL import Image

from product_extractor.fetchers import http_fetcher as http_fetcher_module
from product_extractor.fetchers.base import ProductExtractionError
from product_extractor.fetchers.http_fetcher import HttpProductPageFetcher
from product_extractor.fetchers.ssrf_guard import assert_safe_url, resolve_pinned_connect_url


# --- ssrf_guard ---------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://127.0.0.1:8000/api/try-on",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "ftp://example.com/",
        "file:///etc/passwd",
        "not-a-url",
    ],
)
def test_ssrf_guard_blocks_unsafe_targets(url):
    with pytest.raises(ProductExtractionError):
        assert_safe_url(url)


@pytest.mark.parametrize("url", ["http://example.com/product", "https://example.com/product"])
def test_ssrf_guard_allows_public_hosts(url):
    assert_safe_url(url)  # must not raise


def test_ssrf_guard_unwraps_nat64_synthesized_addresses(monkeypatch):
    """Regression test for a real bug found while verifying Milestone 8
    against a real public URL: an IPv6-only network reaching an ordinary
    public IPv4 site via NAT64/DNS64 synthesizes an address in
    64:ff9b::/96. Python's ipaddress marks that whole prefix `.is_reserved`,
    which used to make us reject every such request outright — including
    ones pointing at a perfectly safe public IPv4 destination."""
    import socket as socket_module

    from product_extractor.fetchers import ssrf_guard as ssrf_guard_module

    public_v4 = "35.211.122.109"
    nat64_synthesized = "64:ff9b::23d3:7a6d"  # encodes the same address above

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket_module.AF_INET6, socket_module.SOCK_STREAM, 6, "", (nat64_synthesized, 0, 0, 0))]

    monkeypatch.setattr(ssrf_guard_module.socket, "getaddrinfo", fake_getaddrinfo)
    assert_safe_url("https://example-behind-nat64.test/product")  # must not raise

    def fake_getaddrinfo_private(host, *args, **kwargs):
        # 64:ff9b::/96 + 10.0.0.1 (a private address) encoded in the low 32 bits
        return [(socket_module.AF_INET6, socket_module.SOCK_STREAM, 6, "", ("64:ff9b::a00:1", 0, 0, 0))]

    monkeypatch.setattr(ssrf_guard_module.socket, "getaddrinfo", fake_getaddrinfo_private)
    with pytest.raises(ProductExtractionError):
        assert_safe_url("https://example-behind-nat64.test/product")


def test_resolve_pinned_connect_url_substitutes_ip_and_returns_hostname(monkeypatch):
    """The core DNS-rebinding fix: the returned connect_url must use the
    exact IP that was just validated, not the original hostname (which a
    second, later resolution could answer differently)."""
    import socket as socket_module

    from product_extractor.fetchers import ssrf_guard as ssrf_guard_module

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket_module.AF_INET, socket_module.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(ssrf_guard_module.socket, "getaddrinfo", fake_getaddrinfo)

    connect_url, hostname = resolve_pinned_connect_url("https://example.test:8443/path?q=1")
    assert connect_url == "https://93.184.216.34:8443/path?q=1"
    assert hostname == "example.test"


def test_resolve_pinned_connect_url_brackets_ipv6_pinned_address(monkeypatch):
    import socket as socket_module

    from product_extractor.fetchers import ssrf_guard as ssrf_guard_module

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket_module.AF_INET6, socket_module.SOCK_STREAM, 6, "", ("2606:4700::1", 0, 0, 0))]

    monkeypatch.setattr(ssrf_guard_module.socket, "getaddrinfo", fake_getaddrinfo)

    connect_url, hostname = resolve_pinned_connect_url("https://example.test/path")
    assert connect_url == "https://[2606:4700::1]/path"
    assert hostname == "example.test"


def test_resolve_pinned_connect_url_still_rejects_unsafe_resolved_address(monkeypatch):
    import socket as socket_module

    from product_extractor.fetchers import ssrf_guard as ssrf_guard_module

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket_module.AF_INET, socket_module.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(ssrf_guard_module.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ProductExtractionError):
        resolve_pinned_connect_url("https://example.test/path")


# --- HttpProductPageFetcher: parsing/orchestration ----------------------


def _disable_ssrf_pinning(monkeypatch) -> None:
    """Disables both the early fast-fail check and the pinning step
    _request actually uses, so MockTransport route lookups below can match
    on the original, human-readable URL rather than a real-DNS-resolved IP
    — see module docstring for why the guard itself is never weakened in
    the real code path, only in these test doubles."""
    monkeypatch.setattr(http_fetcher_module, "assert_safe_url", lambda url: None)
    monkeypatch.setattr(
        http_fetcher_module, "resolve_pinned_connect_url", lambda url: (url, urlparse(url).hostname)
    )


def _mock_fetcher(monkeypatch, routes: dict) -> HttpProductPageFetcher:
    """routes: {url: httpx.Response}."""
    _disable_ssrf_pinning(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in routes:
            return routes[url]
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    return HttpProductPageFetcher(transport=transport)


def _png_bytes(color="blue") -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (300, 400), color=color).save(buf, format="PNG")
    return buf.getvalue()


def test_extracts_image_from_jsonld_product_schema(monkeypatch):
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Product", "name": "Cool Shirt",
     "image": "https://example.com/shirt.png"}
    </script>
    </head><body></body></html>
    """
    fetcher = _mock_fetcher(
        monkeypatch,
        {
            "http://example.com/robots.txt": httpx.Response(404),
            "http://example.com/product": httpx.Response(200, text=html, headers={"content-type": "text/html"}),
            "https://example.com/shirt.png": httpx.Response(
                200, content=_png_bytes(), headers={"content-type": "image/png"}
            ),
        },
    )

    image = fetcher.fetch_product_image("http://example.com/product")
    assert image.size == (300, 400)


def test_falls_back_to_og_image_when_no_jsonld(monkeypatch):
    html = """
    <html><head>
    <meta property="og:image" content="/images/shirt.png" />
    </head><body></body></html>
    """
    fetcher = _mock_fetcher(
        monkeypatch,
        {
            "http://example.com/robots.txt": httpx.Response(404),
            "http://example.com/product": httpx.Response(200, text=html, headers={"content-type": "text/html"}),
            "http://example.com/images/shirt.png": httpx.Response(
                200, content=_png_bytes("green"), headers={"content-type": "image/png"}
            ),
        },
    )

    image = fetcher.fetch_product_image("http://example.com/product")
    assert image.size == (300, 400)


def test_no_image_found_raises_clear_error(monkeypatch):
    html = "<html><head><title>No product data here</title></head><body></body></html>"
    fetcher = _mock_fetcher(
        monkeypatch,
        {
            "http://example.com/robots.txt": httpx.Response(404),
            "http://example.com/product": httpx.Response(200, text=html, headers={"content-type": "text/html"}),
        },
    )

    with pytest.raises(ProductExtractionError, match="couldn't find a product image"):
        fetcher.fetch_product_image("http://example.com/product")


def test_robots_txt_disallow_blocks_extraction(monkeypatch):
    robots = "User-agent: *\nDisallow: /\n"
    fetcher = _mock_fetcher(
        monkeypatch,
        {"http://example.com/robots.txt": httpx.Response(200, text=robots)},
    )

    with pytest.raises(ProductExtractionError, match="doesn't allow automatic product extraction"):
        fetcher.fetch_product_image("http://example.com/product")


def test_unreachable_page_raises_clear_error(monkeypatch):
    fetcher = _mock_fetcher(
        monkeypatch,
        {"http://example.com/robots.txt": httpx.Response(404)},
        # no route for /product -> handler returns 404
    )

    with pytest.raises(ProductExtractionError, match="couldn't reach that page"):
        fetcher.fetch_product_image("http://example.com/product")


# --- HttpProductPageFetcher.fetch_image_from_url (Milestone 9, extended) --


def test_fetch_image_from_url_downloads_direct_image(monkeypatch):
    """No page fetch, no robots.txt check — only the image URL itself is
    requested. Asserts no request was made to any other route to prove
    the page-fetch/robots.txt steps are genuinely skipped, not just
    unused by this particular route table."""
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})

    _disable_ssrf_pinning(monkeypatch)
    transport = httpx.MockTransport(handler)

    fetcher = HttpProductPageFetcher(transport=transport)
    image = fetcher.fetch_image_from_url("https://cdn.example.com/shirt.png")
    assert image.size == (300, 400)
    assert requested_urls == ["https://cdn.example.com/shirt.png"]


def test_fetch_image_from_url_still_ssrf_guarded():
    fetcher = HttpProductPageFetcher()
    with pytest.raises(ProductExtractionError):
        fetcher.fetch_image_from_url("http://169.254.169.254/latest/meta-data/")


def test_fetch_image_from_url_invalid_content_raises_clear_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not an image", headers={"content-type": "text/html"})

    _disable_ssrf_pinning(monkeypatch)
    transport = httpx.MockTransport(handler)

    fetcher = HttpProductPageFetcher(transport=transport)
    with pytest.raises(ProductExtractionError, match="couldn't find a product image"):
        fetcher.fetch_image_from_url("https://cdn.example.com/not-an-image")


def test_fetch_image_from_url_rejects_oversized_dimensions_before_full_decode(monkeypatch):
    """A production-readiness audit finding: _decode_image used to call
    img.load() with no dimension check at all, on these unauthenticated,
    internet-reachable endpoints -- an image whose header declares
    dimensions in Pillow's own DecompressionBombWarning band (above
    Image.MAX_IMAGE_PIXELS but below its 2x hard-error threshold) would
    fully decode into memory before anything rejected it. Fixed by reading
    width/height from the header first, mirroring
    backend/app/core/validation.py's existing pattern for direct uploads.

    The dimension cap is monkeypatched down to keep this test fast and
    memory-light -- the real 4096px production cap would need an
    unreasonably large image to actually exceed."""
    monkeypatch.setattr(http_fetcher_module, "_MAX_IMAGE_DIMENSION_PX", 10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})  # 300x400

    _disable_ssrf_pinning(monkeypatch)
    transport = httpx.MockTransport(handler)

    fetcher = HttpProductPageFetcher(transport=transport)
    with pytest.raises(ProductExtractionError, match="couldn't find a product image"):
        fetcher.fetch_image_from_url("https://cdn.example.com/oversized.png")


def test_request_connects_to_pinned_ip_not_a_second_dns_lookup(monkeypatch):
    """The DNS-rebinding fix, verified end-to-end through _request: the
    actual HTTP connection must go to the IP resolve_pinned_connect_url
    already validated -- not a second, independent resolution of the
    hostname at connect time -- while still sending the original hostname
    as the Host header and TLS SNI name so virtual-hosting/cert validation
    keep working. Deliberately does NOT bypass pinning (unlike every other
    test in this section) to prove the real mechanism, not a stand-in."""
    import socket as socket_module

    from product_extractor.fetchers import ssrf_guard as ssrf_guard_module

    def fake_getaddrinfo(host, *args, **kwargs):
        assert host == "pinned-example.test"
        return [(socket_module.AF_INET, socket_module.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(ssrf_guard_module.socket, "getaddrinfo", fake_getaddrinfo)

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.url.host
        captured["header_host"] = request.headers.get("host")
        captured["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})

    transport = httpx.MockTransport(handler)
    fetcher = HttpProductPageFetcher(transport=transport)

    image = fetcher.fetch_image_from_url("https://pinned-example.test/shirt.png")
    assert image.size == (300, 400)
    assert captured["host"] == "93.184.216.34"
    assert captured["header_host"] == "pinned-example.test"
    assert captured["sni"] == "pinned-example.test"
