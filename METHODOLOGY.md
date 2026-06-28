# METHODOLOGY

Notes on how each data series is collected and how they may (and may not) be
combined. Read alongside the per-series RECON_*.md documents.

## Two kinds of log — instrument vs. world (read this first)

The repo keeps two deliberately separate event records. The split is by *cause*,
not severity, and conflating them would corrupt analysis:

- **Instrument events → `INCIDENTS.md`.** Faults in our *measurement apparatus*:
  scraper bugs, schema drift, GitHub Actions cadence gaps (e.g. INC-001 the
  granica `rok` error, INC-002 schedule-not-firing). These affect whether the
  series was *recorded correctly*.
- **World events → `data/corridor_events.csv`.** Real-world disruptions at the
  border itself: blockades, strikes, policy changes, closures. These affect what
  the series was *measuring*. See `data/corridor_events.README.md` and
  `RECON_event_log.md`.

A spike caused by an instrument fault must be discounted; a spike caused by a
world event is signal to be explained. Keeping the two in different files makes
that distinction mechanical rather than a judgment call at analysis time.

## Series overview

| Series | Source | Direction | Metric | Cadence |
|---|---|---|---|---|
| A — granica | granica.gov.pl SOAP | PL→UA (wyjazd / eastbound) | physical **wait minutes** | ~8×/day |
| B — eCherga | back.echerha.gov.ua | UA→PL (westbound) | **virtual** queue (seconds, booked count) | ~30 min |
| C — DPSU | dpsu.gov.ua/uk/map | UA→PL (westbound) | physical **trucks waiting** (count) | sub-hourly |
| **Baseline** — monthly traffic | **dane.gov.pl 2708** | **both** (z RP / do RP) | monthly **vehicle counts** | **monthly** |

## Corridor event log (`data/corridor_events.csv`)

A hand-curated, sourced record of real-world freight disruptions used to annotate
anomalies across Series A–C — the interpretive backbone that turns an unexplained
spike into a natural experiment, and the layer that unlocks the C-vs-B divergence
question (do physical and virtual queues decouple precisely inside blockade
windows?). It is the repo's **only hand-curated, non-reproducible** layer, which
inverts several live-logger rules: it is **committed** (irreplaceable, vs the
re-derivable 2708 baseline which is gitignored), lives as **hand-editable CSV**
(not a DB), is **sourced not remembered** (every row cited), and is governed by a
**pre-registered inclusion rule** that removes curation bias at capture time.
Schema, rubric, and the source watchlist are in `data/corridor_events.README.md`;
design rationale in `RECON_event_log.md`.

### No-contamination property (a provenance strength)

The data window is `[2026-06-12 → today]` — granica's first observation is the
earliest of the four series. The whole window **postdates the model knowledge
cutoff (Jan 2026)**, so model-memory leakage into the log is not merely forbidden,
it is **impossible**: every row is guaranteed live-sourced. Backfill never goes
earlier than 2026-06-12 — events predating series collection cannot confound data
that does not exist.

### What 16 days does and does not support

The window supports the **structural-asymmetry** read (true at any window length)
but **no capacity-trend claim** (far too short). A near-empty log is itself a
finding: it characterises the baseline as **normal operations** — a caveat to
state, not to hide. As of 2026-06-28 the file is header-only: a live survey found
no qualifying freight-disruption event in-window (two candidates — a Medyka
bus-lane rebuild and a seasonal passenger-traffic surge — were correctly excluded
by the freight-only inclusion rule; see the README).

### Joining

`events_log.events_for(events, crossing, ts)` returns every event whose scope
covers `crossing` and whose date window contains `ts`. Scope is either explicit
canonical codes or one of two system-wide sentinels — `corridor` (asserted
system-wide) vs `unknown` (scope not yet investigated) — both of which annotate
all crossings but stay distinguishable in the raw row. Materiality is a query-time
filter (`include_below_floor=False`), never a deletion: short events are tagged
`below_materiality_floor`, not dropped, so filtered views stay recomputable.

## Monthly traffic baseline (dataset 2708)

