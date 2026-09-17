"""HttpProductPageFetcher: the concrete ProductPageFetcher (brief Method A).

Fetch a product page -> find its product image (JSON-LD Product schema,
falling back to Open Graph meta tags — both are widely-adopted, publicly
documented conventions sites use specifically so external services *can*
read a page's product image respectfully, unlike scraping arbitrary CSS)
-> download that image.

Explicitly does NOT: use a browser-spoofing User-Agent, defeat CAPTCHAs,
log in, bypass paywalls, or retry around a site's refusal. A site that
blocks us, requires a challenge, or has no structured product data simply
fails extraction — the caller's job is the fallback (ask the user to
upload a photo/screenshot instead), not to try harder to get in.
"""

import json
import logging
from io import BytesIO
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from PIL import Image

from .base import ProductExtractionError, ProductPageFetcher
from .ssrf_guard import assert_safe_url, resolve_pinned_connect_url

logger = logging.getLogger(__name__)

# Identifies this project honestly — the opposite of a browser-spoofing UA
# chosen to slip past a site's bot detection.
USER_AGENT = "AITryOnBot/0.1 (+https://github.com/ai-try-on; product image extraction)"

_REQUEST_TIMEOUT_SECONDS = 10.0
_MAX_PAGE_BYTES = 5 * 1024 * 1024
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_REDIRECTS = 3
_ALLOWED_IMAGE_CONTENT_TYPES = {"JPEG", "PNG", "WEBP"}

NO_IMAGE_FOUND_MESSAGE = (
    "We couldn't find a product image on that page. Please upload a photo or screenshot instead."
)
BLOCKED_BY_ROBOTS_MESSAGE = (
    "This site doesn't allow automatic product extraction. Please upload a photo or screenshot instead."
)
FETCH_FAILED_MESSAGE = "We couldn't reach that page. Please check the link or upload a photo instead."


