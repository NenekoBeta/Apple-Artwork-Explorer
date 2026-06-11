"""Apple TV URL parsing, UTS search, and artwork extraction helpers."""

import json
import re
from typing import Any, TypedDict
from urllib.parse import urlparse

import requests

from itunes_api import ITunesAPIError, TIMEOUT_SECONDS


APPLE_TV_USER_AGENT = "Mozilla/5.0 AppleMediaArtworkFinder/1.0"
APPLE_TV_CONTENT_ID_PATTERN = re.compile(r"(umc\.[a-z0-9]+\.[A-Za-z0-9]+)")
APPLE_TV_CONTENT_TYPES = {"show", "movie", "season", "episode"}
UTS_API_URL = "https://uts-api.itunes.apple.com/uts/v3"
UTS_DEFAULT_PATTERN = re.compile(r'"Default"\s*:\s*(\{[^{}]*"utsk"[^{}]*\})')
UTS_TYPES = {"movie": "movies", "show": "shows"}

ARTWORK_TARGETS = [
    ("CoverArt", ("posterArt",), "jpg", False, "poster"),
    ("CoverArtParallax", ("posterArt",), "lsr", True, "poster"),
    ("PreviewFrame", ("previewFrame", "previewFrameImage"), "jpg", False, "landscape"),
    ("SingleColorContentLogo", ("singleColorContentLogo", "contentLogo"), "png", False, "any"),
    ("FullColorContentLogo", ("fullColorContentLogo", "contentLogo"), "png", False, "any"),
    ("CenteredFullScreenBackgroundImage", ("contentImage",), "jpg", False, "any"),
    ("CenteredFullScreenBackgroundSmallImage", ("contentImageTall",), "jpg", False, "any"),
    ("FullScreenBackground", ("fullScreenBackground",), "jpg", False, "landscape"),
    ("BannerUberImage", ("bannerUberImage",), "jpg", False, "wide"),
    ("ContentLogo", ("contentLogo",), "jpg", False, "any"),
    ("CoverArt16X9", ("contentImage16X9", "posterArt"), "jpg", False, "landscape"),
    ("CoverArt16X9Parallax", ("contentImage16X9", "posterArt"), "lsr", True, "landscape"),
]


class AppleTVArtwork(TypedDict):
    """Artwork discovered from Apple TV UTS JSON."""

    type: str
    resolution: str
    format: str
    url: str
    preview_url: str | None
    available: bool


class AppleTVSeason(TypedDict):
    """Season metadata and artwork discovered from Apple TV UTS JSON."""

    id: str
    title: str
    season_number: int | None
    url: str
    artworks: list[AppleTVArtwork]


class AppleTVSearchResult(TypedDict):
    """Small Apple TV search result."""

    id: str
    title: str
    type: str
    url: str
    artwork_url: str | None


def parse_apple_tv_input(value: str) -> dict[str, Any]:
    """Parse a tv.apple.com URL or raw Apple TV content ID."""
    original = value.strip()
    content_id = extract_apple_tv_content_id(original)

    if not validate_apple_tv_url(original):
        return {
            "is_valid": False,
            "is_url": False,
            "original": original,
            "region": None,
            "content_type": None,
            "title_slug": None,
            "content_id": content_id,
            "error": "Please enter a full tv.apple.com title URL.",
        }

    return {
        "is_valid": True,
        "is_url": True,
        "original": original,
        "region": extract_apple_tv_region(original),
        "content_type": extract_apple_tv_content_type(original),
        "title_slug": extract_apple_tv_title_slug(original),
        "content_id": content_id,
        "error": None,
    }


def validate_apple_tv_url(url: str) -> bool:
    """Return True only for http(s) tv.apple.com URLs."""
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and "tv.apple.com" in parsed.netloc.lower()


def extract_apple_tv_content_id(value: str) -> str | None:
    """Return the first Apple TV umc.* content ID in a string."""
    match = APPLE_TV_CONTENT_ID_PATTERN.search(value.strip())
    return match.group(1) if match else None


def extract_apple_tv_region(url: str) -> str | None:
    """Extract storefront region from a tv.apple.com URL."""
    parts = _path_parts(url)
    if parts and len(parts[0]) == 2:
        return parts[0].upper()
    return None


