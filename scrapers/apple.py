"""Apple — jobs.apple.com

Apple's job search is a JSON API guarded by a CSRF token:

    GET  /api/v1/CSRFToken      -> token in the X-Apple-CSRF-Token header
    POST /api/v1/search         -> 20 results per page

Its location filter only accepts Apple's own place IDs, and there is no
radius option, so we ask for the metro area that contains Irvine
(`postLocation-LAMETRO`, which covers Los Angeles and Orange County) sorted
newest-first, and let main.py's distance check narrow that to 15 miles.

Two quirks worth knowing about:

* results give a city name but no state, so SEARCH_REGION_STATE is appended —
  correct as long as SEARCH_LOCATIONS stays inside one state;
* Apple Retail store roles are posted as a single nationwide pipeline
  requisition rather than per store, so they arrive located at "United
  States" instead of a city. main.py's INCLUDE_NATIONWIDE decides whether
  those are worth alerting on.
"""

from __future__ import annotations

import logging

from . import DEFAULT_RADIUS_MILES, DEFAULT_ZIP, REQUEST_TIMEOUT, new_session

log = logging.getLogger(__name__)

COMPANY = "Apple"
SEARCH_PAGE = "https://jobs.apple.com/en-us/search"
TOKEN_URL = "https://jobs.apple.com/api/v1/CSRFToken"
SEARCH_URL = "https://jobs.apple.com/api/v1/search"
JOB_URL = "https://jobs.apple.com/en-us/details/{position_id}/{slug}"

# Apple place IDs to search. Add more (they appear in the ?location= slug on
# jobs.apple.com, e.g. "irvine-IRV" -> "postLocation-IRV") to widen the net.
SEARCH_LOCATIONS = ["postLocation-LAMETRO"]
SEARCH_REGION_STATE = "CA"

RESULTS_PER_PAGE = 20
MAX_PAGES = 25

# Apple marks nationwide postings at location level 1 ("United States").
NATIONWIDE_LEVEL = 1


def _describe_location(job: dict) -> str:
    """Best available location text for a posting."""
    if job.get("homeOffice"):
        return "Remote"

    locations = job.get("locations") or []
    if not locations:
        return "Unknown"

    # Prefer the most specific place that is still a real city: Apple nests
    # store names (level 6) under cities (level 5) under metros (level 4).
    city = next((loc for loc in locations if loc.get("level") == 5), None)
    place = city or max(locations, key=lambda loc: loc.get("level") or 0)

    name = (place.get("name") or place.get("city") or "").strip()
    if not name:
        return "Unknown"
    if (place.get("level") or 0) <= NATIONWIDE_LEVEL:
        return name
    return f"{name}, {SEARCH_REGION_STATE}"


def fetch_jobs(zip_code: str = DEFAULT_ZIP, radius_miles: int = DEFAULT_RADIUS_MILES):
    """Apple openings in the metro areas listed in SEARCH_LOCATIONS."""
    session = new_session()

    # The token is only issued to a session that has loaded the search page.
    session.get(SEARCH_PAGE, timeout=REQUEST_TIMEOUT).raise_for_status()
    token_response = session.get(TOKEN_URL, timeout=REQUEST_TIMEOUT)
    token_response.raise_for_status()
    token = token_response.headers.get("X-Apple-CSRF-Token")
    if not token:
        raise RuntimeError("Apple did not return an X-Apple-CSRF-Token header")

    jobs, page = [], 1
    while page <= MAX_PAGES:
        response = session.post(
            SEARCH_URL,
            json={
                "query": "",
                "filters": {"locations": SEARCH_LOCATIONS},
                "page": page,
                "locale": "en-us",
                "sort": "newest",
                "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"},
            },
            headers={
                "Content-Type": "application/json",
                "X-Apple-CSRF-Token": token,
                "Referer": SEARCH_PAGE,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json().get("res") or {}

        results = payload.get("searchResults") or []
        if not results:
            break

        for job in results:
            position_id = job.get("positionId") or job.get("id")
            title = (job.get("postingTitle") or "").strip()
            if not position_id or not title:
                continue

            slug = job.get("transformedPostingTitle") or "role"
            jobs.append(
                {
                    "id": str(job.get("id") or position_id),
                    "title": title,
                    "location": _describe_location(job),
                    "url": JOB_URL.format(position_id=position_id, slug=slug),
                    "company": COMPANY,
                }
            )

        total = payload.get("totalRecords") or 0
        if page * RESULTS_PER_PAGE >= total:
            break
        page += 1

    log.info("%s: collected %d listings over %d page(s)", COMPANY, len(jobs), page)
    return jobs
