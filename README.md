# bb-job-tracker

Watches the careers sites of **Best Buy, Target, Costco and Apple** for jobs
near ZIP **92606** and sends a Telegram message the moment a new one appears.

It runs entirely on GitHub Actions' free tier: every 15 minutes a workflow
runs the scrapers, alerts on anything it has not seen before, and commits the
updated list of seen job IDs back to the repository.

```
main.py            settings, distance filter, dedupe, alerting
geo.py             geocoding (OpenStreetMap) + distance maths
notify.py          Telegram delivery
scrapers/          one file per company, each exposing fetch_jobs()
jobs.json          job IDs already alerted on, keyed by company
geocache.json      city -> coordinates, so we rarely hit the geocoder
```

---

## Setup

### 1. Create the Telegram bot and find your chat ID

1. In Telegram, message [@BotFather](https://t.me/BotFather), send
   `/newbot`, and follow the prompts. It replies with a token that looks like
   `123456789:AAE...`. That is your **TELEGRAM_BOT_TOKEN**.
2. Send your new bot any message (e.g. "hi"). A bot cannot message you until
   you have messaged it first.
3. Open this URL in a browser, with your token pasted in:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   Find `"chat":{"id":123456789` in the response. That number is your
   **TELEGRAM_CHAT_ID**.

If Telegram answers `Bad Request: chat not found` when an alert goes out, it
almost always means step 2 was skipped — a bot cannot start a conversation,
so you have to message it first. `setup_telegram.sh` checks for exactly that.

Or skip steps 1-2 below and run [`setup_telegram.sh`](setup_telegram.sh),
which finds the chat ID, sends a test message and stores the secret for you.
It reads the token from a hidden prompt and never writes it to disk.

### 2. Add the two repository secrets

In the repository on GitHub: **Settings → Secrets and variables → Actions →
New repository secret**. Add both, named exactly:

| Name | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | the chat ID from `getUpdates` |

No other secrets are needed — the workflow commits with the built-in
`GITHUB_TOKEN`.

### 3. Enable Actions

Go to the **Actions** tab. If you see "Workflows aren't being run on this
forked repository" or a similar prompt, click **I understand my workflows,
go ahead and enable them**.

Then check that the workflow may push: **Settings → Actions → General →
Workflow permissions → Read and write permissions → Save**. Without this the
run will work but the commit of `jobs.json` will fail.

### 4. Test it with the manual trigger

**Actions → Job tracker → Run workflow → Run workflow** (on `main`).

Open the run and read the log. On a healthy first run you will see something
like:

```
home: ZIP 92606 at 33.6997, -117.8042 — radius 15 miles
Best Buy: 23 listings fetched, 19 in range and not filtered out
Best Buy: first run — recording 19 listings without alerting
Target: 162 listings fetched, 99 in range and not filtered out
...
```

**The first run deliberately sends no Telegram messages.** With nothing to
compare against, every listing in range would look new and you would get a
few hundred messages at once. Instead it records them quietly, and from the
second run onwards you only hear about genuinely new postings. To watch the
alerts work immediately, set `ALERT_ON_FIRST_RUN = True` in `main.py`, or
delete a few IDs from `jobs.json` and trigger the workflow again.

After the run, `jobs.json` should have a new commit against it. From then on
the cron schedule takes over.

---

## Tuning it

Everything you are likely to change is at the top of [`main.py`](main.py):

| Constant | Default | What it does |
| --- | --- | --- |
| `HOME_ZIP` | `"92606"` | The centre of the search. |
| `RADIUS_MILES` | `15` | How far out to alert. |
| `INCLUDE_KEYWORDS` | `[]` | If non-empty, only alert on titles/locations containing one of these. Empty allows everything. |
| `EXCLUDE_KEYWORDS` | `["commission", "warehouse", "fulfillment"]` | Never alert on titles/locations containing one of these. |
| `INCLUDE_NATIONWIDE` | `True` | Alert on roles posted for the whole country rather than a city (see Apple Retail, below). |
| `ALERT_ON_FIRST_RUN` | `False` | Whether the first run for a company alerts or just records. |
| `MAX_ALERTS_PER_RUN` | `40` | Backstop if a careers site reissues its whole catalogue under new IDs. |
| `MAX_STORED_IDS_PER_COMPANY` | `5000` | Keeps `jobs.json` from growing forever. |

Matching is case-insensitive and checks the job title **and** its location.

Both `HOME_ZIP` and `RADIUS_MILES` are passed down to the scrapers, so
changing them changes what each careers site is asked for, not just what gets
filtered afterwards.

To run it on your own machine:

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python main.py
```

---

## Adding another company

Each scraper is a self-contained module in `scrapers/`. To add one:

**1. Find the data source first.** Open the company's job search in a browser
with DevTools on the **Network** tab, filter to Fetch/XHR, and run a search.
Nearly every large careers site is a front end over a JSON API, and hitting
that API directly is far more robust than parsing HTML. All four scrapers
here were built this way:

| Company | Platform | Endpoint |
| --- | --- | --- |
| Best Buy | ServiceNow portal | `POST /api/now/sp/widget/<id>` (needs the `g_ck` token from the page) |
| Target | in-house | `POST corporate.target.com/api/jobsearch` (form-encoded, radius in km) |
| Costco | Jibe / iCIMS | `GET careers.costco.com/api/jobs` |
| Apple | in-house | `POST jobs.apple.com/api/v1/search` (needs `X-Apple-CSRF-Token`) |

If there is no API, fall back to `requests` + BeautifulSoup (already in
`requirements.txt`). If the listings only exist after JavaScript runs, add
`playwright` to `requirements.txt` and uncomment the Playwright step in
`.github/workflows/job-tracker.yml`.

**2. Write `scrapers/<company>.py`** exposing a `fetch_jobs()` that returns a
list of dicts. Copy an existing scraper as a starting point — `costco.py` is
the simplest.

```python
from . import DEFAULT_RADIUS_MILES, DEFAULT_ZIP, REQUEST_TIMEOUT, new_session

COMPANY = "Example Co"

def fetch_jobs(zip_code=DEFAULT_ZIP, radius_miles=DEFAULT_RADIUS_MILES):
    session = new_session()
    ...
    return [
        {
            "id": "unique-and-stable-within-this-company",
            "title": "Sales Associate",
            "location": "Irvine, CA",       # or "Remote"
            "url": "https://example.com/jobs/123",
            "company": COMPANY,
        }
    ]
```

Rules the rest of the code relies on:

* `id` must be **stable across runs** — it is the dedupe key. Prefer the
  site's own requisition ID over anything derived from the title.
* `location` should be `"City, ST"`. `"City, California"` and `"CITY, CA"`
  are fine too; `geo.normalize_location` tidies them up.
* Use the word `Remote` in `location` for fully-remote roles. They are
  alerted on regardless of distance.
* Filtering by distance is **not** the scraper's job. Narrow by location if
  the API supports it, and let `main.py` apply the real 15-mile rule.
* Raise on failure rather than returning junk. A scraper that raises (or
  returns an empty list) is logged and skipped for that run, and no company's
  stored state is touched.

**3. Register it** in `main.py`:

```python
from scrapers import apple, bestbuy, costco, example, target

SCRAPERS = [bestbuy, target, costco, apple, example]
```

The new company gets its own key in `jobs.json` automatically, and its first
run records silently like any other.

---

## How the filtering works

For each listing, in order:

1. `EXCLUDE_KEYWORDS` and `INCLUDE_KEYWORDS` are matched against
   `title + location`.
2. Anything whose location says *Remote* is kept regardless of distance.
3. Anything posted nationwide is kept if `INCLUDE_NATIONWIDE` is on.
4. Otherwise the location is geocoded and kept only if the great-circle
   distance from `HOME_ZIP` is `<= RADIUS_MILES`.

Geocoding uses OpenStreetMap's free Nominatim service, rate-limited to one
request per second and cached in `geocache.json`, which is committed
alongside `jobs.json`. In practice a run makes zero geocoding requests
because every nearby city is already in the cache. A location the geocoder
does not recognise is logged and the listing is skipped.

An ID is only written to `jobs.json` once its Telegram alert has actually
been delivered, so a failed send is retried on the next run rather than
being silently lost.

---

## Known limits

* **Scheduled runs are best-effort.** GitHub does not guarantee the
  `*/15 * * * *` cron fires on time, and it can skip ticks when the platform
  is busy. Treat 15 minutes as a floor, not a promise.
* **"Salaried" is not filtered on.** The scrapers pull *every* posted role at
  each company near you, hourly and salaried alike, across all categories —
  which is what you want from stopgap employers. Narrow it with
  `INCLUDE_KEYWORDS` if you change your mind.
* **Costco's listings are a catalogue, not live vacancies.** Costco itself
  says these are "the typical kinds of positions that Costco may hire for
  when openings exist". The scraper reads the 100 most recently posted
  listings in range (`MAX_PAGES` in `scrapers/costco.py`); raise it to
  backfill more history.
* **Apple Retail store roles are posted nationwide.** Apple lists store jobs
  as a single US-wide pipeline requisition instead of one per store, so they
  arrive with no city attached. `INCLUDE_NATIONWIDE = True` is what lets
  those through; set it to `False` if the "US - Specialist" style postings
  are noise to you.
* **Careers sites change.** These are private APIs with no compatibility
  promises. If one company goes quiet for days, check the Actions log — a
  broken scraper is logged loudly and skipped, and the other three keep
  running.
