"""Best Buy — jobs.bestbuy.com

Best Buy's careers portal is a ServiceNow Service Portal, and its job list
comes from a widget endpoint that answers with GeoJSON:

    POST /api/now/sp/widget/<widget id>   {"action": "update_data", ...}

Two details make it work from a script:

* the endpoint wants ServiceNow's CSRF token (`g_ck`), which is embedded in
  the portal page, so we load the page first and reuse its session cookies;
* the location filter is not a radius parameter but an encoded bounding box
  in `options.filters.l`, which we build from the ZIP's coordinates. Best Buy
  exposes its own unauthenticated geocoder for that, so no third party is
  needed here.

The bounding box is a square around the search circle, so it returns slightly
more than asked for. main.py's haversine check trims the corners off.
"""

from __future__ import annotations

import logging
import math
import re

from . import DEFAULT_RADIUS_MILES, DEFAULT_ZIP, REQUEST_TIMEOUT, new_session

log = logging.getLogger(__name__)

COMPANY = "Best Buy"
PORTAL_URL = "https://jobs.bestbuy.com/bby?id=all_jobs"
GEOCODE_URL = "https://jobs.bestbuy.com/api/x_nero_bb_career/newrocket_all_jobs_service/nr/loc_coords"
WIDGET_URL = (
    "https://jobs.bestbuy.com/api/now/sp/widget/"
    "dd786d721b71b010b4c011f18c4bcb87?country=US&id=all_jobs&spa=1"
)
JOB_URL = "https://jobs.bestbuy.com/bby?id=job_details&sys_id={sys_id}"

TOKEN_PATTERN = re.compile(r"g_ck\s*=\s*['\"]([0-9a-zA-Z]{24,})['\"]")

# Miles per degree of latitude, matching the constant the site's own search uses.
MILES_PER_DEGREE = 69.0947


def _bounding_box(lat: float, lon: float, radius_miles: float) -> str:
    """The `longitudeBETWEEN...^latitudeBETWEEN...` filter the widget expects."""
    lat_delta = radius_miles / MILES_PER_DEGREE
    lon_delta = radius_miles / (MILES_PER_DEGREE * math.cos(math.radians(lat)))
    return (
        f"longitudeBETWEEN{lon - lon_delta}@{lon + lon_delta}"
        f"^latitudeBETWEEN{lat - lat_delta}@{lat + lat_delta}"
    )


def fetch_jobs(zip_code: str = DEFAULT_ZIP, radius_miles: int = DEFAULT_RADIUS_MILES):
    """Best Buy requisitions inside a box around `zip_code`."""
    session = new_session()

    page = session.get(PORTAL_URL, timeout=REQUEST_TIMEOUT)
    page.raise_for_status()
    match = TOKEN_PATTERN.search(page.text)
    if not match:
        raise RuntimeError("could not find the ServiceNow g_ck token on the portal page")
    token = match.group(1)

    coords = session.get(
        GEOCODE_URL,
        params={"term_type": "zip", "term_val": zip_code, "valid_input": "true"},
        timeout=REQUEST_TIMEOUT,
    )
    coords.raise_for_status()
    lon, lat = (float(value) for value in coords.json()["result"])

    response = session.post(
        WIDGET_URL,
        json={
            "action": "update_data",
            "options": {
                "items_per_page": 20,
                "current_page": 0,
                "sort": "distance",
                "sort_val": "distance",
                "radius": str(radius_miles),
                "filters": {
                    "country": "countrySTARTSWITHUS",
                    "l": _bounding_box(lat, lon, radius_miles),
                },
                "limitResults": "limitResults",
            },
        },
        headers={"Content-Type": "application/json", "X-UserToken": token},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    features = response.json()["result"]["data"]["items"]["features"]
    log.info("%s: API returned %d listings", COMPANY, len(features))

    jobs = []
    for feature in features:
        job = feature.get("properties") or {}
        sys_id = job.get("sys_id")
        title = (job.get("title") or "").strip()
        if not sys_id or not title:
            continue

        city = (job.get("city") or "").strip()
        state = (job.get("state") or "").strip()
        location = ", ".join(part for part in (city, state) if part)

        jobs.append(
            {
                "id": str(sys_id),
                "title": title,
                "location": location or "Unknown",
                "url": JOB_URL.format(sys_id=sys_id),
                "company": COMPANY,
            }
        )
    return jobs
