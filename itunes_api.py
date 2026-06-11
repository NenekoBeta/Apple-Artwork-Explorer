"""iTunes Search and Lookup API client."""

from typing import Any

import requests


ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
TIMEOUT_SECONDS = 12


class ITunesAPIError(Exception):
    """Raised when Apple API or artwork requests fail."""


def search_itunes(
    keyword: str,
    country: str = "US",
    media: str = "movie",
    limit: int = 20,
    entity: str | None = None,
) -> list[dict[str, Any]]:
    """Search Apple media through the public iTunes Search API."""
    params: dict[str, Any] = {
        "term": keyword,
        "country": country,
        "media": media,
        "limit": limit,
    }
    if entity is not None:
        params["entity"] = entity

    payload = _get_json(ITUNES_SEARCH_URL, params)
    return _extract_results(payload)


def _get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Fetch JSON from an Apple API endpoint with consistent errors."""
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout as exc:
        raise ITunesAPIError("The Apple API request timed out.") from exc
    except requests.RequestException as exc:
        raise ITunesAPIError("Could not reach the Apple API.") from exc
    except ValueError as exc:
        raise ITunesAPIError("The Apple API returned invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise ITunesAPIError("The Apple API returned an unexpected response.")
    return payload


def _extract_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the results list or raise when the response is malformed."""
    results = payload.get("results")
    if not isinstance(results, list):
        raise ITunesAPIError("The Apple API response did not include results.")
    return results
