from __future__ import annotations

"""Link verification service for ChaosWing.

Validates Polymarket and external URLs by performing lightweight HEAD requests
with caching to prevent re-checking known-good or known-bad links.
"""

import logging
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.conf import settings


logger = logging.getLogger("apps.web.services.link_verifier")


class _VerificationCache:
    """Thread-safe TTL cache for URL verification results."""

    __slots__ = ("_lock", "_cache", "_ttl")

    def __init__(self, ttl: int = 600):
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[bool, float]] = {}
        self._ttl = ttl

    def get(self, url: str) -> bool | None:
        with self._lock:
            entry = self._cache.get(url)
            if entry is None:
                return None
            is_valid, ts = entry
            if time.monotonic() - ts > self._ttl:
                del self._cache[url]
                return None
            return is_valid

    def set(self, url: str, is_valid: bool) -> None:
        with self._lock:
            self._cache[url] = (is_valid, time.monotonic())

    def cleanup(self) -> None:
        cutoff = time.monotonic() - self._ttl
        with self._lock:
            stale = [k for k, (_, ts) in self._cache.items() if ts < cutoff]
            for k in stale:
                del self._cache[k]


_verification_cache = _VerificationCache(ttl=600)


POLYMARKET_DOMAINS = frozenset({"polymarket.com", "www.polymarket.com"})


class LinkVerificationService:
    """Validates URLs before they are shown to users.

    Performs HEAD requests to confirm the target page exists (HTTP 2xx/3xx).
    Results are cached for 10 minutes.
    """

    def __init__(self, timeout_seconds: float | None = None):
        self.timeout_seconds = (
            min(timeout_seconds or settings.CHAOSWING_HTTP_TIMEOUT_SECONDS, 5.0)
        )

    def verify_url(self, url: str) -> bool:
        """Returns True if the URL appears to be live and reachable."""
        if not url or not self._is_valid_structure(url):
            return False

        cached = _verification_cache.get(url)
        if cached is not None:
            return cached

        is_valid = self._check_url(url)
        _verification_cache.set(url, is_valid)
        return is_valid

    def verify_polymarket_url(self, url: str) -> bool:
        """Validates that a Polymarket URL is both structurally valid and reachable."""
        if not self._is_polymarket_url(url):
            return False
        return self.verify_url(url)

    def verify_batch(self, urls: list[str]) -> dict[str, bool]:
        """Verify multiple URLs. Returns a mapping of url -> is_valid."""
        results = {}
        for url in urls:
            results[url] = self.verify_url(url)
        return results

    def build_verified_event_url(self, slug: str) -> str | None:
        """Build and verify a canonical Polymarket event URL from a slug."""
        if not slug:
            return None
        url = f"https://polymarket.com/event/{slug}"
        if self.verify_url(url):
            return url
        return None

    def _check_url(self, url: str) -> bool:
        try:
            request = Request(
                url,
                method="HEAD",
                headers={
                    "User-Agent": "ChaosWing/0.1 link-verifier (+https://chaos-wing.com)",
                    "Accept": "text/html,application/json",
                },
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.status < 400
        except HTTPError as exc:
            logger.debug("Link verification failed for %s: HTTP %d", url, exc.code)
            if exc.code == 405:
                return self._check_url_get_fallback(url)
            return False
        except (URLError, OSError, TimeoutError):
            logger.debug("Link verification unreachable: %s", url)
            return False

    def _check_url_get_fallback(self, url: str) -> bool:
        """Some servers reject HEAD; fall back to a range-limited GET."""
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "ChaosWing/0.1 link-verifier",
                    "Accept": "text/html",
                    "Range": "bytes=0-0",
                },
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.status < 400
        except (HTTPError, URLError, OSError, TimeoutError):
            return False

    def _is_valid_structure(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            return bool(parsed.scheme in {"http", "https"} and parsed.netloc)
        except Exception:
            return False

    def _is_polymarket_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            return parsed.netloc.lower().replace("www.", "") in {"polymarket.com"}
        except Exception:
            return False
