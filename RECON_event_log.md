# Recon — Corridor Event Log (hand-curated disruption record)

> **Handoff note for a fresh session.** Self-contained reconnaissance report.
> Status: **RECON ONLY — nothing built.** No data file, no entries, no validator,
> no join helper exist yet. The deliverable is this report + a reviewed
> build/don't-build decision. **Stop here until the maintainer reviews.**
> Recon date: **2026-06-28**.

---

## §0 — What this layer is, and why it inverts the live-logger rules

The repo runs a four-series directional grid (A granica PL→UA physical, B eCherga
UA→PL virtual, C DPSU UA→PL physical, D PL→UA virtual = structurally absent — see
[`SERIES_D.md`](SERIES_D.md) for the dated claim + verification scaffold) plus
a code-only monthly volume baseline (2708). **Every one of those is
machine-collected, schema-validated, hard-failing, and reproducible by re-running
a script.**

The event log is **different in kind**: a dated, sourced, hand-curated record of
real-world events (blockades, strikes, policy changes, closures) that confound or
explain anomalies in the series. **No scraper can regenerate it.** That single
property — irreproducibility — drives every design decision, and several of them
**invert** the live-logger rules:

| Dimension | Live loggers / 2708 | Event log | Why it inverts |
|---|---|---|---|
| Storage | SQLite DB | **CSV** | hand-edited → must be diff-legible & hand-editable |
| Commit policy | 2708 **gitignored** (re-derivable) | **committed** | irreplaceable hand-research fails recoverability → commits as ground truth |
| Capture | automated, scheduled | **manual append** | episodic & low-volume → no permanent scraper justified |
| Provenance | code is the source | **every row cited** | model memory inadmissible; sourced not remembered |
| Failure mode | silent bad data | **curation bias** | fixed by a *pre-registered* inclusion rule, not by a parser |

It is the interpretive backbone for spikes across all four series, and it
specifically unlocks the **C-vs-B divergence** question (do physical and virtual
queues decouple precisely inside blockade windows?). It is **not** required for
the structural-asymmetry thesis (piece one), which proceeds in parallel and does
**not** block on this.

> **Naming caution.** `INCIDENTS.md` already exists but is a *different* artifact:
> it logs **data incidents** (scraper bugs, cadence gaps — INC-001/INC-002) that
> affect the time-series *collection*. The event log records **real-world
> external events** that affect the *border*. Keep them separate. Proposed
> filename: `data/corridor_events.csv` (data, committed) with this recon as the
> design doc. Do **not** fold events into `INCIDENTS.md`.

---

## §1 — Data window (the log only covers this forward)

Earliest observation timestamp per series DB (queried read-only this session):

| Series | DB / table | Earliest `scraped_at` | n rows |
|---|---|---|---|
| A — granica (PL→UA physical) | `queues.db` / `queue_records` | **2026-06-12T06:40:00Z** | 1632 |
| B — eCherga (UA→PL virtual) | `echerha.db` / `echerha_records` | 2026-06-15T11:32:00Z | 17328 |
| C — DPSU (UA→PL physical) | `dpsu.db` / `dpsu_records` | 2026-06-18T18:27:00Z | 38 |
| D — PL→UA virtual | — | structurally absent | — |

**Window start = `2026-06-12` (granica, the earliest of the four).** Events
predating this cannot confound data we don't have, so **do not backfill earlier
than 2026-06-12.** The active window is `[2026-06-12 … today]`, i.e. ~16 days as
of this recon. (Per-series sub-windows differ — a B-vs-C divergence question only
has data from 2026-06-18 onward — but the *log's* coverage floor is the union's
earliest, 2026-06-12.)

> Sanity check on the "sourced not remembered" rule: the assistant knowledge
> cutoff (Jan 2026) **predates the entire window**, so no event in scope is even
> recallable from memory. Every row must be live-sourced regardless. The searches
> in §2 already surfaced real candidates *outside* the window (e.g. a Jan-2026
> blockade suspension, an Apr-23 Medyka roadwork) — those are correctly excluded.

---

## §2 — Source survey per event type

Reachability confirmed via live search this session (2026-06-28). PL/UA
government + wire prioritised over aggregators, per existing repo learnings.
**These are candidate channels, not logged events** — no event is entered in recon.

