# Methodology — PL-physical vs UA-virtual join & divergence

Read-only analysis joining the two loggers in this repo:

- **Physical (PL):** `granica.gov.pl` SOAP feed → `data/queues.db` / `queue_records`.
  Truck **physical wait time in minutes** at the Polish crossing.
- **Virtual (UA):** `eCherga` workload API → `data/echerha.db` / `echerha_records`.
  The Ukrainian **electronic-queue** state: estimated **virtual wait in seconds**
  (`virtual_wait_s`) and **vehicles booked** in active queues (`vehicles_waiting`).

The analysis (`analysis/join_divergence.py`) opens both loggers' SQLite files in
**read-only** mode (`?mode=ro`) and writes only to `analysis/output/`. It never
writes to, locks, or alters the loggers' databases or the live GitHub Actions
pipelines.

> Run it where the data lives (the `master` branch / any checkout containing
> `data/queues.db` **and** `data/echerha.db`). To analyse without checking out
> master, extract read-only copies first:
> `git show origin/master:data/echerha.db > /tmp/echerha.db` etc.

---

## ⚠️ Direction — the single most important caveat

**This is a directional-asymmetry comparison, not a same-flow divergence.**

- granica publishes only `wyjazd` = trucks **leaving Poland → PL→UA (outbound)**.
  The `wjazd` (entering Poland → UA→PL) columns exist in the schema but are
  **always NULL** — the Polish source does not publish them (confirmed: 0 of 132
  rows populated across all crossings/timestamps).
- eCherga's queue is for trucks **leaving Ukraine → UA→PL (inbound to Poland)** —
  i.e. the *same physical direction granica leaves empty*.

So the only same-direction pair (granica `wjazd` ↔ eCherga) has **no Polish data**,
and the only data-bearing pair **crosses directions**:

| Side | Feed | Physical direction of travel |
|------|------|------------------------------|
| Physical | granica `wyjazd` | **PL → UA** (outbound from Poland) |
| Virtual  | eCherga          | **UA → PL** (inbound to Poland)   |

These are the **two opposing flows of the same corridor**, not the same flow.
The pipeline labels every output with `physical_direction=PL_to_UA_outbound` and
`virtual_direction=UA_to_PL_inbound` so this can never be read as like-for-like.
In the article this must be stated plainly: we are asking whether *outbound*
physical pressure and *inbound* virtual backlog at the same crossing move
together — a corridor-level question — **not** comparing one direction's physical
vs virtual wait. A true same-direction physical-vs-virtual divergence requires a
UA→PL **physical** source (recon ongoing — see `RECON_ua_pl_physical.md`).

---

## Step 0 — what each field means

| Concept | Physical (granica) | Virtual (eCherga) |
|---|---|---|
| Wait metric | `trucks_exit_min` — minutes | `virtual_wait_s` — seconds (default), multi-day is normal (e.g. Dorohusk empties ≈ 363 000 s ≈ 100 h) |
| Volume metric | — (not published) | `vehicles_waiting` — vehicles booked in active queues |
| Granularity | one truck figure per crossing | several sub-queues per crossing (tonnage / empty / goods-group); buses separate |
| Poll cadence | every ~3 h | every ~30 min |

Because one side is a **time** and the other can be a **count or a time**,
**raw subtraction is meaningless** and normalisation (below) is essential.

**eCherga truck sub-queue collapse.** granica gives a single truck figure per
crossing; eCherga splits trucks into `truck_empty`, `truck_ge_7_5t`,
`truck_le_7_5t`, `truck_goods_1_24`. Per poll we collapse a crossing's truck
sub-queues to one value before bucketing:
- `virtual_wait_s` → **max** across sub-queues (`--truck-wait-agg`, default `max`):
  the worst-case wait a trucker at that crossing faces. `mean` available as alt.
- `vehicles_waiting` → **sum**: total trucks queued at the crossing.

