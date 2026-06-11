"""Artwork URL conversion and download helpers."""

import re

import requests

from itunes_api import ITunesAPIError, TIMEOUT_SECONDS


ARTWORK_SIZE_PATTERN = re.compile(r"/(?P<size>\d+x\d+)bb\.(?P<ext>jpg|jpeg|png)$", re.IGNORECASE)
AUTO_SIZE = "Auto (Highest Available)"
FALLBACK_SIZES = ["1200x1200", "1000x1000", "600x600"]


def build_artwork_candidates(artwork_url: str | None, selected_size: str) -> list[str]:
    """Build candidate artwork URLs in the order they should be attempted."""
    if not artwork_url:
        return []

    if selected_size == AUTO_SIZE:
        sizes = ["3000x3000", *FALLBACK_SIZES]
    else:
        sizes = [selected_size, *FALLBACK_SIZES]

    candidates = [_resize_artwork_url(artwork_url, size) for size in _dedupe(sizes)]
    candidates.append(artwork_url)
    return _dedupe([candidate for candidate in candidates if candidate])


def get_artwork_url(artwork_url: str | None, selected_size: str) -> str | None:
    """Return a display URL candidate without making a network request."""
    candidates = build_artwork_candidates(artwork_url, selected_size)
    return candidates[0] if candidates else None


def download_artwork(artwork_url: str | None, selected_size: str) -> tuple[bytes, str, str]:
    """Download artwork, falling back through smaller sizes and the original URL."""
    for candidate in build_artwork_candidates(artwork_url, selected_size):
        try:
            response = requests.get(candidate, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.RequestException:
            continue

        content_type = response.headers.get("content-type", "image/jpeg").split(";", maxsplit=1)[0]
        return response.content, content_type, candidate

    raise ITunesAPIError("Could not download this artwork image.")


def _resize_artwork_url(artwork_url: str, size: str) -> str:
    """Replace an Apple artwork size segment while preserving the extension."""
    return ARTWORK_SIZE_PATTERN.sub(f"/{size}bb.\\g<ext>", artwork_url)


def _dedupe(values: list[str]) -> list[str]:
    """Preserve order while removing duplicates."""
    seen = set()
    deduped = []
    for value in values:
        if value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped
