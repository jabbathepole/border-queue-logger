# Project Brief: PL–UA Border Queue Logger

## Goal
Build a scheduled scraper that records truck queue/waiting-time data for all
Poland–Ukraine border crossings, creating a historical time-series dataset
(no public archive of this data exists).

## Step 1 — Reconnaissance (do this first)
- Inspect https://granica.gov.pl (Polish customs border-traffic site).
  Check the network tab / page source for a JSON endpoint feeding the
  waiting-time widgets — scraping an API is far more robust than parsing HTML.
- Also check whether the related mobile app or the Ukrainian DPSU site
  (kordon.customs.gov.ua or similar) exposes endpoints for the UA side.
- Document whatever endpoint/structure you find before writing the scraper.
- Respect robots.txt; set a descriptive User-Agent; poll no more than once
  per 2–3 hours.

## Step 2 — Scraper
- Python 3, `requests` (+ `beautifulsoup4` only if no JSON endpoint exists).
- For each PL–UA crossing (Dorohusk, Hrebenne, Zosin, Dołhobyczów, Korczowa,
  Medyka, Budomierz, Krościenko — confirm current list from the site):
  record: timestamp (UTC), crossing name, direction (entry/exit),
  vehicle class (trucks vs cars vs buses — trucks are the priority),
  reported waiting time and/or queue length.
- Normalize crossing names (Polish diacritics) to stable ASCII IDs.

## Step 3 — Storage
- SQLite database (`queues.db`), one table, append-only.
- Also write a daily CSV export for easy analysis later.

## Step 4 — Scheduling
- Run via GitHub Actions on a cron schedule (every 3 hours), committing the
  updated SQLite/CSV back to a private repo — this keeps the logger running
  without a personal machine being on.
- Add basic failure handling: if the site is unreachable or the structure
  changes, log the error and open a GitHub issue automatically rather than
  silently writing nothing.

## Step 5 — Sanity checks
- Validate each scrape: non-empty, expected crossings present, values within
  plausible ranges. Flag anomalies in a log — anomalies may be news, not bugs
  (a sudden 40 h wait at Dorohusk is exactly what the analysis is for).

## Later (not now)
- Charting script: queue trends per crossing over time (matplotlib).
- Join with Sentinel-2 imagery dates for visual verification of long queues.