Source: **Komenda Główna Straży Granicznej** (Polish Border Guard) via
**dane.gov.pl dataset 2708**, per-crossing family *"ruch graniczny środków
transportu drogowego w dpg z podziałem na odcinki i przejścia (GDDKiA)"*.
Collected by `monthly_traffic_fetch.py` into `data/monthly_traffic.db`
(table `monthly_traffic`). This is the chosen **volume baseline** for Series A
(the granica SOAP feed carries wait time only — no throughput count; see the
recon handoff). It subsumes the previously-planned Eurostat/GUS baseline with a
cleaner licence.

**Licence: CC0 1.0.** Attribution is not legally required, but we cite the source
(Border Guard via dane.gov.pl 2708) as good practice.

### What it is — and how it may be joined
- **Monthly, restated, ~1–2 month lag.** Recent months first publish as zero,
  then get **backfilled** in a later vintage of the same per-crossing file. So
  this is a **baseline, never live**. The fetcher re-pulls the current year and
  **UPSERTs** (last-write-wins); a backfilled month overwrites the earlier zero,
  and `source_resource_id` records which vintage the stored value came from.
- **Aggregate-only joins.** Because it is month-level counts and Series A is
  minute-level wait readings, the two join **only at aggregate level** (e.g.
  monthly mean wait vs monthly volume per crossing) — **never row-level**.
- **Both directions captured.** `z_RP` (exit from Poland = PL→UA **eastbound**) is
  the Series-A denominator; `do_RP` (entry to Poland = UA→PL **westbound**) is a
  bonus volume denominator for normalising Series C / DPSU. So this one baseline
  supports both the eastbound and westbound physical series.
- **Direction validation (`z_RP` is not transposed).** The April 2026 eastbound
  truck volume (`truck`/`z_RP`) ranks Dorohusk 17,683 > Korczowa 10,821 > Hrebenne
  8,706 > Medyka 6,120 … Małhowice 0 — the same crossing ordering as where Series-A
  (granica) eastbound waits are highest. That agreement confirms `z_RP` is the
  PL→UA export direction and has not been swapped with `do_RP`. (This is the
  direction-validation for this layer, analogous to `direction_check` for Series C.)
- **Vehicle scope (current):** `truck` (samochody ciężarowe) and `total` (RAZEM
  łączny ruch graniczny), registration `all`. Cars/buses and the foreign/Polish
  (obce/polskie) registration split are schema-ready but not yet populated.

### Caveats / data categories
- **Małhowice vs Medyka (corrected from recon).** The recon noted identical
  values for Małhowice–Niżankowice and Medyka–Szeginie and assumed joint
  reporting. That identity exists **only in the annual `Razem` aggregate sheet**
  (which duplicates Medyka's total into the Małhowice row) — and we do **not**
  ingest aggregate sheets. In the **monthly** sheets we do ingest, the two are
  **genuinely separate**: Małhowice is a small passenger crossing with real car
  traffic but, so far, **zero trucks**; Medyka is the large crossing with its own
  figures. So Małhowice is stored **independently** (its zero-truck months are
  genuine zeros, not joint-reporting). The fetcher keeps a guard: if a *monthly*
  sheet ever shows the two as identical across all fields (the aggregate-sheet
  duplication leaking in), it collapses Małhowice to `count = NULL,
  joint_reported_with = 'medyka'` to prevent a double-count and records the
  anomaly once in INCIDENTS.md.
- **Absent ≠ zero.** A monthly sheet that is entirely zero across all crossings is
  treated as **not-yet-published** and skipped (so a later pull fills it).
  Genuine zeros *within* a published month are stored as `0`.
- **Aggregate sheets ignored.** Only the monthly sheets (`I`–`XII`) are ingested;
  the Razem / quarter / half sheets are derivable aggregates and would
  double-count.
- **Layout is asserted.** The multi-row merged header is mapped and every expected
  label position is checked; any drift hard-fails rather than mis-reading columns.
- **Crossing names:** mapped from the 2708 row labels (the Polish, left-of-dash
  name). Dataset 2090 ("Wykaz przejść granicznych") is **not** used — recon found
  it stale (only 4 of 9 UA road crossings); the 2708 file is the better authority.
- **Registration dimension — never sum across it.** `registration='all'` is the
  per-direction total and **already equals** foreign (`obce`) + Polish (`polskie`).
  The foreign/Polish split is schema-ready but unpopulated; when it is added,
  `'all'` and `{'foreign','polish'}` are **mutually exclusive views of the same
  figure**, so rows must never be summed across the registration dimension —
  summing `all + foreign + polish` doubles every total.
