# Data Incidents Log

Known incidents affecting the collected time-series, with measured data impact.
The **database is ground truth** here, not the run logs. All investigation
queries are read-only (`?mode=ro`).

---

## INC-001 — granica SOAP `'rok'` error (2026-06-12) — **no data impact**

**Status:** Resolved before first commit. **Dataset impact: none** (no gap, no bad data).

### What broke
The first granica scrape attempt logged, for all 9 crossings:

```
ERROR Unexpected error for <crossing>: ComplexType() got an unexpected
keyword argument 'rok'. Signature: `dane_in_wszystko: {granicaServiceOriginal}getCzasyReq`
ERROR VALIDATION FAIL: zero records returned
ERROR Validation failed — aborting insert
```

at **`2026-06-12T05:37:00Z`** (log line wall-clock `06:37:12` local = `05:37Z`).

### Root cause
The scraper was calling the wrong SOAP operation — one whose request type
(`getCzasyReq`) does **not** accept a `rok` (year) keyword. Passing
`rok`/`miesiac`/`dzien`/`godzina` to that ComplexType raised `TypeError` for
every crossing. The fix switched to the `getCzasyWszystko` operation, whose
request type **does** accept those fields (see `scraper.py:scrape_all` →
`client.service.getCzasyWszystko(dane_in_wszystko={... "rok": str(now.year) ...})`).

### Fix commit + timing
- Fixed **before the project's first commit** — this was a pre-deployment dev
  iteration, never shipped to CI.
- First commit `6aa1786 "init: PL-UA border queue logger"` at
  **`2026-06-12T07:20:23Z`** already contains the working `getCzasyWszystko`
  call. The first *successful* run is in the logs at `2026-06-12T06:40:00Z`.

### Why there is no data impact — the fail-safe worked
The validator (`validate.py`) rejected the zero-record result and **aborted the
insert**. A failed scrape therefore writes **nothing** — no partial rows, no
null-filled placeholder rows. Confirmed against the DB:

- Rows with `scraped_at <= 2026-06-12T05:37:00Z`: **0**.
- Earliest row in `queue_records`: **`2026-06-12T06:40:00Z`** (the first
  successful run).

So the bug did not corrupt or null-out any stored row, and it did not punch a
hole *inside* the series — the series simply **begins** at the first good run.
The only "loss" is a single would-be ~05:37Z reading that predates the dataset.

### Affected (degraded) window
**Empty.** Degraded window `[dataset start … first clean timestamp)` =
`[2026-06-12T06:40:00Z … 2026-06-12T06:40:00Z)` = ∅.

- `2026-06-12T06:40:00Z` — 8 of 9 crossings (kroscienko absent this run due to a
  **separate**, transient `getCzasy ... invalid XML / b'blad'` server response,
  not the `rok` bug; it returned normally one minute later).
- `2026-06-12T06:41:00Z` — all 9 crossings. First fully-complete scrape.

(Two rows in this window — `zosin` at 06:40 and 06:41 — have all six metric
columns NULL. That is the granica feed genuinely reporting no times for a quiet
crossing at that early hour, not corruption: `zosin` carries real values from
the 21:36Z run onward.)

### CSV cross-check
`data/queues_2026-06-12.csv` and `queue_records` agree exactly for 2026-06-12:
35 rows each, identical set of 4 timestamps, identical `(timestamp, crossing)`
keys and `trucks_exit_min` values. The CSV does **not** contain a 05:37Z row
either.

### Recoverable?
**No — and nothing needs recovering.** granica.gov.pl publishes only *current*
state with no public history, so the missing 05:37Z point can never be
backfilled. But since the next successful run was ~1 h later and sits at the very
start of the dataset, there is no interior gap to repair. The window is closed,
not degraded.

### Remediation for analysis
**None required.** There is no bad data to exclude and no interior gap to bridge.
The dataset's clean start is `2026-06-12T06:40:00Z`. See note in
`analysis/METHODOLOGY.md`.

---

## INC-002 — irregular scrape cadence / scheduler not firing (early window) — *separate from INC-001*

Surfaced while measuring INC-001. **Not** caused by the `rok` bug. Recorded here
for completeness; previously logged in commit
`3d7b20f "docs: log 2026-06-15 schedule-not-firing incident and external-cron fix"`.

The cron is `0 */3 * * *` (every 3 h → 8 scrapes/day expected). Actual scrape
counts and inter-scrape intervals in the early window are well below cadence:

| Day | Scrapes | Expected | Notable inter-scrape gaps |
|---|---|---|---|
| 2026-06-12 | 4 | 8 | 06:41Z → 21:36Z ≈ **895 min** (deployment day; only manual morning runs then first CI run) |
| 2026-06-13 | 6 | 8 | several 300–400 min intervals |
| 2026-06-14 | 6 | 8 | 22:09(13th)→04:43 ≈ 394 min; 09:44→14:16 ≈ 272 min |
| 2026-06-15 | 4 | 8 | 04:59→12:27 ≈ 448 min, 12:27→17:27 ≈ 300 min |

This is a **cadence/coverage** characteristic (GitHub Actions schedule
unreliability, since addressed with an external cron trigger), not a data-
corruption issue. The analysis already handles uneven coverage correctly:
missing buckets are never filled (`complete=0` buckets are excluded), and
`--min-buckets` (default 24) gates per-crossing statistics. No row is wrong;
there are simply fewer of them than the nominal cadence implies.
