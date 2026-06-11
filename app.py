"""Simple Streamlit app for finding Apple media artwork."""

import re
import unicodedata
from typing import Any

import pandas as pd
import streamlit as st

from apple_tv import (
    AppleTVArtwork,
    AppleTVSearchResult,
    AppleTVSeason,
    discover_apple_tv_from_url,
    discover_uts_details,
    fetch_apple_tv_html,
    parse_apple_tv_input,
    search_uts,
)
from artwork import AUTO_SIZE, download_artwork, get_artwork_url
from itunes_api import ITunesAPIError, search_itunes


COUNTRY_CODES = {
    "United States": "US",
    "Japan": "JP",
    "China": "CN",
    "United Kingdom": "GB",
    "Hong Kong": "HK",
    "Taiwan": "TW",
    "South Korea": "KR",
    "Canada": "CA",
    "Australia": "AU",
    "France": "FR",
    "Germany": "DE",
    "Italy": "IT",
    "Spain": "ES",
    "Singapore": "SG",
}

MEDIA_OPTIONS = {
    "TV Show Seasons": {"media": "tvShow", "entity": "tvSeason"},
    "TV Episodes": {"media": "tvShow", "entity": "tvEpisode"},
    "Movie": {"media": "movie", "entity": "movie"},
    "Album (Apple Music)": {"media": "music", "entity": None},
    "Album (iTunes)": {"media": "music", "entity": "album"},
    "Podcast": {"media": "podcast", "entity": "podcast"},
    "App": {"media": "software", "entity": "software"},
    "Audiobook": {"media": "audiobook", "entity": "audiobook"},
    "Book": {"media": "ebook", "entity": "ebook"},
    "Music Video": {"media": "musicVideo", "entity": "musicVideo"},
    "Short Film": {"media": "shortFilm", "entity": "shortFilm"},
}

APPLE_TV_TYPES = {"TV Show": "show", "Movie": "movie"}
SEARCH_MODES = ["Keyword Search", "Apple TV Search", "Apple TV URL Lookup"]
LIMIT_OPTIONS = list(range(5, 55, 5))


st.set_page_config(page_title="Apple Media Artwork Finder", page_icon="A", layout="wide")