| event_type | Primary authoritative source(s) | Tier | Window-reachable? | Notes |
|---|---|---|---|---|
| `blockade` (farmer/carrier protest — **dominant confounder**) | PAP [pap.pl]; logistyka.rp.pl / rp.pl; wnp.pl; UA: Ukrinform, Ukrainska Pravda; gov context: kmu.gov.ua | wire/outlet + gov | Yes | Heavily covered; PL carrier protests are the headline corridor confounder. Cross-check PL outlet vs UA wire to fix dates. |
| `strike` / work-to-rule (customs, border guard) | strazgraniczna.pl (komunikaty); PAP; UA: dpsu.gov.ua, customs.gov.ua | gov + wire | Yes | Lower frequency; gov komunikaty are authoritative. |
| `policy` (EU–UA road-transport agreement; permits; reclassifications) | **transport.ec.europa.eu** (EU primary); **kmu.gov.ua** + mindev.gov.ua (UA gov); Ukrinform/RBC-Ukraine | gov | Yes (archived) | Agreement currently extended to **31 Mar 2027**; a **1 Jul 2026 smart-tachograph** provision falls *inside the window* — a concrete near-term policy marker. |
| `closure` (scheduled/emergency crossing closure) | **granica.gov.pl** portal notices; strazgraniczna.pl + regional oddziały (e.g. bieszczadzki.strazgraniczna.pl); DPSU Telegram `t.me/s/DPSUkr` | gov / portal_notice | Yes | **Partly capturable from feeds the repo already touches** (see §3). |
| `infrastructure` (scanner/lane outage, construction, DB outage) | strazgraniczna.pl komunikaty; DPSU Telegram; nv.ua (wire) | gov / portal_notice | Yes | Real in-window example surfaced: **Medyka bus-inspection reorg 2026-06-15**. (Jan-18 customs-DB outage at Medyka/Hrebenne predates window → excluded.) |
| `security` (air-raid disruption near western UA crossings) | DPSU Telegram; Ukrinform; oblast military-administration channels | gov / wire | Yes (Telegram) | Episodic, often short — many will fail the duration floor (§6). |
| `weather` (major snow/flood) | IMGW (PL met service); UA: Ukrhydromet; wire | gov / wire | Yes | Rare for a June window; included for completeness/seasonality. |
| `holiday` (PL + UA public holidays) | PL: official ustawa calendar; UA: KMU calendar | gov / reference | n/a | **Recommend: derive from a calendar, do NOT hand-enter as events** (see §4) — they are predictable, dense, and would pollute the curated log. |

**Aggregators seen but de-prioritised** (use only to *locate* primary sources,
never as the cited `source_url`): kordon.customs.gov.ua, nakordoni.com.ua,
uahelp.info, visahq.com, Wikipedia "Poland–Ukraine border crisis".

---

## §3 — Overlap with sources the repo already polls

| Category | Capturable from existing feeds? | Detail |
|---|---|---|
| `closure` | **Partly** | `granica.gov.pl` (Series A source host) and `dpsu.gov.ua` (Series C source host) both post closure/notice text on their human-facing portals. The repo already reaches both hosts. The DBs also carry signal: `dpsu_records.closure_flag` / `state` / `restricted_flag` flip on closures, and a granica scrape returning fewer crossings can mark an outage. |
| `infrastructure` | **Partly** | Same portals + DPSU Telegram. `dpsu_records.parse_miss_flag` and missing-crossing runs are weak in-band hints. |
| `blockade` / `strike` / `policy` / `security` / `weather` | **No** | These need new (manual) sourcing — they are not in any feed the repo polls. |

**Do not build new scrapers in recon, or as part of this layer at all** (§5).
Where an event is *corroborated* by an in-band DB signal (e.g. a closure window
that lines up with `closure_flag`), note it in the row `description` — but the
`source_url` must still be the external notice, never the DB.

---

## §4 — Format + schema confirmation

**Recommendation: CSV.** The proposed schema has exactly one list-typed field
(`crossings_affected`). A single list field is handled cleanly in CSV with a
fixed in-cell delimiter (recommend `;` or `|`, since `,` is the CSV separator and
crossing codes never contain `;`). That does not make CSV awkward enough to
justify JSONL's worse line-diffs — and diff-legibility is the whole reason this
layer is a flat file. **Stay CSV.**

> **JSONL fallback trigger (pre-registered):** switch to JSONL only if, during
> backfill, a *second* list/nested field appears (e.g. multiple `source_url`s per
> event, or per-crossing severity). One list field → CSV; two+ → revisit. This
> keeps the decision mechanical rather than aesthetic.

Schema is confirmed as proposed, with these tightenings:

