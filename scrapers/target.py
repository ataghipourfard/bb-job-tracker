"""Target — corporate.target.com/careers/job-search

The search page is backed by a plain JSON endpoint:

    POST /api/jobsearch     (form-encoded, not JSON)

It takes the search centre as lat/lon and the radius in *kilometres*, and
answers with a total count plus one page of results. Each result carries
city, state and a relative job URL.

The default page size is 15, but an undocumented `pagesize` is honoured up
to 200 — past that the API silently reverts to 15. Asking for 200 returns
every listing in range in a single request instead of eleven. Results are
not date-ordered, so there is nothing to stop early on; the paging loop
below only earns its keep if a search ever exceeds 200 listings.
"""

from __future__ import annotations

import logging

from . import (
    DEFAULT_RADIUS_MILES,
    DEFAULT_ZIP,
    REQUEST_TIMEOUT,
    home_coordinates,
    miles_to_km,
    new_session,
)

log = logging.getLogger(__name__)

COMPANY = "Target"
SEARCH_URL = "https://corporate.target.com/api/jobsearch"
SITE_URL = "https://corporate.target.com"
# 200 is the highest the API honours; above it, it quietly falls back to 15.
RESULTS_PER_PAGE = 200

# A guard against paging forever if the API ever stops shrinking its result set.
MAX_PAGES = 20


def fetch_jobs(zip_code: str = DEFAULT_ZIP, radius_miles: int = DEFAULT_RADIUS_MILES,
               known_ids: set | None = None):
    """Target openings within `radius_miles` of `zip_code`.

    Results are not date-ordered, so `known_ids` cannot shorten the walk.
    """
    session = new_session()
    lat, lon = home_coordinates(zip_code)

    jobs, page, fetched = [], 1, 0
    while page <= MAX_PAGES:
        response = session.post(
            SEARCH_URL,
            data={
                "lat": lat,
                "lon": lon,
                "maxdistance": f"{miles_to_km(radius_miles):.3f}",
                "currentPage": page,
                "pagesize": RESULTS_PER_PAGE,
                "culture": "en-us",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{SITE_URL}/careers/job-search",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        fetched += 1

        results = payload.get("results") or []
        if not results:
            break

        for result in results:
            job = result.get("document") or {}
            job_id = job.get("sys_id") or job.get("postingid")
            title = (job.get("title") or "").strip()
            if not job_id or not title:
                continue

            if job.get("remotetype"):
                location = f"Remote ({job['remotetype']})"
            else:
                city = (job.get("city") or "").strip()
                state = (job.get("stateabbreviated") or job.get("state") or "").strip()
                location = ", ".join(part for part in (city, state) if part)

            path = job.get("url") or ""
            jobs.append(
                {
                    "id": str(job_id),
                    "title": title,
                    "location": location or "Unknown",
                    "url": f"{SITE_URL}{path}" if path else f"{SITE_URL}/careers/job-search",
                    "company": COMPANY,
                }
            )

        total = payload.get("count") or 0
        if page * RESULTS_PER_PAGE >= total:
            break
        page += 1

    log.info("%s: collected %d listings in %d request(s)", COMPANY, len(jobs), fetched)
    return jobs
