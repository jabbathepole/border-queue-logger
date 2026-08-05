# border-queue-logger

Time-series loggers for the Poland–Ukraine border crossings. Three independent
scrapers write to separate SQLite tables that share canonical crossing ids
(`crossings.py`) so they join cleanly per `(crossing_id, time)`.

| Logger | Source | Direction | Metric | Files | Table |
|--------|--------|-----------|--------|-------|-------|
| **granica** | `granica.gov.pl` SOAP | PL→UA physical | wait, **minutes** | `scraper.py`, `db.py` | `queue_records` |
| **eCherga** | `back.echerha.gov.ua` JSON | UA→PL virtual | wait **seconds** + booked count | `echerha_scraper.py`, `echerha_db.py` | `echerha_records` |
| **DPSU** | `dpsu.gov.ua/uk/map` HTML | UA→PL physical | trucks/cars **queued** (count) + cars/hr | `dpsu_scraper.py`, `dpsu_db.py` | `dpsu_records` |

Cadence differs per logger: **granica** runs every 3 h (`scrape.yml`, cron
`0 */3`, plus an external cron trigger for reliable spacing — see INC-002);
**eCherga** and **DPSU** run every 30 min (`echerha.yml` / `dpsu.yml`, cron
`*/30`). Each logger validates before insert and opens a GitHub issue on failure
(see INC-003 for the case where that issue stream went untriaged). The analysis layer
(`analysis/join_divergence.py`) joins the feeds read-only; see
`analysis/METHODOLOGY.md`, `INCIDENTS.md`, and the `RECON_*.md` notes for detail.

The **DPSU** logger (Series C) is the UA→PL *physical* truck-queue feed: trucks
queued in Ukraine waiting to exit into Poland. The source has no API — figures are
scraped from `data-*` attributes on `<option>` elements in the map page — and
refreshes only every ~3 h (staggered per crossing), so it's a coarse physical
baseline, deduped on the source's own `source_updated_utc`. See
`RECON_dpsu_map.md`.

## Running locally

```bash
pip install -r requirements.txt
python scraper.py            # granica (PL→UA physical)
python echerha_scraper.py    # eCherga (UA→PL virtual)
python dpsu_scraper.py       # DPSU    (UA→PL physical)
python dpsu_latest.py        # show the latest DPSU snapshot per crossing
python -m tests.verify_dpsu_mapping   # offline checks
python -m tests.verify_dpsu_join      # offline forward-fill checks
```

## Data sources & attribution

This project republishes public border-congestion data. Attribution per source:

- **DPSU map** — Border-crossing data © Державна прикордонна служба України
  (State Border Guard Service of Ukraine), https://dpsu.gov.ua/uk/map — licensed
  under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- **granica.gov.pl** — Polish Border Guard / Ministry of the Interior and
  Administration public border wait-time service. Basis: public no-login SOAP
  surface; `robots.txt` reviewed **2026-07-08** (`User-agent: *` / `Disallow:`
  empty → no path restriction); no clause prohibiting automated read identified.
  No explicit reuse licence is published for the SOAP data, so it is treated as
  public-sector information and attributed here as good practice.
- **eCherga** — Ukrainian electronic-queue service (`echerha.gov.ua`), public
  workload (Завантаженість) surface. Basis (per `RECON_echerha.md`, recon
  **2026-06-15**): public no-login workload surface only (no account/booking/
  per-driver data); `robots.txt` was **unreachable during recon** (the site
  intermittently reset the connection), so no directive could be confirmed —
  cadence is held conservative (30 min) and the User-Agent is descriptive to stay
  well within polite-use norms.

Public, no-login surfaces only; `robots.txt` respected where reachable;
descriptive User-Agent.