def extract_apple_tv_content_type(url: str) -> str | None:
    """Extract Apple TV URL content type such as show or movie."""
    parts = _path_parts(url)
    for part in parts:
        if part in APPLE_TV_CONTENT_TYPES:
            return part
    return None


def extract_apple_tv_title_slug(url: str) -> str | None:
    """Extract title slug from a tv.apple.com URL."""
    parts = _path_parts(url)
    content_type = extract_apple_tv_content_type(url)
    if not content_type or content_type not in parts:
        return None

    index = parts.index(content_type)
    if len(parts) > index + 1:
        candidate = parts[index + 1]
        if not candidate.startswith("umc."):
            return candidate
    return None


def fetch_apple_tv_html(url: str) -> str:
    """Fetch a public Apple TV page."""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": APPLE_TV_USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise ITunesAPIError("The Apple TV page request timed out.") from exc
    except requests.RequestException as exc:
        raise ITunesAPIError("Could not fetch the Apple TV page.") from exc

    return response.text


def discover_apple_tv_from_url(url: str) -> dict[str, Any]:
    """Discover Apple TV UTS artwork from one URL."""
    metadata = parse_apple_tv_input(url)
    result: dict[str, Any] = {
        **metadata,
        "uts_artwork_count": 0,
        "uts_artworks": [],
        "season_count": 0,
        "seasons": [],
    }

    if not metadata["is_valid"] or not metadata["is_url"]:
        return result

    page_html = fetch_apple_tv_html(url)
    uts_artworks, seasons = discover_uts_details(
        page_html,
        str(metadata["content_id"]) if metadata["content_id"] else None,
        str(metadata["content_type"]) if metadata["content_type"] else None,
    )

    result["uts_artwork_count"] = len(uts_artworks)
    result["uts_artworks"] = uts_artworks
    result["season_count"] = len(seasons)
    result["seasons"] = seasons
    return result


def discover_uts_details(
    page_html: str,
    content_id: str | None,
    content_type: str | None,
) -> tuple[list[AppleTVArtwork], list[AppleTVSeason]]:
    """Discover main artwork and season artwork from the UTS API."""
    if not page_html or not content_id or content_type not in UTS_TYPES:
        return [], []

    config = extract_uts_config(page_html)
    if not config:
        return [], []

    payload = fetch_uts_payload(content_id, content_type, config)
    seasons = extract_seasons(payload)
    if content_type == "show":
        seasons = enrich_seasons(content_id, seasons, config)
    return extract_artwork(payload), seasons


def search_uts(page_html: str, term: str, content_type: str | None, limit: int) -> list[AppleTVSearchResult]:
    """Search Apple TV UTS for shows or movies by name."""
    config = extract_uts_config(page_html)
    if not config:
        return []

    try:
        response = requests.get(
            f"{UTS_API_URL}/search",
            params={**config, "searchTerm": term},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []

    return _extract_search_results(payload, content_type, limit)


def extract_uts_config(page_html: str) -> dict[str, str]:
    """Extract current UTS request parameters embedded in the Apple TV page."""
    match = UTS_DEFAULT_PATTERN.search(page_html)
    if not match:
        return {}

    try:
        config = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}

    required = {"caller", "locale", "pfm", "sf", "utscf", "utsk", "v"}
    if not required.issubset(config):
        return {}
    return {key: str(config[key]) for key in required}


