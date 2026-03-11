from __future__ import annotations

"""Production security middleware for ChaosWing.

Provides:
- Sliding-window IP-based rate limiting with burst protection
- Comprehensive security headers (CSP, Permissions-Policy, HSTS)
- Request body size enforcement
- Suspicious request pattern detection and auto-ban
"""

import hashlib
import logging
import time
import threading
from collections import defaultdict
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse


logger = logging.getLogger("chaoswing.security")


# ---------------------------------------------------------------------------
# Sliding-window rate limiter with burst detection
# ---------------------------------------------------------------------------

class _SlidingWindow:
    """Thread-safe sliding window counter per key (IP or IP+path)."""

    __slots__ = ("_lock", "_windows", "_bans", "_ban_duration")

    def __init__(self, ban_duration: int = 600):
        self._lock = threading.Lock()
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._bans: dict[str, float] = {}
        self._ban_duration = ban_duration

    def is_banned(self, key: str) -> bool:
        with self._lock:
            ban_until = self._bans.get(key)
            if ban_until is None:
                return False
            if time.monotonic() > ban_until:
                del self._bans[key]
                return False
            return True

    def ban(self, key: str) -> None:
        with self._lock:
            self._bans[key] = time.monotonic() + self._ban_duration

    def hit(self, key: str, window_seconds: int, max_hits: int) -> bool:
        """Record a hit. Returns True if the request is within the limit."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            timestamps = self._windows[key]
            timestamps[:] = [ts for ts in timestamps if ts > cutoff]
            if len(timestamps) >= max_hits:
                return False
            timestamps.append(now)
            return True

    def cleanup(self) -> None:
        """Prune stale entries. Call periodically to prevent unbounded growth."""
        cutoff = time.monotonic() - 3600
        with self._lock:
            stale_keys = [k for k, ts_list in self._windows.items() if not ts_list or ts_list[-1] < cutoff]
            for k in stale_keys:
                del self._windows[k]
            stale_bans = [k for k, until in self._bans.items() if time.monotonic() > until]
            for k in stale_bans:
                del self._bans[k]


_rate_limiter = _SlidingWindow(ban_duration=600)
_last_cleanup = time.monotonic()
_CLEANUP_INTERVAL = 300


def _get_client_ip(request: HttpRequest) -> str:
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "0.0.0.0")


def _ip_hash(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Suspicious pattern detection
# ---------------------------------------------------------------------------

_SUSPICIOUS_PATHS = frozenset({
    "/.env", "/wp-admin", "/wp-login.php", "/xmlrpc.php",
    "/phpmyadmin", "/admin.php", "/shell", "/cmd",
    "/.git/config", "/.aws/credentials", "/etc/passwd",
    "/actuator", "/api/v1/../", "/debug", "/trace",
})

_SUSPICIOUS_UA_FRAGMENTS = frozenset({
    "sqlmap", "nikto", "nmap", "masscan", "zgrab",
    "gobuster", "dirbuster", "wfuzz", "hydra",
    "nuclei", "jaeles", "xray",
})


def _is_suspicious(request: HttpRequest) -> bool:
    path = request.path.lower().rstrip("/")
    if any(path.startswith(p) or path.endswith(p) for p in _SUSPICIOUS_PATHS):
        return True
    if ".." in request.path:
        return True
    ua = request.META.get("HTTP_USER_AGENT", "").lower()
    if any(frag in ua for frag in _SUSPICIOUS_UA_FRAGMENTS):
        return True
    return False


# ---------------------------------------------------------------------------
# Rate Limiting Middleware
# ---------------------------------------------------------------------------

class RateLimitMiddleware:
    """IP-based rate limiter with separate tiers for pages, API reads, and API writes.

    Auto-bans IPs that hit suspicious paths or exceed burst limits.
    """

    TIERS: dict[str, dict[str, Any]] = {
        "api_write": {"window": 60, "max_hits": 12, "paths": {"/api/v1/graph/from-url/", "/api/v1/runs/"}},
        "api_read": {"window": 60, "max_hits": 60, "paths": {"/api/v1/"}},
        "page": {"window": 60, "max_hits": 40, "paths": {"/", "/app/"}},
    }
    BURST_WINDOW = 5
    BURST_MAX = 20

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = getattr(settings, "CHAOSWING_RATE_LIMIT_ENABLED", True)

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not self.enabled or settings.DEBUG:
            return self.get_response(request)

        global _last_cleanup
        now = time.monotonic()
        if now - _last_cleanup > _CLEANUP_INTERVAL:
            _rate_limiter.cleanup()
            _last_cleanup = now

        ip = _get_client_ip(request)
        ip_key = _ip_hash(ip)

        if _rate_limiter.is_banned(ip_key):
            logger.warning("Blocked banned IP: %s", ip_key)
            return JsonResponse(
                {"error": "Too many requests. You have been temporarily blocked."},
                status=429,
            )

        if _is_suspicious(request):
            _rate_limiter.ban(ip_key)
            logger.warning("Auto-banned suspicious request from %s: %s", ip_key, request.path)
            return HttpResponse(status=403)

        burst_key = f"burst:{ip_key}"
        if not _rate_limiter.hit(burst_key, self.BURST_WINDOW, self.BURST_MAX):
            _rate_limiter.ban(ip_key)
            logger.warning("Burst limit exceeded, auto-banned: %s", ip_key)
            return JsonResponse(
                {"error": "Request rate too high. Slow down."},
                status=429,
            )

        tier = self._classify_request(request)
        if tier:
            tier_key = f"{tier['name']}:{ip_key}"
            if not _rate_limiter.hit(tier_key, tier["window"], tier["max_hits"]):
                remaining_seconds = tier["window"]
                response = JsonResponse(
                    {"error": f"Rate limit exceeded. Try again in {remaining_seconds}s."},
                    status=429,
                )
                response["Retry-After"] = str(remaining_seconds)
                return response

        return self.get_response(request)

    def _classify_request(self, request: HttpRequest) -> dict[str, Any] | None:
        path = request.path.rstrip("/") + "/"
        if request.method == "POST" and path.startswith("/api/"):
            return {**self.TIERS["api_write"], "name": "api_write"}
        if path.startswith("/api/"):
            return {**self.TIERS["api_read"], "name": "api_read"}
        if path in {"/", "/app/"}:
            return {**self.TIERS["page"], "name": "page"}
        return None


# ---------------------------------------------------------------------------
# Security Headers Middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware:
    """Adds production-hardened security headers to every response.

    CSP, Permissions-Policy, HSTS, and anti-abuse headers.
    """

    CSP_DIRECTIVES = {
        "default-src": "'self'",
        "script-src": "'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net",
        "style-src": "'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src": "'self' https://fonts.gstatic.com",
        "img-src": "'self' data: https://*.polymarket.com https://polymarket.com blob:",
        "connect-src": "'self' https://gamma-api.polymarket.com",
        "frame-ancestors": "'none'",
        "base-uri": "'self'",
        "form-action": "'self'",
        "object-src": "'none'",
    }

    PERMISSIONS_POLICY = (
        "accelerometer=(), autoplay=(), camera=(), "
        "cross-origin-isolated=(), display-capture=(), encrypted-media=(), "
        "fullscreen=(self), geolocation=(), gyroscope=(), keyboard-map=(), "
        "magnetometer=(), microphone=(), midi=(), payment=(), "
        "picture-in-picture=(), publickey-credentials-get=(), "
        "screen-wake-lock=(), sync-xhr=(), usb=(), xr-spatial-tracking=()"
    )

    def __init__(self, get_response):
        self.get_response = get_response
        csp_parts = [f"{key} {value}" for key, value in self.CSP_DIRECTIVES.items()]
        self.csp_header = "; ".join(csp_parts)

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)

        response["Content-Security-Policy"] = self.csp_header
        response["Permissions-Policy"] = self.PERMISSIONS_POLICY
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "DENY"
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response["Cross-Origin-Opener-Policy"] = "same-origin"
        response["Cross-Origin-Embedder-Policy"] = "credentialless"
        response["Cross-Origin-Resource-Policy"] = "same-origin"

        if not settings.DEBUG:
            response["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"

        return response


# ---------------------------------------------------------------------------
# Request Size Limit Middleware
# ---------------------------------------------------------------------------

MAX_BODY_BYTES = 1_048_576  # 1 MB


class RequestSizeLimitMiddleware:
    """Rejects requests with bodies larger than the allowed threshold."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.max_bytes = getattr(settings, "CHAOSWING_MAX_REQUEST_BODY_BYTES", MAX_BODY_BYTES)

    def __call__(self, request: HttpRequest) -> HttpResponse:
        content_length = request.META.get("CONTENT_LENGTH")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    return JsonResponse(
                        {"error": "Request body too large."},
                        status=413,
                    )
            except (TypeError, ValueError):
                pass
        return self.get_response(request)
