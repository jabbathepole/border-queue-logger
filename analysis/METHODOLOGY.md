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
| `--min-buckets` | 24 | min complete buckets before a crossing gets stats |
| `--lag-hours` | 24 | cross-correlation lag scan (±) |
| `--virtual-metric` | `virtual_wait_s` | or `vehicles_waiting` |
| `--truck-wait-agg` | `max` | collapse eCherga truck sub-queue waits: `max`/`mean` |
| `--window-start` / `--window-end` | none | restrict observation window (ISO `…Z`) |
| `--charts` | off | emit per-crossing PNGs (needs matplotlib) |
