# METHODOLOGY

Notes on how each data series is collected and how they may (and may not) be
combined. Read alongside the per-series RECON_*.md documents.

## Series overview

| Series | Source | Direction | Metric | Cadence |
|---|---|---|---|---|
| A — granica | granica.gov.pl SOAP | PL→UA (wyjazd / eastbound) | physical **wait minutes** | ~8×/day |
| B — eCherga | back.echerha.gov.ua | UA→PL (westbound) | **virtual** queue (seconds, booked count) | ~30 min |
| C — DPSU | dpsu.gov.ua/uk/map | UA→PL (westbound) | physical **trucks waiting** (count) | sub-hourly |
| **Baseline** — monthly traffic | **dane.gov.pl 2708** | **both** (z RP / do RP) | monthly **vehicle counts** | **monthly** |

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
