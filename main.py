#!/usr/bin/env python3
"""Check every company scraper for new jobs near home and alert on Telegram.

Run it with `python main.py`. GitHub Actions runs it every 15 minutes and
commits the updated jobs.json back to the repository.
"""

# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

# Where you are, and how far you are willing to travel.
HOME_ZIP = "92606"
RADIUS_MILES = 15

# Only alert on listings whose title or location contains one of these.
# Empty means "allow everything", which is the default.
INCLUDE_KEYWORDS: list[str] = []

# Never alert on a listing whose title or location contains one of these.
EXCLUDE_KEYWORDS: list[str] = ["commission", "warehouse", "fulfillment"]

# Some roles are posted once for the whole country rather than per store —
# Apple Retail does this. They have no city to measure a distance to, so this
# decides whether they are worth hearing about.
INCLUDE_NATIONWIDE = True

# The very first run has nothing to compare against, so every listing in range
# looks new. Leave this False to record them quietly instead of sending a few
# hundred messages at once.
ALERT_ON_FIRST_RUN = False

# A backstop in case a careers site reissues its whole catalogue under new ids.
MAX_ALERTS_PER_RUN = 40

# Keep jobs.json from growing without limit; oldest ids are dropped first.
MAX_STORED_IDS_PER_COMPANY = 5000

# --------------------------------------------------------------------------- #

import json
import logging
import os
import sys

import geo
import notify
from scrapers import apple, bestbuy, costco, target

SCRAPERS = [bestbuy, target, costco, apple]

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "jobs.json")

# Location text for a posting that covers the whole country.
NATIONWIDE_MARKERS = ("united states", "usa", "nationwide", "multiple locations")

log = logging.getLogger("job-tracker")


# --------------------------------------------------------------------------- #
# stored state
# --------------------------------------------------------------------------- #

def load_state() -> dict[str, list[str]]:
    """Read jobs.json: {"Best Buy": ["id", ...], ...}."""
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            state = json.load(fh)
        if not isinstance(state, dict):
            raise ValueError("jobs.json is not an object")
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, ValueError) as exc:
        # Wiping state here would re-alert on everything, so refuse to run.
        raise SystemExit(f"jobs.json is unreadable ({exc}); fix or delete it") from exc

    return {company: list(ids) for company, ids in state.items() if isinstance(ids, list)}