**Paused queues excluded.** Sub-queues with `is_paused=1` are dropped from the
collapse — their wait estimate is stale while metering is suspended. **Buses
excluded** entirely (scheduled-slot queue, `queue_flow=2`, `wait_time` NULL —
not comparable to a live wait).

---

## Step 1 — common time grid

Both feeds are floored (UTC) to a shared bucket (`--bucket-hours`, **default 1 h**).
Within each `(crossing, bucket)` per feed we keep **mean** (primary) plus min/max
for context. Pairing rules:

- match on **canonical crossing id** (`dorohusk`, `korczowa`, …);
- **trucks-to-trucks** only (buses excluded, see above);
- direction is fixed per feed (the asymmetry above) and **labelled**, never crossed
  silently.

**Missing data is never filled.** A bucket with a reading on only one side is
marked `complete=0` and **excluded** from all divergence statistics. No
forward-fill, no interpolation across gaps.

> **Cadence note:** granica's ~3 h poll is the binding constraint on how many
> buckets can ever be `complete`. At a 1 h bucket, most buckets hold only one
> feed. A coarser `--bucket-hours 3` raises overlap at the cost of resolution.

---

## Step 2 — normalisation (per crossing, within the observation window)

Each feed is normalised **within its own per-crossing distribution** over the
window, then compared:

- **Primary — percentile rank** in `[0,1]` (robust to the heavy right-skew of
  queue data; ties counted at-or-below).
- **Cross-check — z-score** (standardised per crossing).

**Divergence (per complete bucket):**

```
divergence = rank_physical − rank_virtual      ∈ [−1, +1]
```

- **Positive** → physical (PL→UA outbound) unusually elevated *relative to its own
  norm* while virtual (UA→PL inbound) is not.
- **Negative** → the reverse.

A `divergence_z = z_physical − z_virtual` is also emitted. If percentile and
z-score disagree on which events are extreme, **prefer percentile** (skew-robust).

### Limitations of this choice (state these in the article)

- **Window-dependent.** Percentile rank is defined relative to the observed
  window. A short window, or a regime shift (e.g. a policy change at a crossing),
  moves the baseline and changes every rank. Re-running on a longer window can
  change which buckets look "extreme."
- **Per-crossing.** Ranks are not comparable *across* crossings in absolute terms,
  only within. A rank of 0.9 at Medyka and at Dorohusk reflect different absolute
  waits.
- **Direction asymmetry (above) is not removed by normalisation** — it is a
  property of *what* is being compared, not *how*.

---

## Step 3 — interpretation quadrant

Using each side's own *elevated* status (above its per-crossing
`--elevated-pct`, **default 75th** percentile), each complete bucket is labelled:

| Quadrant | Meaning |
|---|---|
| `BOTH_ELEVATED` | genuine corridor saturation |
| `PHYSICAL_HIGH_VIRTUAL_NORMAL` | constraint **at the crossing** (customs/booths/infrastructure); eCherga metering keeping up |
| `VIRTUAL_HIGH_PHYSICAL_NORMAL` | eCherga holding trucks **upstream** — demand absorbed as virtual backlog, physical stays low. The hidden congestion a physical-only view misses |
| `BOTH_NORMAL` | corridor flowing freely |

(Interpret the two "high" quadrants through the directional-asymmetry lens: they
relate *outbound* physical to *inbound* virtual.)

---

## Step 4 — ranked decoupling events (the deliverable)

