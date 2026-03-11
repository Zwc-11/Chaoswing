from __future__ import annotations

import base64
import html
import mimetypes
from functools import lru_cache
from urllib.request import Request, urlopen

from django.conf import settings


TYPE_COLOR_MAP = {
    "Event": ("#0b1830", "#58d7ff"),
    "Entity": ("#0e1f19", "#8fd4a4"),
    "RelatedMarket": ("#291c0f", "#f1ca78"),
    "Evidence": ("#161c34", "#9ab2ff"),
    "Rule": ("#17182e", "#86a7ff"),
    "Hypothesis": ("#2a150f", "#ffab8a"),
}


def svg_data_uri(svg_markup: str) -> str:
    encoded = base64.b64encode(svg_markup.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def build_type_icon(label: str, node_type: str) -> str:
    background, accent = TYPE_COLOR_MAP.get(node_type, ("#101826", "#58d7ff"))
    initials = "".join(part[:1] for part in label.split()[:2]).upper() or node_type[:2].upper()
    safe_type = html.escape(node_type)
    safe_initials = html.escape(initials[:2])

    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128" fill="none">
      <defs>
        <linearGradient id="g" x1="12" y1="12" x2="116" y2="116" gradientUnits="userSpaceOnUse">
          <stop stop-color="{background}"/>
          <stop offset="1" stop-color="#0A1018"/>
        </linearGradient>
      </defs>
      <rect x="8" y="8" width="112" height="112" rx="30" fill="url(#g)" stroke="{accent}" stroke-width="4"/>
      <circle cx="64" cy="46" r="20" fill="{accent}" fill-opacity="0.16"/>
      <text x="64" y="55" text-anchor="middle" fill="{accent}" font-size="22" font-family="Arial, sans-serif" font-weight="700">{safe_initials}</text>
      <text x="64" y="92" text-anchor="middle" fill="#D3E4F3" font-size="12" font-family="Arial, sans-serif">{safe_type}</text>
    </svg>
    """
    return svg_data_uri(svg)


@lru_cache(maxsize=64)
def fetch_remote_image_data_uri(image_url: str) -> str:
    if not image_url:
        return ""

    request = Request(
        image_url,
        headers={
            "User-Agent": "ChaosWing/0.1 (+https://polymarket.com)",
            "Accept": "image/*",
        },
    )

    with urlopen(request, timeout=settings.CHAOSWING_HTTP_TIMEOUT_SECONDS) as response:
        content_type = response.headers.get_content_type() or mimetypes.guess_type(image_url)[0]
        if not content_type or not content_type.startswith("image/"):
            return ""

        image_bytes = response.read(1_500_000 + 1)
        if len(image_bytes) > 1_500_000:
            return ""

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}"