def fetch_uts_payload(content_id: str, content_type: str, config: dict[str, str]) -> dict[str, Any]:
    """Fetch Apple TV UTS JSON for one movie or show."""
    endpoint = UTS_TYPES[content_type]
    try:
        response = requests.get(
            f"{UTS_API_URL}/{endpoint}/{content_id}",
            params=config,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout as exc:
        raise ITunesAPIError("The Apple TV UTS request timed out.") from exc
    except requests.RequestException as exc:
        raise ITunesAPIError("Could not reach the Apple TV UTS API.") from exc
    except ValueError as exc:
        raise ITunesAPIError("The Apple TV UTS API returned invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise ITunesAPIError("The Apple TV UTS API returned an unexpected response.")
    return payload


def fetch_uts_season_payload(show_id: str, season_id: str, config: dict[str, str]) -> dict[str, Any] | None:
    """Fetch detailed UTS JSON for one Apple TV show season."""
    try:
        response = requests.get(
            f"{UTS_API_URL}/shows/{show_id}/{season_id}",
            params=config,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def extract_artwork(payload: dict[str, Any]) -> list[AppleTVArtwork]:
    """Extract main content artwork from UTS JSON."""
    images = payload.get("data", {}).get("content", {}).get("images", {})
    if not isinstance(images, dict):
        return []
    return _build_artworks_from_images(images)


def extract_seasons(payload: dict[str, Any]) -> list[AppleTVSeason]:
    """Extract season summary rows and season artwork from UTS JSON."""
    seasons = payload.get("data", {}).get("seasons", {})
    if not isinstance(seasons, dict):
        return []

    rows = []
    for season in seasons.values():
        if not isinstance(season, dict):
            continue
        images = season.get("images", {})
        artworks = _build_artworks_from_images(images) if isinstance(images, dict) else []
        rows.append(
            {
                "id": str(season.get("id") or ""),
                "title": str(season.get("title") or "Untitled Season"),
                "season_number": season.get("seasonNumber") if isinstance(season.get("seasonNumber"), int) else None,
                "url": str(season.get("url") or ""),
                "artworks": artworks,
            }
        )

    return sorted(rows, key=lambda row: row["season_number"] or 0)


def enrich_seasons(show_id: str, seasons: list[AppleTVSeason], config: dict[str, str]) -> list[AppleTVSeason]:
    """Replace season summary artwork with richer season-detail artwork when available."""
    enriched = []
    for season in seasons:
        payload = fetch_uts_season_payload(show_id, season["id"], config)
        artworks = extract_artwork(payload) if payload else season["artworks"]
        enriched.append({**season, "artworks": artworks or season["artworks"]})
    return enriched


def _build_artworks_from_images(images: dict[str, Any]) -> list[AppleTVArtwork]:
    """Build available artwork objects from a UTS images dictionary."""
    artworks = []
    for label, keys, file_format, require_layered, shape in ARTWORK_TARGETS:
        image_data = _first_image(images, keys)
        artwork = _build_artwork(label, image_data, file_format, require_layered, shape)
        if artwork:
            artworks.append(artwork)
    artworks.extend(_square_artworks(images, {artwork["url"] for artwork in artworks}))
    return _dedupe_artworks(artworks)


def _build_artwork(
    label: str,
    image_data: Any,
    file_format: str,
    require_layered: bool,
    shape: str,
) -> AppleTVArtwork | None:
    """Build one artwork item from a UTS image object."""
    if not isinstance(image_data, dict):
        return None

    template = image_data.get("url")
    source_width = image_data.get("width")
    source_height = image_data.get("height")
    if not isinstance(template, str) or not isinstance(source_width, int) or not isinstance(source_height, int):
        return None
    if require_layered and not _supports_layered_image(image_data):
        return None
    if not _matches_shape(source_width, source_height, shape):
        return None

    url = _fill_image_template(template, source_width, source_height, file_format)
    return {
        "type": _display_type(label),
        "resolution": f"{source_width}x{source_height}",
        "format": _format_from_url(url, file_format),
        "url": url,
        "preview_url": None if file_format == "lsr" else _preview_url(template, source_width, source_height),
        "available": True,
    }


def _matches_shape(width: int, height: int, shape: str) -> bool:
    """Avoid turning one artwork shape into another."""
    ratio = width / height
    if shape == "square":
        return 0.95 <= ratio <= 1.05
    if shape == "poster":
        return ratio <= 1.05
    if shape == "landscape":
        return 1.3 <= ratio <= 2.2
    if shape == "wide":
        return ratio >= 2.5
    return True


def _square_artworks(images: dict[str, Any], seen_urls: set[str]) -> list[AppleTVArtwork]:
    """Expose native square artwork fields without inventing a square crop."""
    rows = []
    for image_data in images.values():
        artwork = _build_artwork("SquareArtwork", image_data, "jpg", False, "square")
        if artwork and artwork["url"] not in seen_urls:
            rows.append(artwork)
            seen_urls.add(artwork["url"])
    return rows


def _extract_search_results(
    payload: dict[str, Any],
    content_type: str | None,
    limit: int,
) -> list[AppleTVSearchResult]:
    """Extract compact search results from UTS search shelves."""
    wanted = {"show": "Show", "movie": "Movie"}.get(content_type)
    shelves = payload.get("data", {}).get("canvas", {}).get("shelves", [])
    if not isinstance(shelves, list):
        return []

    results: list[AppleTVSearchResult] = []
    seen = set()
    for shelf in shelves:
        if not isinstance(shelf, dict):
            continue
        items = shelf.get("items", [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            item_id = str(item.get("id") or "")
            if not item_id or item_id in seen or (wanted and item_type != wanted):
                continue
            seen.add(item_id)
            results.append(
                {
                    "id": item_id,
                    "title": str(item.get("title") or "Untitled"),
                    "type": item_type,
                    "url": str(item.get("url") or ""),
                    "artwork_url": _search_artwork_url(item.get("images", {})),
                }
            )
            if len(results) >= limit:
                return results
    return results


def _search_artwork_url(images: Any) -> str | None:
    """Return one lightweight poster URL for an Apple TV search result."""
    if not isinstance(images, dict):
        return None
    image = images.get("shelfItemImage") or images.get("contentImage")
    if not isinstance(image, dict):
        return None
    template = image.get("url")
    width = image.get("width")
    height = image.get("height")
    if not isinstance(template, str) or not isinstance(width, int) or not isinstance(height, int):
        return None
    return _fill_image_template(template, min(width, 600), min(height, 900), "jpg")


def _first_image(images: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first image object available for a target."""
    for key in keys:
        if key in images:
            return images[key]
    return None


def _supports_layered_image(image_data: dict[str, Any]) -> bool:
    """Return True when a UTS image object can plausibly emit layered/parallax data."""
    template = str(image_data.get("url") or "").lower()
    return bool(image_data.get("supportsLayeredImage")) or ".lsr/" in template


def _dedupe_artworks(artworks: list[AppleTVArtwork]) -> list[AppleTVArtwork]:
    """Preserve artwork order while removing duplicate labels and URLs."""
    seen = set()
    deduped = []
    for artwork in artworks:
        key = (artwork["type"], artwork["url"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(artwork)
    return deduped


def _fill_image_template(template: str, width: int, height: int, file_format: str) -> str:
    """Fill Apple image URL placeholders."""
    return (
        template.replace("{w}", str(width))
        .replace("{h}", str(height))
        .replace("{f}", file_format)
        .replace("{c}", "")
    )


def _format_from_url(url: str, fallback: str) -> str:
    """Infer the real file extension from the generated URL when possible."""
    matches = re.findall(r"\.(jpg|jpeg|png|webp|lsr)(?:[/?#]|$)", url, flags=re.IGNORECASE)
    if not matches:
        return fallback
    extension = matches[-1].lower()
    return "jpg" if extension == "jpeg" else extension


def _preview_url(template: str, width: int, height: int) -> str | None:
    """Build a small JPG preview URL from the source aspect ratio."""
    if width <= 0 or height <= 0:
        return None
    preview_width = min(width, 600)
    preview_height = max(1, round(height * preview_width / width))
    return _fill_image_template(template, preview_width, preview_height, "jpg")


def _display_type(image_type: str) -> str:
    """Convert UTS camelCase-like labels into readable text."""
    words = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", image_type)
    words = words.replace("16 X 9", "16:9").replace("16 X9", "16:9").replace("16X9", "16:9")
    words = words.replace("16:9Parallax", "16:9 Parallax")
    return words[:1].upper() + words[1:]


def _path_parts(url: str) -> list[str]:
    """Return URL path parts without empty segments."""
    parsed = urlparse(url.strip())
    return [part for part in parsed.path.split("/") if part]


def _dedupe(values: list[str]) -> list[str]:
    """Preserve order while removing duplicates."""
    seen = set()
    deduped = []
    for value in values:
        if value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped
