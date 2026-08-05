# Corridor event log — `corridor_events.csv`

Hand-curated, sourced record of **real-world events** (blockades, strikes, policy
changes, closures) that confound or explain anomalies in the four collected
series. This is the repo's **only hand-curated, non-reproducible** layer.

- **Committed, not gitignored** — irreplaceable hand-research; fails recoverability,
  so it is ground truth (the inverse of the re-derivable 2708 baseline, which *is*
  gitignored).
- **CSV, not a DB** — it is edited by hand, so it must stay diff-legible.
- **Sourced, never remembered** — every row carries a resolving `source_url`. The
  data window (see below) postdates the model knowledge cutoff, so model-memory
  leakage is not merely forbidden, it is *impossible* — every row is guaranteed
  live-sourced.
- **Validate with** `python events_validate.py` (hard-fails on bad rows).
- **Join with** `events_log.events_for(events, crossing, ts)`.

Design rationale and the source survey live in `RECON_event_log.md`. This is an
**event-world** log; instrument faults (scraper bugs, cadence gaps) live in the
separate `INCIDENTS.md`. See METHODOLOGY.md for the instrument-vs-world split.

---

## Data window

`[2026-06-12 → today]`. The earliest observation across the four series is granica
`2026-06-12T06:40:00Z`. **Do not backfill earlier than 2026-06-12** — events
predating series collection cannot confound data that does not exist.

As of 2026-07-08 the window spans 26 days (2026-06-12 → …). It supports the
**structural-asymmetry** read — which is *window-invariant* (true at any window
length, so it does not stale as the window grows) — but **no capacity-trend
claim** (still far too short). A near-empty log is itself a finding: it
characterises the baseline as **normal operations**, a caveat to state, not to hide.

---

## Inclusion criterion (pre-registered — apply mechanically)

> **Log any event that has (a) a named, externally-sourced disruption to FREIGHT
> movement at one or more corridor crossings and (b) a citable `source_url`. Apply
> uniformly; log every qualifying event regardless of which thesis it supports or
> undercuts.**

This mechanical "sourced freight disruption → row" rule is the **anti-bias
guardrail**: it removes curator discretion at capture time.

**Materiality is a tag, not a filter (Call 1).** Do **not** drop short events.
Log every qualifying event and set `below_materiality_floor = true` when its
duration is **< 6 hours**. Materiality then becomes a *query-time* parameter, so a
filtered analysis stays recomputable and a 5-hour event is never silently lost.
The 6 h threshold is anchored to granica's coarsest inter-scrape gap (INC-002:
5–7.5 h) — an event shorter than that may be invisible to Series A even though a
finer feed (B/C) could see it; tagging (not cutting) sidesteps that asymmetry. If
a hard floor is ever reintroduced, anchor it to the *finest* feed's gap, not the
coarsest. (Dates here are day-granularity, so the boolean is set by the curator
from the source's reported duration, not recomputed from `start/end_date`.)

**Carve-outs:**
- `policy` events are effectively instantaneous but persistent: `start_date` =
  effective date, `end_date` = `ongoing` (or the next superseding policy). The
  materiality tag does not apply.
- **Holidays are NOT logged here.** A public holiday is a recurring covariate — a
  deterministic function of (country, year) — handled by a derived calendar
  overlay at analysis time. *Bridge:* a holiday that triggers an **announced**
  closure is logged as a normal sourced `closure` row.

---

## Schema

One row per event. `;`-delimited inside `crossings_affected`; everything else is a
plain scalar. (Fallback to JSONL only if a **second** list/nested field ever
appears.)

| field | type | rule |
|---|---|---|
| `event_id` | str | stable, unique, non-empty |
| `start_date` | ISO date `YYYY-MM-DD` | required |
| `end_date` | ISO date \| `ongoing` | `start_date ≤ end_date` |
| `date_precision` | enum | `exact` / `day` / `week` / `month` |
| `event_type` | enum | `blockade` / `strike` / `policy` / `closure` / `infrastructure` / `security` / `weather` |
| `direction_affected` | enum | `PL_UA` / `UA_PL` / `both` / `unknown` |
| `crossings_affected` | `;`-list \| sentinel | each token ∈ the 9 canonical codes, **or** a lone sentinel `corridor` / `unknown` |
| `severity` | enum | `low` / `med` / `high` |
| `confidence` | enum | `confirmed` / `reported` / `unconfirmed` |
| `below_materiality_floor` | bool | `true` / `false` (`true` ⇔ duration < 6 h) |
| `description` | str | 1–2 sentences, non-empty |
| `source_url` | url | **required**, non-empty |
| `source_type` | enum | `gov` / `wire` / `outlet` / `portal_notice` |
| `date_recorded` | ISO date | when the row was added |
| `last_verified` | ISO date | provenance vintage (news restates) |

### `crossings_affected` — two system-wide sentinels (Call 3)

Default to **the narrowest scope the source supports**. False negatives are the
dangerous error (an under-scoped blockade lets a real spike read as a capacity
signal), so when genuinely uncertain, bias broad — but keep two *distinct*
system-wide values:

- **`corridor`** — established system-wide (the source asserts it).
- **`unknown`** — scope not yet pinned down (a research gap to close).

Both annotate **all** crossings in the join (safe-error property preserved), but
they are different facts — at drafting time you must tell a *real* corridor-wide
event from an *uninvestigated* one. (Mirrors the Series-D "absent because
system-wide" vs "absent because uninvestigated" distinction.) A sentinel stands
alone; the validator rejects mixing a sentinel with specific codes.

### The 9 canonical crossing codes

`dorohusk, zosin, dolhobyczow, hrebenne, budomierz, korczowa, medyka, malhowice,
kroscienko` (from `crossings.py:CANONICAL_NAMES`). The validator hard-fails any
other token.

### Severity rubric (a documented judgment)

- `high` — crossing(s) effectively closed to freight / multi-day backlog.
- `med` — material slowdown but freight still flowing.
- `low` — minor / localised disruption.

---

## Current contents — empty by honest sourcing (2026-06-28)

The file ships with **header only**. A live source survey on 2026-06-28 found **no
qualifying freight-disruption event** inside `[2026-06-12 → 2026-06-28]`. This is
the documented normal-operations baseline, not an omission. Two in-window
candidates were examined and **correctly excluded by the mechanical rule** — worked
examples of the criterion doing its job:

1. **Medyka–Szeginie bus-lane rebuild** (announced from 2026-06-15, later
   postponed for the summer). Excluded: it affects **buses, not freight**.
   (UNIAN, 2026-06-11: <https://www.unian.ua/economics/transport/polshcha-peredumala-zakrivati-vazhliviy-punkt-propusku-na-kordoni-z-ukrajinoyu-13409637.html>)
2. **Late-June queues at Korczowa/Medyka** (~141k crossings on 2026-06-27).
   Excluded: DPSU attributed them to a **weekend/summer passenger-tourist surge**,
   not a freight disruption — and explicitly *not* to any carrier blockade.
   (24tv, 2026-06: <https://24tv.ua/zakordon24/chergi-kordoni-pp-krakovets-yaka-situatsiya_n3095862>)

The Glavcom blockade aggregator carried no items in the window (most recent
2026-06-05), corroborating "no active freight blockade."

---

## Going-forward capture — manual by design

Episodic, low-volume events do not justify a permanent automated logger. Capture
is **manual append** against this source watchlist (robots.txt checked per source
at first fetch):

- **blockade / strike** — PAP, logistyka.rp.pl / rp.pl, wnp.pl; UA: Ukrinform,
  Ukrainska Pravda.
- **policy** — transport.ec.europa.eu (EU primary), kmu.gov.ua + mindev.gov.ua.
- **closure / infrastructure** — granica.gov.pl + strazgraniczna.pl komunikaty
  (PL side); DPSU Telegram `t.me/s/DPSUkr` + dpsu.gov.ua (UA side). Partly
  corroborated in-band by `dpsu_records.closure_flag` / `state` /
  `parse_miss_flag` — but the `source_url` must always be the external notice.
- **security / weather** — DPSU Telegram, Ukrinform; IMGW / Ukrhydromet.

DPSU Telegram is the one channel frequent + structured enough to tempt automation;
it is **left manual for now** (a notice-watcher would be a separate decision).
Aggregators (kordon.customs.gov.ua, nakordoni.com.ua) are for *locating* primaries
only — never the cited `source_url`.
