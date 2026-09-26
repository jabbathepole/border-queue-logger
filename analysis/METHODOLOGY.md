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
  stale)."* **Measured 2026-07-08, clean window (`--window-start
  2026-06-27T00:00:00Z`, 1 h buckets): `1372/2090` filled buckets within cutoff —
  `34%` excluded as stale.** (Over the *full* history the figure is inflated to
  ~60% because forward-fill smears the last pre-blackout reading across the whole
  216 h INC-003 hole; always measure C-vs-B staleness on the clean window.)
- **Follow-up — resolved 2026-07-08 (see [`dpsu_coverage.py`](dpsu_coverage.py) /
  B1).** The 6 h default was checked against the real inter-update gap
  distribution. DPSU's daytime cadence is a ~3.1 h median gap, so daytime fills
  comfortably clear 6 h; the exclusions are dominated by the **overnight dead
  zone** (no readings 22:00–02:59 UTC, recurrent ~9 h evening→morning gaps). Of
  the 718 stale exclusions, **~58 % fall in the 22:00–05:59 UTC band** (the night
  gap and its aging tail, peaking 02:00–05:00), the rest concentrated on its
  early-morning/evening shoulders. So the cutoff mainly removes structurally-
  absent night coverage, not mid-day staleness — 6 h remains the right default,
  and **no diurnal claim may rest on C** (it is daytime-conditioned; see the
  C-vs-B diurnal note below).

### Diurnal coverage — Series C is daytime-conditioned (B1, 2026-07-08)

DPSU does not publish uniformly around the clock. Measured over the clean window
by [`dpsu_coverage.py`](dpsu_coverage.py): **native readings occur only in UTC
hours 03–21; there are zero readings 22:00–02:59** — a recurrent overnight dead
zone (evening→morning gaps of ~9 h, occasionally ~18 h on a full-day skip). The
effective rate is ~4–5 readings/day, roughly half what a flat "~3 h refresh"
implies. This supersedes the earlier "~2.5–3 h, occasionally skips"
characterisation (see the `RECON_dpsu_map.md` addendum).

Consequences, which every C-vs-B statistic inherits:
- **Series C is daytime-conditioned.** Any C-vs-B comparison is effectively a
  daytime comparison; the 6 h staleness cutoff structurally excludes most night
  buckets (≈58 % of stale exclusions fall in 22:00–05:59 UTC — see above).
- **No diurnal claim may rest on C.** C cannot speak to overnight queue dynamics;
  it does not observe them. A "queues fall overnight" reading would be an artefact
  of coverage, not a finding.
- **This is documented NORMAL source behaviour, not a fault.** Do **not** add any
  alerting on overnight DPSU staleness — the overnight gap is expected. (The one
  DPSU condition worth alerting on, a source outage, is INC-003's 403 class, not
  a nightly gap; and per PR-C scope there is no DPSU-side queue guard.)

### Sufficiency gate for `dpsu_rank`

A crossing needs ≥ `--min-buckets` **distinct native DPSU readings** before its
`dpsu_rank` is trusted (same philosophy as the A-vs-B `min_buckets` gate); below
that the percentile distribution is degenerate and `dpsu_rank` is left `None`.

### Standing limitation (label this in the writeup)

C is a **coarse ~3 h feed forward-filled onto a finer grid**. The C-vs-B
divergence is **only trusted within the freshness cutoff**; stale fills are
reported raw but never ranked or compared.

### Paused sub-queues in count comparisons — INCLUDE them (B2/B3/B4, 2026-07-08)

The A-vs-B pipeline (`load_virtual`) drops `is_paused=1` sub-queues because its
default metric is a **wait time**, which is stale while metering is suspended.
For a **count** comparison against DPSU (`vehicles_waiting` vs C) that exclusion
is *wrong*: a paused sub-queue's booked trucks are still a real physical backlog.
Excluding them decorrelates the crossings with heavy paused queues — e.g.
Dorohusk `truck_empty` is paused on ~29 % of polls, and dropping it collapses the
clean-window `r(C, B_sum)` from **+0.994 to +0.047** and inflates the level gap
from 0.6 % to 24 %. So the C-vs-B level/decomposition/direction analyses
(`class_decomposition.py`, the direction check, the B2 table below) sum truck
sub-queues **with paused included**; `dpsu_echerha_comovement.load_echerha` grew
an `include_paused` flag for this. (This is a known inconsistency vs the A-vs-B
pipeline's blanket exclusion — logged, not "fixed" here, since re-baselining the
A-vs-B numbers is out of scope; analogous to the phys_rank baseline note above.)

### Outlier resolution — Zosin is not an outlier on the clean window (B2, 2026-07-08)

A prior pass flagged Zosin as a C-vs-B outlier (r ≈ 0.911). Re-derived on the
clean window (`>= 2026-06-27`, 1 h buckets, paused-included counts) it is
**not** an outlier: **r_levels +0.981, r_diff +0.816, median |gap| 2.6 %**
(n = 60) — squarely in the pack with the other loaded/restricted crossings. The
earlier flag was contamination: it predated the clean-window floor, so the single
pre-blackout 2026-06-18 DPSU day sat in the baseline, and it used the paused-
excluded collapse. Both are corrected above. Full nine-crossing table (clean
window, C = native DPSU, B = paused-included truck-class sum):

| crossing | n | r_levels | r_diff | lag-1 AC(C) | med B | med C | med \|gap\|% | tier |
|---|---|---|---|---|---|---|---|---|
| dorohusk | 60 | +0.994 | +0.952 | +0.860 | 2034.0 | 2051.0 | 0.6 | heavy |
| korczowa | 44 | +0.998 | +0.985 | +0.923 | 398.2 | 397.5 | 0.8 | heavy |
| hrebenne | 44 | +0.999 | +0.990 | +0.887 | 451.2 | 448.0 | 0.8 | heavy |
| medyka | 42 | +0.999 | +0.993 | +0.896 | 572.7 | 569.5 | 0.4 | heavy |
| zosin | 60 | +0.981 | +0.816 | +0.907 | 328.0 | 328.5 | 2.6 | restricted |
| dolhobyczow | 44 | +0.984 | +0.978 | +0.897 | 370.0 | 284.0 | 32.5 | restricted |
| budomierz | 44 | +0.864 | +0.846 | +0.219 | 6.6 | 6.0 | 10.6 | marginal |
| kroscienko | 40 | −0.068 | −0.003 | −0.026 | 2.0 | 0.0 | — | marginal |
| malhowice | 0 | — | — | — | — | — | — | marginal |

(Dołhobyczów's 32.5 % gap is the population-definition effect below, not a data
error; kroscienko's near-zero r is the degenerate-marginal case, B6.)

### DPSU population definition at Dołhobyczów (B3, 2026-07-08) — strongly-supported hypothesis, not settled fact

At most crossings C matches the eCherga truck-class **sum** (dorohusk gap 0.6 %,
zosin 2.6 %). At **Dołhobyczów it does not**: decomposing B by class (clean
window, n = 44) shows C tracks the **`truck_empty` (empty ≥ 7.5 t) sub-queue
alone** — median B_empty 284.0 vs median C 284.0, **median |gap| 0.7 %**,
median(B−C) +0.3, r_levels +1.000 — while the loaded 3.5–7.5 t queue
(`truck_le_7_5t`, median 84.8) and hence the class **sum** (median 370.0, gap
32.5 %) diverge from C. Controls confirm the contrast: at dorohusk and zosin C
matches B_sum (gaps 0.6 % / 2.6 %), and zosin's `truck_empty` **alone** misses
(gap ~23 %).

**Hypothesis (strongly supported, not proven):** DPSU's `trucks_waiting` at
Dołhobyczów counts the **empty ≥ 7.5 t** approach population and **excludes** the
loaded 3.5–7.5 t queue — plausibly because the physical approach lane / sensor
DPSU reads covers only the empty-truck lane at this small restricted crossing.

Consequences:
- **Per-crossing DPSU population definitions may differ.** C is not guaranteed to
  be "all trucks" everywhere; at Dołhobyczów it is a sub-population.
- **Dołhobyczów C-vs-B comparisons must use `B_empty`, not `B_sum`.** Using the
  sum manufactures a spurious 32 % divergence.

Verification path (maintainer): watch whether C stays locked to `B_empty` through
a future divergence of the empty vs loaded volumes (if they move apart and C
follows empty, the hypothesis holds); and inspect the DPSU map legend/tooltip
semantics at Dołhobyczów for what population the figure claims to count.

**This per-class mismatch is additional evidence of source INDEPENDENCE.** If
DPSU and eCherga were the same feed dressed differently, C could not track one
eCherga sub-queue while ignoring another at one crossing yet track the sum
elsewhere. The crossings therefore corroborate each other as genuinely separate
instruments.

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

**Both levels and first differences are reported** (`r_lvl`, `r_diff`). Queue
series are highly persistent (lag-1 autocorrelation of C at the big four
0.86–0.92), so a high *levels* correlation partly reflects shared slow drift and
overstates evidential weight. The **differenced** correlation asks whether the
hour-to-hour *changes* co-move — the version of the corroboration that survives
the persistence objection — and it holds strongly. The C-vs-B leg uses
paused-included truck counts (see the paused note above).

- **Regenerate:** `python -m analysis.direction_check [--bucket-hours 1]`
  (default `--window-start 2026-06-27T00:00:00Z`, the post-INC-003 clean window)
  where `data/{dpsu,echerha,queues}.db` are present.
- **Result (measured 2026-07-08, clean window, 1 h buckets): direction
  SUPPORTED at all 7 crossings with a truck queue; 0 flagged.** Per crossing,
  `r(C,B)` (same direction) vs `r(C,A)` (opposite):

  | crossing | med C | r(C,B) lvl | r(C,B) diff | r(C,A) lvl | verdict |
  |---|---|---|---|---|---|
  | dorohusk | 2051 | +0.994 | +0.952 | +0.554 | SUPPORTED |
  | korczowa | 398 | +0.998 | +0.985 | −0.306 | SUPPORTED |
  | hrebenne | 448 | +0.999 | +0.990 | +0.058 | SUPPORTED |
  | medyka | 570 | +0.999 | +0.993 | +0.413 | SUPPORTED |
  | zosin | 328 | +0.981 | +0.816 | −0.415 | SUPPORTED |
  | dolhobyczow | 284 | +0.984 | +0.978 | −0.203 | SUPPORTED |
  | budomierz | 6 | +0.864 | +0.846 | +0.227 | SUPPORTED |
  | kroscienko | 0 | −0.068 | −0.003 | n/a | thin (degenerate marginal) |
  | malhowice | — | — | — | — | no truck queue |

  Every truck-bearing crossing tracks eCherga (same direction) far more tightly
  than granica (opposite), on both levels and differences. Magnitude corroborates:
  Dorohusk med ~2,051 trucks (the westbound backlog scale), near-zero at the
  marginal crossings. kroscienko/malhowice are the degenerate marginal tier (B6),
  not evidence against direction.

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

## Data-sufficiency status (measured 2026-07-08)

The thin-window caveat that opened this section (one eCherga snapshot, zero
`complete` rows) is **resolved**. Measured over the full window
(`2026-06-12 → 2026-07-08`, 1 h buckets) by `python -m analysis.join_divergence`
(read from `per_crossing_summary.csv`), **all 8 truck-bearing crossings clear the
`--min-buckets = 24` gate** — Steps 2–5 are now computable:

| crossing | n complete buckets | ≥ 24? | tier |
|---|---|---|---|
| dorohusk | 253 | yes | heavy |
| medyka | 246 | yes | heavy |
| korczowa | 218 | yes | heavy |
| hrebenne | 144 | yes | heavy |
| dolhobyczow | 247 | yes | restricted |
| zosin | 216 | yes | restricted |
| kroscienko | 222 | yes | marginal (degenerate) |
| budomierz | 108 | yes | marginal (degenerate) |
| malhowice | 0 | no | marginal (no truck queue) |

Malhowice never qualifies: it has no eCherga truck queue (bus id 102 only), so it
has no complete truck bucket and is correctly absent from all per-crossing truck
statistics. The marginal crossings clear the *count* gate but their near-zero
medians make ranks/correlations noise (B6) — report them flagged degenerate.

`--min-buckets` (default 24) still gates who earns statistics; the earlier "do
not interpret until multiple crossings clear that gate" warning is now satisfied
for the heavy and restricted tiers. (Note: A-vs-B remains the *cross-direction*
asymmetry proxy — a high complete-bucket count does not upgrade it to a same-flow
comparison; that is what C-vs-B is for.)

## Crossing tiers — the freight scope is not uniform (B6, 2026-07-08)

"Nine canonical crossings" conflates four freight regimes. `crossings.py`
exposes them as `CROSSING_TIER`; the evidence (eCherga truck classes present,
clean-window median B = paused-included truck-class sum and median C = native
DPSU, and dataset-2708 April-2026 `truck`/`z_RP` volume):

| crossing | tier | eCherga truck classes | med B | med C | 2708 truck (Apr) |
|---|---|---|---|---|---|
| dorohusk | **heavy** | empty, ge_7.5t, goods_1_24 | 2034 | 2051 | 17,683 |
| korczowa | **heavy** | ge_7.5t, goods_1_24 | 398 | 398 | 10,821 |
| hrebenne | **heavy** | ge_7.5t, goods_1_24 | 451 | 448 | 8,706 |
| medyka | **heavy** | empty, ge_7.5t | 573 | 570 | 6,120 |
| zosin | **restricted** | empty, le_7.5t | 328 | 329 | 4,275 |
| dolhobyczow | **restricted** | empty, le_7.5t | 370 | 284 | 4,044 |
| budomierz | **marginal** | le_7.5t | 6.6 | 6.0 | 2,262 |
| kroscienko | **marginal** | le_7.5t | 2.0 | 0.0 | 752 |
| malhowice | **marginal** | *bus only (id 102)* | — | — | 0 |

- **heavy** — a loaded ≥ 7.5 t queue exists; large physical backlog. These carry
  the corridor's freight signal.
- **restricted** — **no** loaded ≥ 7.5 t queue (only le_7.5 t + empty). Freight
  scope is genuinely narrower, not merely quieter — relevant when reading their
  levels against the heavy crossings, and the cause of the Dołhobyczów
  population-definition effect above.
- **marginal** — degenerate truck queue: le_7.5 t only with near-zero medians
  (budomierz ~6, kroscienko ~0), or **no truck queue at all** (malhowice). Note
  2708 shows budomierz/kroscienko *do* pass trucks monthly (2,262 / 752) — they
  just don't **queue**, so the *queue* series is degenerate even though volume is
  nonzero.

**Rule:** per-crossing statistics are reported for the **heavy + restricted**
tiers; **marginal-tier statistics are computed but flagged degenerate** — their
near-zero medians make ranks and correlations noise (clean-window kroscienko
`r(C,B) ≈ −0.07`). Do not headline a marginal-tier number.

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