class HttpProductPageFetcher(ProductPageFetcher):
    def __init__(self, transport: Optional[httpx.BaseTransport] = None):
        # A single, long-lived Client (this fetcher itself is a long-lived
        # singleton, see backend/app/api/extraction.py) -- also required to
        # use build_request()+send() below instead of the module-level
        # httpx.stream() convenience function, since only the Client API
        # accepts the `extensions={"sni_hostname": ...}` needed to connect
        # to a pinned IP while still validating TLS against the real
        # hostname (see ssrf_guard.py). transport= is a test seam only
        # (httpx.MockTransport); production code never passes it.
        self._client = httpx.Client(transport=transport) if transport is not None else httpx.Client()

    def fetch_product_image(self, url: str) -> Image.Image:
        assert_safe_url(url)
        self._check_robots_txt(url)

        html = self._get_text(url, max_bytes=_MAX_PAGE_BYTES)
        image_url = _find_product_image_url(html, base_url=url)
        if image_url is None:
            raise ProductExtractionError(NO_IMAGE_FOUND_MESSAGE)

        assert_safe_url(image_url)  # the image may be on a different host — re-check
        image_bytes = self._get_bytes(image_url, max_bytes=_MAX_IMAGE_BYTES)
        return _decode_image(image_bytes)

    def fetch_image_from_url(self, image_url: str) -> Image.Image:
        """Download a URL that a caller already knows points at a product
        image directly (e.g. a browser extension reading a page's own
        JSON-LD/og:image), skipping robots.txt and HTML/JSON-LD parsing
        entirely — there is no page to scrape here, just one resource to
        fetch. Still goes through the same assert_safe_url + manually
        re-validated redirect chain as every other fetch in this class; see
        ssrf_guard.py. This exists specifically because a shopping site's
        page route can be behind an anti-bot/CAPTCHA wall (see
        fetch_product_image's docstring) while its image CDN host typically
        is not — that difference is the whole point of this method, not a
        way around the wall on the page route itself."""
        assert_safe_url(image_url)
        image_bytes = self._get_bytes(image_url, max_bytes=_MAX_IMAGE_BYTES)
        return _decode_image(image_bytes)

    # --- robots.txt -----------------------------------------------------

    def _check_robots_txt(self, url: str) -> None:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            assert_safe_url(robots_url)
            robots_text = self._get_text(robots_url, max_bytes=_MAX_PAGE_BYTES, allow_404=True)
        except ProductExtractionError:
            # No reachable robots.txt is treated as "no restriction" — the
            # same convention every well-behaved crawler follows.
            return
        if robots_text is None:
            return

        parser = RobotFileParser()
        parser.parse(robots_text.splitlines())
        if not parser.can_fetch(USER_AGENT, url):
            raise ProductExtractionError(BLOCKED_BY_ROBOTS_MESSAGE)

    # --- safe fetch with manual, re-validated redirects ------------------

    def _get_text(self, url: str, max_bytes: int, allow_404: bool = False) -> Optional[str]:
        content = self._request(url, max_bytes, allow_404=allow_404)
        return content.decode("utf-8", errors="replace") if content is not None else None

    def _get_bytes(self, url: str, max_bytes: int) -> bytes:
        content = self._request(url, max_bytes)
        if content is None:
            raise ProductExtractionError(FETCH_FAILED_MESSAGE)
        return content

    def _request(self, url: str, max_bytes: int, allow_404: bool = False) -> Optional[bytes]:
        current_url = url
        headers_base = {"User-Agent": USER_AGENT}
        for _ in range(_MAX_REDIRECTS + 1):
            # Re-resolve, re-validate, and re-pin on every hop -- a redirect
            # can point at an entirely different host, and pinning here
            # (rather than trusting a check against current_url followed by
            # a separate connect-time resolution) is what closes the
            # DNS-rebinding gap; see ssrf_guard.py.
            connect_url, hostname = resolve_pinned_connect_url(current_url)
            headers = {**headers_base, "Host": hostname}
            try:
                request = self._client.build_request(
                    "GET",
                    connect_url,
                    headers=headers,
                    extensions={"sni_hostname": hostname},
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
                response = self._client.send(request, stream=True, follow_redirects=False)
                try:
                    if response.is_redirect:
                        next_url = response.headers.get("location")
                        if not next_url:
                            raise ProductExtractionError(FETCH_FAILED_MESSAGE)
                        current_url = urljoin(current_url, next_url)
                        continue

                    if response.status_code == 404 and allow_404:
                        return None
                    if response.status_code >= 400:
                        raise ProductExtractionError(FETCH_FAILED_MESSAGE)

                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise ProductExtractionError(FETCH_FAILED_MESSAGE)
                    return bytes(content)
                finally:
                    response.close()
            except httpx.HTTPError:
                raise ProductExtractionError(FETCH_FAILED_MESSAGE)
        raise ProductExtractionError(FETCH_FAILED_MESSAGE)  # too many redirects


def _decode_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception:
        raise ProductExtractionError(NO_IMAGE_FOUND_MESSAGE)
    if img.format not in _ALLOWED_IMAGE_CONTENT_TYPES:
        raise ProductExtractionError(NO_IMAGE_FOUND_MESSAGE)
    return img.convert("RGB")


def _find_product_image_url(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")

    image_url = _find_jsonld_product_image(soup)
    if image_url:
        return urljoin(base_url, image_url)

    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        return urljoin(base_url, og_image["content"])

    return None


def _find_jsonld_product_image(soup: BeautifulSoup) -> Optional[str]:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        image = _extract_product_image_from_jsonld(data)
        if image:
            return image
    return None


def _extract_product_image_from_jsonld(data) -> Optional[str]:
    # JSON-LD shows up in the wild as a bare object, a list of objects, or a
    # {"@graph": [...]} wrapper — handle all three.
    candidates = []
    if isinstance(data, dict) and "@graph" in data and isinstance(data["@graph"], list):
        candidates = data["@graph"]
    elif isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        candidates = [data]

    for node in candidates:
        if not isinstance(node, dict):
            continue
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if "Product" not in types:
            continue
        image = node.get("image")
        return _first_image_url(image)
    return None


def _first_image_url(image) -> Optional[str]:
    if isinstance(image, str):
        return image
    if isinstance(image, dict):
        return image.get("url")
    if isinstance(image, list) and image:
        return _first_image_url(image[0])
    return None