def save_state(state: dict[str, list[str]]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")


def merge_ids(known: list[str], seen_now: list[str]) -> list[str]:
    """Append ids we have not stored before, keeping the newest at the end."""
    merged = list(known)
    known_set = set(known)
    for job_id in seen_now:
        if job_id not in known_set:
            merged.append(job_id)
            known_set.add(job_id)
    return merged[-MAX_STORED_IDS_PER_COMPANY:]


# --------------------------------------------------------------------------- #
# filtering
# --------------------------------------------------------------------------- #

def matches_keywords(job: dict) -> bool:
    """Apply INCLUDE_KEYWORDS and EXCLUDE_KEYWORDS to title and location."""
    haystack = f"{job['title']} {job['location']}".lower()

    for keyword in EXCLUDE_KEYWORDS:
        if keyword.lower() in haystack:
            log.debug("skipping %r — matches exclude keyword %r", job["title"], keyword)
            return False

    if INCLUDE_KEYWORDS and not any(k.lower() in haystack for k in INCLUDE_KEYWORDS):
        log.debug("skipping %r — matches no include keyword", job["title"])
        return False

    return True


def is_nationwide(location: str) -> bool:
    lowered = location.lower()
    return any(marker in lowered for marker in NATIONWIDE_MARKERS)


def is_near_home(job: dict, home: tuple[float, float]) -> bool:
    """True if the job is within RADIUS_MILES, fully remote, or nationwide."""
    location = job["location"]

    if geo.is_remote(location):
        return True
    if is_nationwide(location):
        return INCLUDE_NATIONWIDE

    distance = geo.distance_miles(home, location)
    if distance is None:
        log.debug("skipping %r — could not place %r", job["title"], location)
        return False
    return distance <= RADIUS_MILES


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #

def collect(home: tuple[float, float], state: dict[str, list[str]]):
    """Run every scraper. Returns (jobs to alert on, per-company ids in range)."""
    to_alert: list[dict] = []
    in_range: dict[str, list[str]] = {}

    for module in SCRAPERS:
        company = module.COMPANY
        known = state.get(company, [])
        try:
            # Handing over what we have already seen lets date-sorted feeds
            # stop paging as soon as they reach familiar ground.
            jobs = module.fetch_jobs(HOME_ZIP, RADIUS_MILES, set(known))
        except Exception:  # noqa: BLE001 — one broken site must not stop the rest
            log.exception("%s: scraper failed; skipping it this run", company)
            continue

        if not jobs:
            log.warning("%s: returned no listings; leaving its saved state alone", company)
            continue

        # Tidy "TUSTIN, California" into "Tustin, CA" so alerts read well and
        # the geocode cache does not hold three spellings of one city.
        for job in jobs:
            job["location"] = geo.normalize_location(job["location"]) or job["location"]

        kept = [job for job in jobs if matches_keywords(job) and is_near_home(job, home)]
        log.info("%s: %d listings fetched, %d in range and not filtered out",
                 company, len(jobs), len(kept))

        new = [job for job in kept if job["id"] not in set(known)]

        if not known and not ALERT_ON_FIRST_RUN:
            log.info("%s: first run — recording %d listings without alerting", company, len(new))
        else:
            to_alert.extend(new)
            if new:
                log.info("%s: %d new listing(s)", company, len(new))

        in_range[company] = [job["id"] for job in kept]

    return to_alert, in_range


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    home = geo.geocode_zip(HOME_ZIP)
    if home is None:
        # Without a home coordinate nothing can be filtered, and guessing would
        # mean alerting on jobs anywhere in the country.
        log.error("could not geocode HOME_ZIP %s; aborting without changing state", HOME_ZIP)
        geo.save_cache()
        return 1
    log.info("home: ZIP %s at %.4f, %.4f — radius %d miles", HOME_ZIP, home[0], home[1], RADIUS_MILES)

    state = load_state()
    to_alert, in_range = collect(home, state)

    if not in_range:
        log.error("every scraper failed; leaving jobs.json untouched")
        geo.save_cache()
        return 1

    suppressed: list[dict] = []
    if len(to_alert) > MAX_ALERTS_PER_RUN:
        suppressed = to_alert[MAX_ALERTS_PER_RUN:]
        to_alert = to_alert[:MAX_ALERTS_PER_RUN]
        log.warning("%d new listings exceeds MAX_ALERTS_PER_RUN (%d); "
                    "sending the first %d and recording the rest silently",
                    len(suppressed) + len(to_alert), MAX_ALERTS_PER_RUN, MAX_ALERTS_PER_RUN)
        for job in suppressed:
            log.warning("  not alerted: %s — %s (%s)", job["company"], job["title"], job["url"])

    undelivered: set[str] = set()
    if to_alert:
        try:
            delivered = notify.send_jobs(to_alert)
        except notify.TelegramNotConfigured as exc:
            log.error("%s — not recording these %d listings so they alert next run",
                      exc, len(to_alert))
            delivered = set()
        undelivered = {job["id"] for job in to_alert} - delivered
    else:
        log.info("no new listings this run")

    # A listing only counts as seen once its alert actually went out; anything
    # that failed to send is left out so the next run tries again.
    for company, ids in in_range.items():
        state[company] = merge_ids(state.get(company, []), [i for i in ids if i not in undelivered])

    save_state(state)
    geo.save_cache()
    log.info("done — %d alert(s) sent", len(to_alert) - len(undelivered))
    return 0


if __name__ == "__main__":
    sys.exit(main())