- `crossings_affected`: `;`-delimited list of canonical codes **or** the literal
  `corridor`. Canonical codes are exactly the 9 in `crossings.py:CANONICAL_NAMES`
  — `dorohusk, zosin, dolhobyczow, hrebenne, budomierz, korczowa, medyka,
  malhowice, kroscienko`. The validator hard-fails any other token (the
  hand-curated analog of "hard-fail on unknown inputs").
- `direction_affected` vocab aligns with the analysis layer's existing labels
  (`PL_UA` ↔ granica `wyjazd`; `UA_PL` ↔ eCherga/DPSU). Keep `both` / `unknown`.
- `date_precision` (`exact`/`day`/`week`/`month`) is the event-log analog of the
  NULL-semantics rigor used elsewhere — it distinguishes known-exact from
  approximate dates rather than faking precision.
- All dates ISO `YYYY-MM-DD` (date granularity; events have duration, the join is
  temporal overlap, not a point). `end_date` may be the literal `ongoing`.
- `severity` rubric (document it in the header): `high` = crossing(s) effectively
  closed to freight / multi-day backlog; `med` = material slowdown but flowing;
  `low` = minor/localised. Explicitly a judgment call.
- `confidence`: `confirmed` (gov/primary or ≥2 independent), `reported` (single
  wire/outlet), `unconfirmed` (rumoured/unverified).
- `last_verified` = vintage anchor (news restates; same discipline as the 2708
  raw-XLSX provenance note).

Full field list is unchanged from the brief's proposed schema; see §7 header.

---

## §5 — Going-forward capture mode (recommend, don't build)

**Recommendation: manual-by-design — source watchlist + append discipline. No
automated logger.** This applies the throughput-scraper deferral logic: corridor
events are **episodic and low-volume** (a handful of material events per month),
so a permanent automated pipeline is unjustified — same call made for the DPSU
higher-cadence layer and the 2708 baseline.

Record this as a rationale note sibling to the scraper decisions:
*going-forward capture is manual-by-design.*

The one category that *is* frequent + structured enough to tempt automation is
`closure`/`infrastructure` via **DPSU Telegram** (`t.me/s/DPSUkr`, a public web
view). **Still recommend manual** for now: the curation/inclusion judgment (§6)
and citation requirement don't automate cleanly, and the in-band DB flags already
give a first-pass anomaly pointer. Revisit only if closure volume proves high
enough to warrant a *notice-watcher* (a follow-on decision, not part of this PR).

**Source watchlist** (the manual-append checklist): PAP + logistyka.rp.pl
(blockade/strike), transport.ec.europa.eu + kmu.gov.ua (policy), granica.gov.pl +
strazgraniczna.pl komunikaty (closure/infra PL side), DPSU Telegram + dpsu.gov.ua
(closure/infra/security UA side), Ukrinform/Ukrainska Pravda (UA wire
cross-check). robots.txt to be checked per source **at first fetch**, non-negotiable.

---

## §6 — Draft inclusion criterion (for review — the anti-bias guardrail)

Pre-registered, applied mechanically, fixed **before** any backfill so maintainer
discretion is removed at capture time:

> **Include any event that has (a) a named, externally-sourced disruption to
> freight movement at one or more corridor crossings, (b) a citable `source_url`,
> and (c) a material duration of ≥ 6 hours. Apply uniformly; log every qualifying
> event regardless of which thesis it supports or undercuts.**

**Why N = 6 hours.** The floor is anchored to the *coarsest* series' sampling
cadence so an included event is actually observable. granica's nominal cadence is
every 3 h (8/day), but INC-002 documents real inter-scrape gaps of 300–448 min
(5–7.5 h) in the early window. An event shorter than ~one granica sampling
interval cannot be reliably distinguished from sampling noise in Series A. 6 h
≈ guarantees an in-window event spans at least one expected granica sample while
still excluding momentary hiccups (a brief lane stop, a 30-min air-raid pause).

**Carve-outs, also pre-registered:**
- A sub-6 h event of unambiguous significance (e.g. an official full crossing
  closure of any duration) **may** be logged with `severity=low` and a note —
  but this is the *only* discretion permitted, and it must be a named official
  closure, not a judgment about importance.
- `policy` events are effectively instantaneous (a signing/entry-into-force date)
  but have **persistent effect**; log them with `start_date` = effective date and
  `end_date` = `ongoing` (or the next superseding policy). The 6 h floor does not
  apply to `policy`.