A **decoupling event** = a contiguous run of complete buckets where
`|divergence|` exceeds a per-crossing threshold (`--decouple-pct`, **default the
90th percentile** = top decile of that crossing's `|divergence|`). A gap larger
than 1.5 buckets breaks a run. Per event we output: crossing, direction note,
start/end (UTC), duration, n_buckets, peak `|divergence|`, mean divergence,
which side was elevated, dominant quadrant, and a ranking
**score = peak |divergence| × duration_h** (biggest + most sustained first).

This ranked table (`decoupling_events.csv`) is what the article is built around.

---

## Step 5 — lead/lag cross-correlation

Per crossing, Pearson correlation of the two normalised series across lags
`−lag_hours … +lag_hours` (`--lag-hours`, **default ±24 h**).

**Sign convention (validated against a known-shift synthetic series):**

```
corr( physical[t] , virtual[t + lag] )
  positive lag  →  physical LEADS, virtual lags
  negative lag  →  VIRTUAL LEADS physical   ← the brief's hypothesised
                                               "eCherga backlog as leading indicator"
```

We report **baseline correlation at lag 0**, the **peak-correlation lag**, the
peak correlation **value**, and the number of overlapping pairs. A peak at a
flattering lag with `|corr| < 0.3` is flagged **"weak — treat as noise, not a
finding."** Report the correlation value, never just the best lag.

---

## Step 6 — outputs (`analysis/output/`)

- `joined_divergence.csv` + `analysis.db:joined_divergence` — one row per
  `(crossing, bucket)`: both normalised values, divergence (rank + z), quadrant,
  `complete` flag.
- `decoupling_events.csv` — ranked events (Step 4).
- `per_crossing_summary.csv` — n complete buckets, sufficiency flag, baseline +
  peak-lag correlation, event count, quadrant counts.
- `chart_<crossing>.png` (optional, `--charts`, needs matplotlib) — the two
  normalised series overlaid with decoupling windows shaded.
- **(PR 2) `cb_decoupling_events.csv`** — the C-vs-B ranked events, kept SEPARATE
  from `decoupling_events.csv` so the two analyses are never conflated.
- **(PR 2)** `joined_divergence.csv` / `analysis.db:joined_divergence` gain
  `dpsu_rank`, `dpsu_stale`, `cb_divergence_rank`, `cb_quadrant` (the existing
  A-vs-B columns are unchanged, byte-for-byte, for a fixed input).
- **(PR 2) `chart_cb_<crossing>.png`** (optional) — the DPSU vs eCherga
  same-direction series, fresh-enough buckets only.

---

## C-vs-B — same-direction (UA→PL) physical-vs-virtual divergence (PR 2)

The A-vs-B divergence above is a **cross-direction asymmetry** proxy (granica
*outbound* physical vs eCherga *inbound* virtual) — see the Direction caveat. The
DPSU feed (series C) supplies the missing **UA→PL physical** truck count, so
C-vs-B is the **same-direction physical-vs-virtual** comparison the project was
built to make:

| Comparison | Physical leg | Virtual leg | Same flow? |
|---|---|---|---|
| **A-vs-B** | granica `wyjazd` (**PL→UA** outbound) | eCherga (**UA→PL** inbound) | **No** — opposing flows |
| **C-vs-B** | DPSU trucks queued (**UA→PL** physical) | eCherga (**UA→PL** virtual) | **Yes** — same flow |

`cb_divergence_rank = dpsu_rank − virt_rank` (DPSU physical minus eCherga
virtual), built exactly like the A-vs-B `divergence`. `cb_quadrant` uses the same
per-crossing `--elevated-pct` threshold, with physical-vs-virtual labels:

| `cb_quadrant` | Meaning |
|---|---|
| `aligned_busy` | both elevated |
| `physical_only` | real trucks on the ground not reflected in the booking queue |
| `virtual_only` | booking queue inflated vs what's physically present |
| `aligned_quiet` | neither elevated |

### Percentile baseline — full native series, per feed

**Decision: each feed's percentile baseline is its own full native distribution.**
For DPSU specifically (`dpsu_rank`), the baseline is each crossing's **distinct
native readings** — one weight per real `source_updated_utc` — **NOT** the
forward-filled bucket series. Forward-fill repeats one reading across every bucket
until the next update; a reading that precedes a long gap would otherwise be
counted dozens of times and distort the distribution. We rank against the true
reading distribution, then *assign* those ranks to the fresh-enough forward-filled
buckets. `ts_synthetic=1` rows (a poll-time fallback when the source timestamp was
absent) are excluded from the baseline and the forward-fill entirely.

> **Open methodology inconsistency (recorded, not fixed here).** `phys_rank` /
> `virt_rank` (A-vs-B) currently baseline over each crossing's **paired complete
> buckets** (buckets where both granica and eCherga have a reading), not the full
> native per-feed series. `dpsu_rank` (this PR) baselines over the full native
> readings. The two are therefore not built identically. Re-baselining
> `phys_rank`/`virt_rank` over their full native series is deliberately **out of
> scope for this PR** (it would move the existing A-vs-B numbers); it is logged
> here as a known inconsistency to resolve in a dedicated PR.
>
> A consequence worth stating: because `cb_divergence_rank` reuses the existing
> `virt_rank`, C-vs-B is only computed on buckets that are also granica-`complete`
> and on crossings past `--min-buckets`. Decoupling C-vs-B from granica
> availability would require the virt re-baselining above.

### Staleness cutoff — `--dpsu-max-age-hours` (default 6 h)

DPSU is a coarse, **batched, staggered** feed (recon: ~2.5–3 h refresh that
occasionally skips a crossing). Forward-filling it onto a 1 h grid is only honest
for a bounded age. A bucket whose `dpsu_reading_age_s` exceeds the cutoff is
**excluded from `dpsu_rank` and the C-vs-B divergence** (`dpsu_rank=None`,
`dpsu_stale=True`); the **raw** `dpsu_trucks` / `dpsu_reading_age_s` columns are
kept unfiltered for transparency.

- **Default 6 h** = ~2× the nominal ~3 h refresh: loose enough to tolerate the
  staggered/skipped batches, tight enough to drop clearly-stale fills.
- It is a **tunable methodology parameter**, not a magic constant.
- The run prints the **measured** excluded fraction:
  *"freshness cutoff = 6.0 h: N/M filled buckets within cutoff (X% excluded as
  stale)."* **Measured value: pending** — as of 2026-06-18 the committed DPSU
  readings (2026-06-18) and the granica/eCherga history (≤ 2026-06-17) do not yet
  overlap, so 0 buckets carry a fill. Re-run once the loggers accumulate
  overlapping data and record N/M/X% here.
