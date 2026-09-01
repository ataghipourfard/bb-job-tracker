"""Target — corporate.target.com/careers/job-search

The search page is backed by a plain JSON endpoint:

    POST /api/jobsearch     (form-encoded, not JSON)

It takes the search centre as lat/lon and the radius in *kilometres*, and
answers with 15 results per page plus a total count, so we page until the
count is covered. Each result carries city, state and a relative job URL.

Target does not geocode a bare ZIP for us, so the centre comes from Best
Buy's public ZIP geocoder — see scrapers/bestbuy.py.
"""

from __future__ import annotations

import logging

from . import (
    DEFAULT_RADIUS_MILES,
    DEFAULT_ZIP,
    REQUEST_TIMEOUT,
    miles_to_km,
    new_session,
)
from .bestbuy import GEOCODE_URL

log = logging.getLogger(__name__)

COMPANY = "Target"
SEARCH_URL = "https://corporate.target.com/api/jobsearch"
SITE_URL = "https://corporate.target.com"
RESULTS_PER_PAGE = 15

# A guard against paging forever if the API ever stops shrinking its result set.
MAX_PAGES = 60


def _home_coordinates(session, zip_code: str) -> tuple[float, float]:
    response = session.get(
        GEOCODE_URL,
        params={"term_type": "zip", "term_val": zip_code, "valid_input": "true"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    lon, lat = (float(value) for value in response.json()["result"])
    return lat, lon


def fetch_jobs(zip_code: str = DEFAULT_ZIP, radius_miles: int = DEFAULT_RADIUS_MILES):
    """Target openings within `radius_miles` of `zip_code`."""
    session = new_session()
    lat, lon = _home_coordinates(session, zip_code)

    jobs, page = [], 1
    while page <= MAX_PAGES:
        response = session.post(
            SEARCH_URL,
            data={
                "lat": lat,
                "lon": lon,
                "maxdistance": f"{miles_to_km(radius_miles):.3f}",
                "currentPage": page,
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

    log.info("%s: collected %d listings over %d page(s)", COMPANY, len(jobs), page)
    return jobs
