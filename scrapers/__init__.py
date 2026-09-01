"""One scraper per company.

Every module in this package exposes:

    fetch_jobs(zip_code=DEFAULT_ZIP, radius_miles=DEFAULT_RADIUS) -> list[dict]

returning dicts shaped like::

    {"id": ..., "title": ..., "location": ..., "url": ..., "company": ...}

`id` only has to be stable and unique within that company — it is the key
main.py uses to decide whether a listing is genuinely new.

A scraper narrows by location where its API supports it, but it is not
responsible for the final 15-mile rule: main.py geocodes every result and
measures the distance itself.
"""

from __future__ import annotations

import requests

DEFAULT_ZIP = "92606"
DEFAULT_RADIUS_MILES = 15

# Careers sites are ordinary consumer web apps; a browser-shaped User-Agent is
# what their APIs expect to see.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 45


def new_session() -> requests.Session:
    """A requests session with the shared browser-ish headers applied."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def miles_to_km(miles: float) -> float:
    return miles * 1.609344