def initialize_state() -> None:
    """Initialize Streamlit session state."""
    defaults = {
        "results": [],
        "uts_artworks": [],
        "apple_tv_search_results": [],
        "apple_tv_seasons": [],
        "apple_tv_metadata": {},
        "apple_tv_cache": {},
        "search_note": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def title_for(result: dict[str, Any]) -> str:
    """Return the best display title from an Apple result."""
    return result.get("_title") or result.get("trackName") or result.get("collectionName") or result.get("artistName") or "Untitled"


def apple_link_for(result: dict[str, Any]) -> str | None:
    """Return an Apple/iTunes URL when present."""
    return result.get("trackViewUrl") or result.get("collectionViewUrl") or result.get("artistViewUrl")


def format_date(value: str | None) -> str:
    """Format an Apple release date."""
    return value.split("T", maxsplit=1)[0] if value else "Unknown"


def safe_filename(title: str, index: int, extension: str) -> str:
    """Create a safe filename for downloads."""
    clean = "".join(char for char in title if char.isalnum() or char in (" ", "-", "_")).strip()
    return f"{clean or 'artwork'}-{index}.{extension}"


def fallback_search_query(query: str) -> str:
    """Create a simpler search query for punctuation/accent-sensitive Apple searches."""
    without_accents = "".join(
        char for char in unicodedata.normalize("NFKD", query) if not unicodedata.combining(char)
    )
    simplified = re.sub(r"[^\w\s-]", " ", without_accents, flags=re.UNICODE)
    return re.sub(r"\s+", " ", simplified).strip()


def rank_results(query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank Apple results by local title/artist token matches while preserving API order ties."""
    tokens = fallback_search_query(query).lower().split()

    def score(result: dict[str, Any]) -> int:
        haystack = " ".join(
            str(result.get(key) or "") for key in ("trackName", "collectionName", "artistName", "sellerName")
        )
        normalized = fallback_search_query(haystack).lower()
        return sum(1 for token in tokens if token in normalized)

    return sorted(results, key=score, reverse=True)


def album_results_from_music(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build album-like results from Apple music search rows."""
    albums = []
    seen = set()
    for result in results:
        collection_id = result.get("collectionId")
        collection_name = result.get("collectionName")
        if not collection_id or not collection_name or collection_id in seen:
            continue
        seen.add(collection_id)
        albums.append({**result, "_title": collection_name, "_mediaType": "Album (Apple Music)"})
    return albums


def render_result(result: dict[str, Any], index: int) -> None:
    """Render one iTunes Search/Lookup result."""
    title = title_for(result)
    artwork_url = get_artwork_url(result.get("artworkUrl100"), AUTO_SIZE)

    with st.expander(f"{index}. {title}", expanded=index <= 2):
        if artwork_url:
            st.image(artwork_url, width=240)
        else:
            st.warning("Artwork not found.")

        st.info(f"Title: {title}")
        st.info(f"Artist / Studio: {result.get('artistName') or result.get('sellerName') or 'Unknown'}")
        st.info(f"Release Date: {format_date(result.get('releaseDate'))}")
        st.info(f"Media Type: {result.get('_mediaType') or result.get('kind') or result.get('wrapperType') or 'Unknown'}")

        if result.get("collectionName") and result.get("collectionName") != title:
            st.info(f"Collection: {result.get('collectionName')}")
        if result.get("trackId"):
            st.info(f"Track ID: {result.get('trackId')}")
        if result.get("collectionId"):
            st.info(f"Collection ID: {result.get('collectionId')}")

        link = apple_link_for(result)
        if link:
            st.info(f"Apple/iTunes link: {link}")

        render_artwork_download(result, title, index)


def render_artwork_download(result: dict[str, Any], title: str, index: int) -> None:
    """Render artwork download button using automatic highest available size."""
    try:
        image_bytes, content_type, downloaded_url = download_artwork(result.get("artworkUrl100"), AUTO_SIZE)
    except ITunesAPIError as exc:
        st.warning(f"Artwork download failed: {exc}")
        return

    extension = "png" if "png" in content_type else "jpg"
    st.download_button(
        "Download Artwork",
        data=image_bytes,
        file_name=safe_filename(title, index, extension),
        mime=content_type,
        key=f"artwork-{index}-{downloaded_url}",
    )


def search_keyword(query: str, country: str, media_label: str, limit: int) -> None:
    """Run iTunes keyword search."""
    media = MEDIA_OPTIONS[media_label]
    st.session_state.search_note = ""
    st.session_state.apple_tv_search_results = []

    def run(term: str, entity: str | None) -> list[dict[str, Any]]:
        return search_itunes(term, country=country, media=media["media"], entity=entity, limit=limit)

    try:
        if media_label == "Album (Apple Music)":
            st.session_state.results = album_results_from_music(run(query, None))
        else:
            st.session_state.results = run(query, media["entity"])
        fallback_query = fallback_search_query(query)
        if not st.session_state.results and fallback_query and fallback_query != query:
            if media_label == "Album (Apple Music)":
                st.session_state.results = album_results_from_music(run(fallback_query, None))
            else:
                st.session_state.results = run(fallback_query, media["entity"])
            if st.session_state.results:
                st.session_state.search_note = f"No exact results. Retried with: {fallback_query}"

        st.session_state.results = rank_results(query, st.session_state.results)
    except ITunesAPIError as exc:
        st.warning(f"Search failed: {exc}")
        st.session_state.results = []


def search_apple_tv(query: str, country: str, content_type: str, limit: int) -> None:
    """Run Apple TV UTS name search for shows or movies."""
    try:
        config_html = fetch_apple_tv_html(f"https://tv.apple.com/{country.lower()}")
    except ITunesAPIError as exc:
        st.warning(f"Apple TV search failed: {exc}")
        return

    st.session_state.apple_tv_search_results = search_uts(config_html, query, content_type, limit)
    if not st.session_state.apple_tv_search_results:
        st.warning("No Apple TV result found.")
        st.info(
            "You can try:\n"
            "1. Search the title as Movie or TV Show Seasons\n"
            "2. Change country\n"
            "3. Paste the exact tv.apple.com title URL"
        )


def cached_uts_details(country: str, content_id: str, content_type: str) -> tuple[list[AppleTVArtwork], list[AppleTVSeason]]:
    """Return cached Apple TV UTS artwork or fetch it once."""
    cache_key = f"{country}:{content_type}:{content_id}"
    cache = st.session_state.setdefault("apple_tv_cache", {})
    if cache_key in cache:
        return cache[cache_key]["artworks"], cache[cache_key]["seasons"]

    config_html = fetch_apple_tv_html(f"https://tv.apple.com/{country.lower()}")
    artworks, seasons = discover_uts_details(config_html, content_id, content_type)
    cache[cache_key] = {"artworks": artworks, "seasons": seasons}
    return artworks, seasons


def lookup_apple_tv_url(value: str) -> None:
    """Parse an Apple TV URL and discover artwork."""
    metadata = parse_apple_tv_input(value)
    st.session_state.apple_tv_metadata = metadata
    st.session_state.uts_artworks = []
    st.session_state.apple_tv_seasons = []

    if not metadata["is_valid"]:
        st.warning(str(metadata["error"]))
        return
    if not metadata["is_url"]:
        st.warning("Please paste the full tv.apple.com title URL.")
        return

    try:
        debug = discover_apple_tv_from_url(value)
    except ITunesAPIError as exc:
        st.warning(f"Apple TV lookup failed: {exc}")
        return

    st.session_state.uts_artworks = debug.get("uts_artworks", [])
    st.session_state.apple_tv_seasons = debug.get("seasons", [])
    if not st.session_state.uts_artworks and not st.session_state.apple_tv_seasons:
        st.warning("No public Apple TV artwork was discovered from this URL.")
    st.session_state.apple_tv_metadata = {
        **metadata,
        "uts_artwork_count": debug.get("uts_artwork_count", 0),
        "season_count": debug.get("season_count", 0),
    }


def render_apple_tv_metadata(metadata: dict[str, Any]) -> None:
    """Render Apple TV URL metadata."""
    if not metadata:
        return

    st.info("Apple TV Metadata")
    st.dataframe(
        pd.DataFrame(
            [
                {"Field": "Original Input", "Value": metadata.get("original") or ""},
                {"Field": "Region", "Value": metadata.get("region") or "Unavailable"},
                {"Field": "Type", "Value": metadata.get("content_type") or "Unavailable"},
                {"Field": "Title Slug", "Value": metadata.get("title_slug") or "Unavailable"},
                {"Field": "Apple TV Content ID", "Value": metadata.get("content_id") or "Unavailable"},
                {"Field": "Main Artwork Count", "Value": metadata.get("uts_artwork_count", 0)},
                {"Field": "Season Count", "Value": metadata.get("season_count", 0)},
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )


def render_main_artwork(artworks: list[AppleTVArtwork]) -> None:
    """Render Apple TV main artwork."""
    if not artworks:
        return

    st.info("Main Artwork")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Artwork Type": artwork["type"],
                    "Status": "Found" if artwork["available"] else "Unavailable",
                    "Resolution": artwork["resolution"],
                    "Format": artwork["format"],
                }
                for artwork in artworks
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    for index, artwork in enumerate(artworks, start=1):
        if not artwork["available"]:
            continue
        with st.expander(f"{artwork['type']} ({artwork['resolution']})", expanded=index == 1):
            if artwork["format"] == "lsr":
                st.info("Parallax .lsr files cannot be previewed here.")
            elif artwork.get("preview_url"):
                st.image(artwork["preview_url"], width=320)

            st.link_button("Open / Download Original Size", artwork["url"])


def render_seasons(seasons: list[AppleTVSeason]) -> None:
    """Render Apple TV season summary and per-season artwork downloads."""
    if not seasons:
        return

    st.info("Season Summary")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Season": season["season_number"] or "",
                    "Title": season["title"],
                    "Content ID": season["id"],
                    "Artwork Count": len(season["artworks"]),
                    "URL": season["url"],
                }
                for season in seasons
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    for season_index, season in enumerate(seasons, start=1):
        with st.expander(f"{season['title']} Artwork"):
            if not season["artworks"]:
                st.warning("No season artwork found.")
                continue
            for artwork_index, artwork in enumerate(season["artworks"], start=1):
                st.info(f"{artwork['type']} - {artwork['resolution']} {artwork['format']}")
                if artwork["format"] == "lsr":
                    st.info("Parallax .lsr files cannot be previewed here.")
                elif artwork.get("preview_url"):
                    st.image(artwork["preview_url"], width=260)

                st.link_button("Open / Download Original Size", artwork["url"])


def render_apple_tv_search_results(results: list[AppleTVSearchResult], country: str, content_type: str) -> None:
    """Render Apple TV name search results."""
    if not results:
        return

    st.success(f"Found {len(results)} Apple TV result{'s' if len(results) != 1 else ''}.")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Title": result["title"],
                    "Type": result["type"],
                    "Content ID": result["id"],
                    "URL": result["url"],
                }
                for result in results
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    for index, result in enumerate(results, start=1):
        with st.expander(f"{index}. {result['title']}", expanded=index == 1):
            if result["artwork_url"]:
                st.image(result["artwork_url"], width=220)
            st.info(f"Type: {result['type']}")
            st.info(f"Content ID: {result['id']}")
            st.info(f"URL: {result['url']}")
            if st.button("Load Apple TV Artwork", key=f"load-apple-tv-{result['id']}"):
                try:
                    st.session_state.uts_artworks, st.session_state.apple_tv_seasons = cached_uts_details(
                        country,
                        result["id"],
                        content_type,
                    )
                except ITunesAPIError as exc:
                    st.warning(f"Apple TV artwork lookup failed: {exc}")
                    return
                st.session_state.apple_tv_metadata = {
                    "original": result["url"],
                    "region": country,
                    "content_type": content_type,
                    "title_slug": "",
                    "content_id": result["id"],
                    "uts_artwork_count": len(st.session_state.uts_artworks),
                    "season_count": len(st.session_state.apple_tv_seasons),
                }


initialize_state()
st.info("Apple Media Artwork Finder")
st.info(
    "Keyword Search: iTunes / Apple Music / App Store / podcasts / TV seasons. "
    "Apple TV Search: Apple TV catalog search when available. "
    "Apple TV URL Lookup: best for exact Apple TV artwork extraction."
)

with st.sidebar:
    search_mode = st.selectbox("Search Mode", SEARCH_MODES)
    country_name = st.selectbox("Country", list(COUNTRY_CODES.keys()))

    media_label = ""
    apple_tv_type_label = "TV Show"
    if search_mode == "Keyword Search":
        media_label = st.selectbox("Media Type", list(MEDIA_OPTIONS.keys()))
        limit = st.selectbox("Limit", LIMIT_OPTIONS, index=3)
        query = st.text_input("Keyword", placeholder="Title, artist, app...")
    elif search_mode == "Apple TV Search":
        apple_tv_type_label = st.selectbox("Apple TV Type", list(APPLE_TV_TYPES.keys()))
        limit = st.selectbox("Limit", LIMIT_OPTIONS, index=3)
        query = st.text_input("Apple TV Keyword", placeholder="Title...")
        st.info("Keyword search may not find all third-party or region-limited Apple TV titles.")
    else:
        query = st.text_input("Apple TV URL", placeholder="https://tv.apple.com/us/show/...")
        st.info("Apple TV artwork extraction works best with a full tv.apple.com title URL.")

    search_clicked = st.button("Search")

if search_clicked:
    cleaned_query = query.strip()
    st.session_state.results = []
    st.session_state.uts_artworks = []
    st.session_state.apple_tv_search_results = []
    st.session_state.apple_tv_seasons = []
    st.session_state.apple_tv_metadata = {}
    st.session_state.search_note = ""

    if not cleaned_query:
        st.warning("Please enter a search value.")
    elif search_mode == "Keyword Search":
        search_keyword(cleaned_query, COUNTRY_CODES[country_name], media_label, limit)
    elif search_mode == "Apple TV Search":
        search_apple_tv(cleaned_query, COUNTRY_CODES[country_name], APPLE_TV_TYPES[apple_tv_type_label], limit)
    else:
        lookup_apple_tv_url(cleaned_query)

if search_mode == "Apple TV URL Lookup":
    render_apple_tv_metadata(st.session_state.apple_tv_metadata)
    render_main_artwork(st.session_state.uts_artworks)
    render_seasons(st.session_state.apple_tv_seasons)
elif st.session_state.apple_tv_search_results:
    render_apple_tv_search_results(
        st.session_state.apple_tv_search_results,
        COUNTRY_CODES[country_name],
        APPLE_TV_TYPES[apple_tv_type_label],
    )
    render_apple_tv_metadata(st.session_state.apple_tv_metadata)
    render_main_artwork(st.session_state.uts_artworks)
    render_seasons(st.session_state.apple_tv_seasons)
elif st.session_state.results:
    if st.session_state.search_note:
        st.warning(st.session_state.search_note)
    st.success(f"Found {len(st.session_state.results)} result{'s' if len(st.session_state.results) != 1 else ''}.")
    for result_index, result in enumerate(st.session_state.results, start=1):
        render_result(result, result_index)
elif search_clicked and query.strip() and search_mode == "Keyword Search":
    st.warning("No results found.")

st.markdown("---")

st.caption(
    "Apple Media Artwork Finder Educational Project"
)

st.caption(
    "Disclaimer: This project is an independent educational and research tool and is not affiliated with or endorsed by Apple Inc. All trademarks, artwork, logos, and media assets belong to their respective copyright owners. The application retrieves publicly accessible metadata and image resources and does not host or redistribute copyrighted audiovisual content."
)

st.caption("(C) 2026 Apple Media Artwork Finder Personal Project, Not affiliated with Apple Inc.")