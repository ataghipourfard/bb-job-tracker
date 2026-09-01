"""Turn a job's location string into coordinates, and coordinates into miles.

Job boards hand back locations as text ("TUSTIN, California", "Irvine, CA").
This module normalises that text, geocodes it against OpenStreetMap's free
Nominatim service, and caches every answer in geocache.json so that a run
that has seen a city before never touches the network again.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time

import requests

log = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "geocache.json")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy asks for an identifying User-Agent and at most one
# request per second. The cache means we rarely make any at all.
USER_AGENT = "bb-job-tracker/1.0 (+https://github.com/topics/job-scraper)"
MIN_SECONDS_BETWEEN_LOOKUPS = 1.1
REQUEST_TIMEOUT = 20

EARTH_RADIUS_MILES = 3958.7613

# A location containing any of these is treated as fully remote and is alerted
# on no matter how far away its nominal office is.
REMOTE_MARKERS = (
    "remote",
    "work from home",
    "home office",
    "telecommute",
    "virtual",
    "anywhere",
)

STATE_ABBREVIATIONS = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "puerto rico": "PR",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}

_cache: dict | None = None
_cache_dirty = False
_last_lookup_at = 0.0


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #

def load_cache() -> dict:
    """Read geocache.json, tolerating a missing or corrupt file."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(CACHE_FILE, encoding="utf-8") as fh:
            _cache = json.load(fh)
        if not isinstance(_cache, dict):
            raise ValueError("geocache.json is not an object")
    except FileNotFoundError:
        _cache = {}
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("geocache.json unreadable (%s); starting a fresh cache", exc)
        _cache = {}
    return _cache


def save_cache() -> None:
    """Write the cache back out, but only if something actually changed."""
    global _cache_dirty
    if _cache is None or not _cache_dirty:
        return
    with open(CACHE_FILE, "w", encoding="utf-8") as fh:
        json.dump(_cache, fh, indent=2, sort_keys=True)
        fh.write("\n")
    _cache_dirty = False


# --------------------------------------------------------------------------- #
# text handling
# --------------------------------------------------------------------------- #

def normalize_location(text: str) -> str:
    """Tidy a location string: "TUSTIN, California" -> "Tustin, CA"."""
    if not text:
        return ""
    parts = [part.strip() for part in text.split(",") if part.strip()]
    tidied = []
    for part in parts:
        state = STATE_ABBREVIATIONS.get(part.lower())
        if state:
            tidied.append(state)
        elif len(part) == 2 and part.isalpha():
            tidied.append(part.upper())
        elif part.isupper() or part.islower():
            # All-caps or all-lowercase city names read badly in an alert.
            tidied.append(part.title())
        else:
            tidied.append(part)
    return ", ".join(tidied)


def is_remote(text: str) -> bool:
    """True when a location string describes a fully-remote role."""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in REMOTE_MARKERS)


# --------------------------------------------------------------------------- #
# geocoding
# --------------------------------------------------------------------------- #

def _nominatim(params: dict) -> tuple[float, float] | None:
    """One rate-limited Nominatim call. Raises on network trouble."""
    global _last_lookup_at
    wait = MIN_SECONDS_BETWEEN_LOOKUPS - (time.monotonic() - _last_lookup_at)
    if wait > 0:
        time.sleep(wait)
    _last_lookup_at = time.monotonic()

    response = requests.get(
        NOMINATIM_URL,
        params={"format": "json", "limit": 1, "countrycodes": "us", **params},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


def _lookup(cache_key: str, params: dict) -> tuple[float, float] | None:
    """Geocode via the cache, falling through to Nominatim on a miss.

    A place Nominatim genuinely does not know is cached as null so we stop
    asking. A network failure is *not* cached, so the next run retries.
    """
    global _cache_dirty
    cache = load_cache()
    if cache_key in cache:
        hit = cache[cache_key]
        return (hit[0], hit[1]) if hit else None

    try:
        coords = _nominatim(params)
    except (requests.RequestException, ValueError, KeyError) as exc:
        log.warning("geocoding %r failed (%s); will retry next run", cache_key, exc)
        return None

    cache[cache_key] = list(coords) if coords else None
    _cache_dirty = True
    if coords is None:
        log.info("geocoder does not recognise %r; jobs there will be skipped", cache_key)
    return coords


def geocode_place(location: str) -> tuple[float, float] | None:
    """Coordinates for a "City, ST" style string, or None."""
    place = normalize_location(location)
    if not place:
        return None
    return _lookup(place.lower(), {"q": f"{place}, USA"})


def geocode_zip(zip_code: str) -> tuple[float, float] | None:
    """Coordinates for a US ZIP code, or None."""
    zip_code = str(zip_code).strip()
    if not zip_code:
        return None
    return _lookup(f"zip:{zip_code}", {"postalcode": zip_code, "country": "us"})


# --------------------------------------------------------------------------- #
# distance
# --------------------------------------------------------------------------- #

def haversine_miles(origin: tuple[float, float], point: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) pairs, in miles."""
    lat1, lon1 = math.radians(origin[0]), math.radians(origin[1])
    lat2, lon2 = math.radians(point[0]), math.radians(point[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def distance_miles(origin: tuple[float, float], location: str) -> float | None:
    """Miles from `origin` to a location string, or None if it won't geocode."""
    coords = geocode_place(location)
    if coords is None:
        return None
    return haversine_miles(origin, coords)
