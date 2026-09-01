"""Costco — careers.costco.com

Costco's careers site is an Angular front end on the Jibe/iCIMS platform, so
the HTML arrives empty and the listings come from its own JSON API:

    GET /api/jobs?location=<zip>&radius=<miles>&page=<n>&sortBy=posted_date

The page size is fixed at 10 and cannot be raised, and a ZIP search near
Irvine matches several hundred postings, so fetching every page on a tight
schedule would be needlessly heavy. Sorting newest-first means anything new
is on the first page, so when `known_ids` is supplied we stop as soon as a
page holds nothing we have not already seen — normally after one request.
MAX_PAGES caps how deep an unseeded run will go.

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

# Newest-first, so depth only matters for the very first run, which has no
# baseline and wants some history. Once state exists, a run only has to cover
# what could have appeared since the last one — 20 listings is a wide margin
# for a five-minute gap in a region that posts a handful a day.
MAX_PAGES = 10        # cold: no stored state yet
WARM_PAGES = 2        # once we have a baseline

# Costco tags some sites with a role suffix — "CORONA (CENTRAL FILL RX)" — which
# no geocoder recognises. The bare city name does.
SITE_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")


def fetch_jobs(zip_code: str = DEFAULT_ZIP, radius_miles: int = DEFAULT_RADIUS_MILES,
               known_ids: set | None = None):
    """The most recently posted Costco listings within range of `zip_code`.

    Passing `known_ids` signals that a baseline already exists, so only the
    newest WARM_PAGES pages are walked instead of MAX_PAGES.
    """
    session = new_session()

    depth = MAX_PAGES if known_ids is None else WARM_PAGES

    jobs, page, fetched = [], 1, 0
    while page <= depth:
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
        fetched += 1

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

    log.info("%s: collected %d listings in %d request(s)", COMPANY, len(jobs), fetched)
    return jobs
