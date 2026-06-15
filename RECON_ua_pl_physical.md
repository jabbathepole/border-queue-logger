# Recon — UA→PL physical-wait source (for a true same-direction comparison)

Opened 2026-06-15 per the join-analysis brief. Goal: find a **physical** wait
feed for the **UA→PL** direction so it can be compared like-for-like against
eCherga's **UA→PL virtual** queue — closing the directional-asymmetry gap
documented in `analysis/METHODOLOGY.md §Direction`.

## Why this matters

- granica.gov.pl publishes only `wyjazd` = **PL→UA** physical (outbound from PL);
  its `wjazd` (UA→PL) columns are always NULL.
- eCherga's virtual queue is **UA→PL** (trucks leaving Ukraine).
- So today's only data-bearing join crosses directions (PL→UA physical vs UA→PL
  virtual). A UA→PL **physical** source makes a genuine same-direction
  physical-vs-virtual divergence possible.

## Finding: nakordoni.eu — viable, free, documented JSON API ✅

`https://nakordoni.eu` (UA mirror `nakordoni.com.ua`) aggregates border-queue
data and exposes a public developer API.

- **Coverage:** all 9 PL-UA crossings, **both directions** (UA→PL *and* PL→UA),
  by vehicle type (freight by weight band, buses, cars, tax-free, pedestrians).
- **Metric:** estimated wait time in **minutes** + timestamp + per-entry data
  source. (Same unit as granica → if used for the PL side too, no unit conversion.)
- **Sources (stated):** State Border Guard Service of Ukraine (**DPSU**) + Polish
  Border Guard, plus official cameras and verified user reports. Refresh ~1–15 min
  (site says 1 min live; queue data "every 15 minutes from DPSU / Polish BG").
- **API:** documented at `https://nakordoni.eu/en/developers`.
  - Auth: `Authorization: Bearer NKD-DEV-XXXX-XXXX-XXXX`.
  - JSON envelope (versioned, includes remaining quota).
  - Endpoints: `/api/v1/data/checkpoints` (directory), `/api/v1/data/queue?ppid=…`
    (live queue), `/api/v1/data/forecast?ppid=…&prediction_steps=24` (ML forecast),
    plus fuel / POIs.
  - **Free "Explorer" plan:** 1 000 calls/day, 2 req/s, **email verification only,
    no card**. Requires a visible attribution link. Paid from €0.70 / 1 000 calls.

### Implication for this project

This is materially easier than the existing two loggers (no SOAP, no Akamai/TLS
fight). One nakordoni logger could:
1. Provide **UA→PL physical** wait (DPSU) — the missing same-direction partner for
   eCherga's UA→PL virtual queue → enables true physical-vs-virtual divergence.
2. Cross-check / supplement granica's **PL→UA** side (nakordoni's Polish-BG feed).

### Open questions before building a logger

- Map nakordoni `ppid` checkpoint ids → our canonical crossing ids
  (`crossings.py`). Pull `/api/v1/data/checkpoints` once a key is issued.
- Confirm the `queue` response carries **direction** and **vehicle band**
  explicitly (so UA→PL trucks can be isolated), and what the per-entry
  `data source` values are (raw DPSU vs camera vs user report) — provenance matters
  for the methodology; prefer DPSU-sourced rows.
- Terms of use / attribution: free tier needs a visible attribution link — fine
  for a public repo, note it in README.
- 1 000 calls/day ≫ a 15-min cadence for 9 crossings × 2 directions, so the free
  tier is ample.

## Secondary sources (lower priority)

- **DPSU** `https://dpsu.gov.ua/uk/map` — official interactive map + checkpoint
  cameras. Authoritative but appears to be a map UI, not an obvious public JSON
  feed; nakordoni already ingests it. Revisit only if nakordoni's API terms or
  provenance prove unusable.
- **Держмитслужба (UA Customs)** — customs throughput, not queue wait; not a
  direct substitute.
- kyivpost / ua-migrant / vsetutpl — secondary write-ups and camera lists, not
  data feeds.

## Recon ask #2 — does eCherga expose the PL→UA (into-Ukraine) virtual queue?

**No (structural).** eCherga (Електронна черга) is Ukraine's **outbound** border
queue — drivers register to **leave Ukraine**. The workload API
(`/api/v4/workload/{carrierType}`) returns Ukraine-exit queues filtered by
*destination* country (`country_id`); there is **no direction parameter** and no
"entering Ukraine" queue, because entry to Ukraine is not eCherga-metered (it's
the destination state's exit that is queued). So eCherga is single-direction
(UA→PL) by design; a PL→UA virtual queue does not exist on eCherga.
→ A same-direction *virtual* comparison for the PL→UA flow is not available from
eCherga; the same-direction opportunity is on the **UA→PL** side (eCherga virtual
↔ nakordoni/DPSU physical), per the finding above. (A live `curl` of the workload
endpoints can confirm there is no direction field, but the API shape captured in
`RECON_echerha.md` already shows none.)

## Recommended next step

Sign up for a nakordoni Explorer key (email only), pull `/api/v1/data/checkpoints`,
and confirm the queue response exposes direction + DPSU provenance for UA→PL
trucks. If it does, a small `nakordoni_scraper.py` (mirroring the existing loggers,
shared `crossings.py`) gives the same-direction physical feed the analysis needs.
