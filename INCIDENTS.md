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

---

## INC-003 — DPSU source returned 403 for 8 days (2026-06-18 → 2026-06-27) — **8-day interior hole in Series C, no bad rows**

**Status:** Recovered on its own; documented after the fact. **Dataset impact:
an 8-day absence in Series C (DPSU), all 9 crossings — no corrupted rows.**
Re-derived read-only against `data/dpsu.db` on **2026-07-08**.

### What broke
For ~8 days the DPSU logger ran on schedule but every run aborted before writing:
`dpsu.gov.ua/uk/map` returned **HTTP 403 Forbidden** to the GitHub Actions
runner, across all four retry attempts (backoff 15/30/45 s). Representative run
log (`2026-06-25T04:43Z`, run `28147387567`):

```
WARNING Fetch attempt 1/4 failed (403 Client Error: Forbidden for url: https://dpsu.gov.ua/uk/map) — retrying in 15s
...
ERROR Failed to fetch https://dpsu.gov.ua/uk/map after 4 attempts: 403 Client Error: Forbidden
ERROR Scrape aborted — map unreachable or structure changed
requests.exceptions.HTTPError: 403 Client Error: Forbidden for url: https://dpsu.gov.ua/uk/map
##[error]Process completed with exit code 1.
```

### Root cause — classification **(b): scraper ran and failed**
Not (a) "Actions never ran" — **every** interior run fired (71 runs
`2026-06-19T16:57Z … 2026-06-26T22:43Z`, conclusion `failure` for all 71). Not
(c) stale/empty payloads deduping to zero — nothing was fetched at all. The
source itself **refused the request** (403), so the fetch guard in
`dpsu_scraper.py:fetch_map_html` raised and the run exited non-zero **before any
insert**. The 403 is source-side (anti-automation block on the runner's egress
IP — the same class of block RECON_echerha.md documents for Akamai; DPSU began
403-ing the Azure runner and later stopped). Whether the block was IP-based or a
temporary WAF change on DPSU's side is **not determinable from here**; the
proximate cause (sustained 403 → clean abort → zero inserts) is certain. The
source recovered at `2026-06-27T18:00Z` and native readings resumed for all 9
crossings with no code change.

### Affected window (UTC) — all 9 crossings
Last native reading before the hole and first native reading after, per crossing
(distinct `source_updated_utc`, `ts_synthetic=0`):

| crossing | last before | first after |
|---|---|---|
| budomierz | 2026-06-18T17:49:28Z | 2026-06-27T18:00:58Z |
| dolhobyczow | 2026-06-18T17:48:52Z | 2026-06-27T18:00:37Z |
| dorohusk | 2026-06-18T18:00:21Z | 2026-06-27T18:04:08Z |
| hrebenne | 2026-06-18T17:49:07Z | 2026-06-27T18:00:49Z |
| korczowa | 2026-06-18T17:50:44Z | 2026-06-27T18:01:10Z |
| kroscienko | 2026-06-18T17:50:02Z | 2026-06-27T18:01:33Z |
| malhowice | 2026-06-18T17:50:21Z | 2026-06-27T18:01:45Z |
| medyka | 2026-06-18T17:49:53Z | 2026-06-27T18:01:23Z |
| zosin | 2026-06-18T18:00:00Z | 2026-06-27T18:03:52Z |

System-wide boundary: **`2026-06-18T18:00:21Z` → `2026-06-27T18:00:37Z`** (last
native anywhere → first native anywhere) = a **216.0 h** gap (9 days + 16 s).
Rows with `scraped_at` in `[2026-06-19, 2026-06-27)`: **0**. No daily CSV exists
for 2026-06-19 … 2026-06-26 (`data/dpsu_*.csv` jumps `2026-06-18` → `2026-06-27`).

### Data impact
An **8-day interior hole** in Series C (DPSU) across all 9 crossings. **No bad
rows** — this is *absence*, not corruption. The fetch guard did exactly its job:
a refused request writes nothing rather than a null-filled placeholder, so every
stored DPSU row remains a real reading.

### Recoverable?
**No.** DPSU publishes only *current* state (no public history), so the 8 days of
missing readings can never be backfilled. Per methodology, the remedy is
documentation, not interpolation — **do not** synthesise fills across this gap.

### Remediation for analysis
The blackout splits Series C into a thin pre-hole stub (2026-06-18 only) and the
usable post-recovery stream. **The clean B–C pairing window starts
`2026-06-27`.** Any C-vs-B statistic must set
`--window-start 2026-06-27T00:00:00Z` or explicitly justify a different floor;
otherwise the single pre-blackout day contaminates the per-crossing DPSU
distribution (see the Zosin re-derivation, `analysis/METHODOLOGY.md`).

### Detection-failure note — the loop fired but no one was listening
The failure→GitHub-issue automation **worked mechanically**: **71** issues titled
`DPSU scraper failure …` were auto-opened over the interior
(`created:2026-06-19..2026-06-26`), all later closed `COMPLETED` with **zero
comments** (batch-closed ~`2026-06-27T23:23Z`). So the loop did not silently
swallow the failure — it produced 71 signals. What it did **not** do is surface
those signals to a human: with no triage step, 71 identical auto-issues read as
noise and no `INCIDENTS.md` entry was made for 8 days. **What closes the loop:** a
recurring **"triage open GitHub issues on this repo"** line in the survey
protocol (added in PR-D, D2), so an unattended run of failure-issues is caught
within a week rather than never.
