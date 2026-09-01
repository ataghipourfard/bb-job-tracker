"""Costco — careers.costco.com

Costco's careers site is an Angular front end on the Jibe/iCIMS platform, so
the HTML arrives empty and the listings come from its own JSON API:

    GET /api/jobs?location=<zip>&radius=<miles>&page=<n>&sortBy=posted_date

The page size is fixed at 10 and cannot be raised, and a ZIP search near
Irvine matches several hundred postings, so fetching every page on a
15-minute schedule would be needlessly heavy. Sorting newest-first instead
means the first few pages always contain anything that could possibly be new;
MAX_PAGES sets how deep to go.

Note that Costco describes these as "the typical kinds of positions that
Costco may hire for when openings exist" rather than as live vacancies.
"""

from __future__ import annotations

import logging
import re

from . import DEFAULT_RADIUS_MILES, DEFAULT_ZIP, REQUEST_TIMEOUT, new_session

log = logging.getLogger(__name__)

COMPANY = "Costco"
SEARCH_URL = "https://careers.costco.com/api/jobs"
JOB_URL = "https://careers.costco.com/jobs/{slug}?lang=en-us"
RESULTS_PER_PAGE = 10

# 10 pages = the 100 most recently posted listings in range, which is far more
# than turns over between two runs. Raise it to backfill more history.
MAX_PAGES = 10

# Costco tags some sites with a role suffix — "CORONA (CENTRAL FILL RX)" — which
# no geocoder recognises. The bare city name does.
SITE_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")


def fetch_jobs(zip_code: str = DEFAULT_ZIP, radius_miles: int = DEFAULT_RADIUS_MILES):
    """The most recently posted Costco listings within range of `zip_code`."""
    session = new_session()

    jobs, page = [], 1
    while page <= MAX_PAGES:
        response = session.get(
            SEARCH_URL,
            params={
                "location": zip_code,
                "radius": radius_miles,
                "stretchUnit": "MILES",
                "page": page,
                "sortBy": "posted_date",
                "descending": "true",
                "internal": "false",
                "domain": "costco.jibeapply.com",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()

        results = payload.get("jobs") or []
        if not results:
            break

        for result in results:
            job = result.get("data") or {}
            slug = job.get("slug") or job.get("req_id")
            title = (job.get("title") or "").strip()
            if not slug or not title:
                continue

            city = SITE_SUFFIX.sub("", (job.get("city") or "").strip())
            state = (job.get("state") or "").strip()
            location = ", ".join(part for part in (city, state) if part)
            if not location:
                location = (job.get("full_location") or "").strip()

            jobs.append(
                {
                    "id": str(slug),
                    "title": title,
                    "location": location or "Unknown",
                    # The careers page shows the full posting and its Apply
                    # button; apply_url drops straight onto an iCIMS login.
                    "url": JOB_URL.format(slug=slug),
                    "company": COMPANY,
                }
            )

        total = payload.get("totalCount") or 0
        if page * RESULTS_PER_PAGE >= total:
            break
        page += 1

    log.info("%s: collected %d listings over %d page(s)", COMPANY, len(jobs), page)
    return jobs