- **Follow-up (don't block):** once a few weeks exist, sanity-check the 6 h
  default against the real per-crossing distribution of inter-update gaps.

### Sufficiency gate for `dpsu_rank`

A crossing needs ≥ `--min-buckets` **distinct native DPSU readings** before its
`dpsu_rank` is trusted (same philosophy as the A-vs-B `min_buckets` gate); below
that the percentile distribution is degenerate and `dpsu_rank` is left `None`.

### Standing limitation (label this in the writeup)

C is a **coarse ~3 h feed forward-filled onto a finer grid**. The C-vs-B
divergence is **only trusted within the freshness cutoff**; stale fills are
reported raw but never ranked or compared.

### Next analytical step (NOT in this PR)

Does the virtual queue (B) **lead** the physical queue (C) by some hours — you
book before you arrive? That lead/lag question reuses the existing `lag_hours`
machinery but against the C-vs-B pair, and is its own PR. Not built here.

---

## Direction validation (`analysis/direction_check.py`, PR 2)

Co-movement of DPSU (C) with eCherga (B) alone cannot prove C is UA→PL — a bad
border day lifts both directions together. The discriminator is the **opposite**
direction: granica (A) is the PL→UA physical wait at the same crossing. The script
reports, per crossing: same-direction `r(C,B)`, opposite-direction `r(C,A)`, a
**verdict** (flag if `|r(C,A)| > |r(C,B)|` — tracks the opposite direction more
tightly), and a **magnitude sanity** check (the westbound UA→PL exit backlog
reaches thousands of trucks — recon's ~2,251 at Dorohusk — so large counts
corroborate the direction).

- **Regenerate:** `python -m analysis.direction_check [--bucket-hours 1]` where
  `data/{dpsu,echerha,queues}.db` are present.
- **Result: pending data.** As of 2026-06-18 the overlap is 0 buckets on both
  legs, so every crossing returns *"insufficient overlap — cannot judge yet."*
  The magnitude leg is already informative (Dorohusk ~2,071; near-zero at
  kroscienko/malhowice). Re-run once C overlaps A and B, and record the verdicts
  here.

---

## Known data-quality incidents

See [`INCIDENTS.md`](../INCIDENTS.md) for the audited log.

- **INC-001 — granica `'rok'` SOAP error (2026-06-12):** a pre-deployment bug
  that failed the very first scrape attempt (`2026-06-12T05:37:00Z`). The
  validator aborted the insert, so **no row was written** — the dataset cleanly
  **starts** at the first successful run `2026-06-12T06:40:00Z`. **No gap and no
  bad data in the series; no analysis exclusion required.**
- **INC-002 — irregular scrape cadence (early window):** GitHub Actions
  schedule unreliability left fewer scrapes than the nominal 3 h cron implies.
  Not corruption — handled by `complete=0` exclusion and `--min-buckets`.
- **INC-003 — DPSU 403 blackout (2026-06-18 → 2026-06-27):** the DPSU source
  returned HTTP 403 to the runner for ~8 days; every run aborted before insert,
  leaving an **8-day interior hole in Series C** (all 9 crossings, boundary
  `2026-06-18T18:00:21Z` → `2026-06-27T18:00:37Z`, 216.0 h). Absence, not
  corruption. **Consequence for this pipeline: the clean C-vs-B window starts
  `2026-06-27` — pass `--window-start 2026-06-27T00:00:00Z` to any C-vs-B run.**

## Data-sufficiency status (as of 2026-06-15)

The eCherga logger went live **2026-06-15** and currently holds **one snapshot**
(`2026-06-15T11:32Z`). With a single virtual timepoint there is **no per-crossing
distribution and no time series**, so Steps 2–5 are **not computable** and the
pipeline reports `INSUFFICIENT DATA` per crossing (it does **not** fabricate
ranks/events/lags). Worse, that single eCherga reading falls in **no bucket that
also contains a granica poll** (granica's nearest are 04:59Z and 12:27Z), so even
the joined table currently has **zero `complete` rows**.

This is expected and self-resolving: eCherga polls every 30 min, granica every
3 h. Re-run as data accumulates; `--min-buckets` (default 24) gates when a
crossing earns real statistics. **Do not interpret divergence/events/lag until
multiple crossings clear that gate** — the article's credibility depends on not
overclaiming from a thin window.

## Parameters (all defaulted, none hard-coded)

| Flag | Default | Meaning |
|---|---|---|
| `--bucket-hours` | 1.0 | common grid bucket size |
| `--elevated-pct` | 75 | per-crossing "elevated" threshold |
| `--decouple-pct` | 90 | per-crossing `|divergence|` event threshold |
| `--min-buckets` | 24 | min complete buckets before a crossing gets stats (also the min distinct native DPSU readings before `dpsu_rank` is trusted) |
| `--dpsu-max-age-hours` | 6.0 | C-vs-B freshness cutoff: drop DPSU fills older than this from `dpsu_rank` / C-vs-B (≈2× the ~3 h refresh) |
| `--lag-hours` | 24 | cross-correlation lag scan (±) |
| `--virtual-metric` | `virtual_wait_s` | or `vehicles_waiting` |
| `--truck-wait-agg` | `max` | collapse eCherga truck sub-queue waits: `max`/`mean` |
| `--window-start` / `--window-end` | none | restrict observation window (ISO `…Z`) |
| `--charts` | off | emit per-crossing PNGs (needs matplotlib) |