- `holiday`: excluded from the event file entirely (derive from calendar, §4).

---

## §7 — Schema to implement (BUILD step only; confirmed, not yet created)

One row per event. CSV header, in order:

```
event_id,start_date,end_date,date_precision,event_type,direction_affected,crossings_affected,severity,confidence,description,source_url,source_type,date_recorded,last_verified
```

| field | type | rule (validator enforces) |
|---|---|---|
| `event_id` | str | stable, unique, non-empty |
| `start_date` | ISO date | required, parses |
| `end_date` | ISO date \| `ongoing` | parses; `start_date ≤ end_date` |
| `date_precision` | enum | `exact`/`day`/`week`/`month` |
| `event_type` | vocab | `blockade`/`strike`/`policy`/`closure`/`infrastructure`/`security`/`weather`; hard-fail otherwise |
| `direction_affected` | enum | `PL_UA`/`UA_PL`/`both`/`unknown` |
| `crossings_affected` | `;`-list \| `corridor` | every token ∈ `CANONICAL_NAMES` or the literal `corridor` |
| `severity` | enum | `low`/`med`/`high` (rubric §4) |
| `confidence` | enum | `confirmed`/`reported`/`unconfirmed` |
| `description` | str | 1–2 sentences, non-empty |
| `source_url` | url | **required, non-empty** — no unsourced rows |
| `source_type` | enum | `gov`/`wire`/`outlet`/`portal_notice` |
| `date_recorded` | ISO date | required |
| `last_verified` | ISO date | required |

---

## §8 — Build plan (ONLY after this report is reviewed)

1. Scaffold `data/corridor_events.csv` with the §7 header + a documented severity
   rubric and the inclusion criterion in a sibling `data/corridor_events.README.md`
   (or top-of-file comment is not possible in CSV → use a README). **No fabricated rows.**
2. **Validator** `events_validate.py` (hard-failing, matching `dpsu_validate.py`
   shape): dates parse; `start_date ≤ end_date`; required fields present;
   `event_type` ∈ vocab; every `crossings_affected` token valid against
   `crossings.CANONICAL_NAMES`; `source_url` present. Unknown inputs fail loudly.
3. **Join helper** (the payoff): given a series observation `(crossing C, time T)`,
   return all events `E` where (`C ∈ E.crossings_affected` **or** `E` is
   `corridor`) **and** `E.start_date ≤ T ≤ E.end_date` (`ongoing` → open-ended).
   Mirror the read-only, label-aligned conventions of `analysis/join_divergence.py`.
4. Backfill entries **only** under §6, each live-sourced, each with honest
   `confidence` + `last_verified`.

---

## §9 — Verification plan (prove it; build step)

- Validator passes on the populated file; inject one bad row (unknown crossing
  code) and one with inverted dates → confirm **both fail**.
- Join helper: pick one known event window + one series, confirm the overlap
  query returns expected annotated observations and excludes out-of-window ones
  (the empirical-confirmation pattern used for the percentile baseline).
- Spot-check every committed row has a resolving `source_url`.

---

## §10 — Decision requested

**Proceed to build** the event log as specified (CSV at `data/corridor_events.csv`,
committed; hard-failing `events_validate.py`; overlap join helper; manual-append
capture; ≥6 h inclusion floor; window from 2026-06-12), **or** adjust any of:
the **N=6 h** floor, the **`corridor` vs per-crossing** default, the **filename**,
or the **holiday=derive** call — before any file or row is created.

**Nothing is built until this is reviewed and the decision is made.**

---

### Source provenance for this recon (channels confirmed reachable 2026-06-28)

- PAP — https://www.pap.pl/aktualnosci/trwa-protest-przewoznikow-i-rolnikow-na-granicy-z-ukraina
- logistyka.rp.pl — https://logistyka.rp.pl/drogowy/
- EU Mobility & Transport (agreement to 31 Mar 2027) — https://transport.ec.europa.eu/news-events/news/eu-and-ukraine-extend-road-transport-agreement-until-31-march-2027-2025-09-25_en
- Cabinet of Ministers of Ukraine — https://www.kmu.gov.ua/en
- Ukrinform — https://www.ukrinform.net/
- Straż Graniczna komunikaty — https://www.strazgraniczna.pl/pl/aktualnosci
- granica.gov.pl — https://granica.gov.pl/
- DPSU Telegram (public web view) — https://t.me/s/DPSUkr
- dpsu.gov.ua — https://dpsu.gov.ua/uk
