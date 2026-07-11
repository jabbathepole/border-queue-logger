# Recon — DPSU border-map (Series C: UA→PL physical)

> **Handoff note for a fresh session.** This is a self-contained reconnaissance
> report. You do not need prior context to act on it. Read §0 first, then jump to
> the section you need. Status: **RECON ONLY — no logger built.** The deliverable
> is a build/don't-build decision (§10). Recon date: **2026-06-17**.

---

## ⚙️ Operational update (2026-06-27) — BUILT & LIVE (Option A)

> The recon's build decision landed on **Option A (DPSU-direct)** and the logger is
> now **operational on master**. Two things the recon (run from a residential IP)
> did **not** surface, discovered once it ran in CI:
>
> 1. **Cloudflare blocks the GitHub runner's IP.** `dpsu.gov.ua` sits behind
>    Cloudflare, which 403s the big-cloud ASNs GitHub-hosted runners egress from
>    (Azure confirmed; AWS/GCP same class). Headers/UA/TLS don't help — it's an
>    **ASN/IP block**, not a fingerprint block. It does **not** block datacenters
>    broadly (a small/regional hosting IP returns 200), so §9's "plain
>    curl/requests work" holds only from a normal/residential IP. **Residential
>    proxies were a dead end** (IPRoyal gates `.gov` behind identity + $500 spend;
>    DataImpulse blocks government/banking).
>    **Fix:** `dpsu_scraper.py` routes the fetch through `DPSU_PROXY_URL` (repo
>    secret) — a tiny auth'd `tinyproxy` on a clean-ASN Ukrainian VPS, host-filtered
>    to `dpsu.gov.ua` only. Unset ⇒ direct (unchanged local behaviour). PR #92.
> 2. **GitHub's `schedule:` is unreliable** (same as the other two loggers). The
>    `*/30` cron is kept as a harmless backstop; the real trigger is a
>    **cron-job.org** dispatcher hitting the `workflow_dispatch` API
>    (`.../actions/workflows/dpsu.yml/dispatches`, body `{"ref":"master"}`, every
>    30 min, same `logger-cron` PAT — repo Actions:RW already covers `dpsu.yml`).
>    See `RECON_echerha.md` §"Fix" for the identical pattern + the literal-`Bearer`
>    header gotcha.
>
> Net: the §10 cadence caveat stands (~3 h source refresh, dedup handles
> over-sampling), but the logger is collecting reliably.

---

## 0. Context you need first (cold-start)

- **Repo:** `border-queue-logger` (github.com/jabbathepole/border-queue-logger).
  It already runs two loggers that share canonical crossing ids in
  `crossings.py` and write separate SQLite tables:
  - **granica** (`scraper.py`, `db.py`) — Poland's `granica.gov.pl` SOAP API.
    PL→UA **physical** wait, in **minutes**. Table `queue_records`.
  - **eCherga** (`echerha_scraper.py`, `echerha_db.py`) — UA `echerha.gov.ua`
    JSON API. UA→PL **virtual** queue, in **seconds** + booked vehicle count.
    Table `echerha_records`.
  - An analysis layer (`analysis/join_divergence.py`) joins them per
    `(crossing_id, time-bucket)`.
- **What "Series C" is:** a *third* logger for **UA→PL physical** congestion —
  trucks physically queued **in Ukraine** waiting to exit into Poland. It is the
  missing partner that lets us compare same-direction physical-vs-virtual
  (with eCherga) and physical-vs-physical (with granica).
- **This report's target:** the State Border Guard (ДПСУ) interactive map at
  `https://dpsu.gov.ua/uk/map` as the Series-C source.
- **Related prior recon:** `RECON_ua_pl_physical.md` surveyed the same gap and
  recommended **nakordoni.eu** (a JSON aggregator, also DPSU-sourced, 1–15 min
  cadence, needs a free API key). This report is the deeper look at DPSU-direct.

### Quick facts (machine-readable)

```yaml
source: DPSU interactive border map
url: https://dpsu.gov.ua/uk/map
access_method: HTML scrape          # NOT an API — data is inline in the page HTML
http: GET, no params, no body, no auth, no CSRF needed
anti_bot: none from residential IP; BUT Cloudflare 403s big-cloud ASNs (Azure/GitHub) — CI needs DPSU_PROXY_URL via clean-ASN VPS (see Operational update)
robots_txt: allows all (Disallow: empty)
license: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)
direction: UA->PL (exit from Ukraine)   # inferred from data, no explicit field
identifier: crossing NAME string only (no numeric id/slug)
all_9_canonical_crossings_present: true
headline_metric: freight truck count queued (data-state_of_busy)
timestamp_field: data-created_at        # Kyiv local time, naive (no TZ)
timestamp_timezone: Europe/Kyiv (UTC+3 summer / +2 winter) -> needs DST-aware UTC conversion
refresh_cadence: ~2.5-3 h, batched, staggered, can skip a crossing
cadence_verdict: too coarse to match eCherga's 30-min cadence; low-freq layer
recon_date: 2026-06-17
build_status: NOT built (recon only)
```

---

## 1. How to get the data (exact mechanics)

There is **no API / XHR / JSON feed**. `js/map.js` reads everything from the DOM
of the server-rendered page. The marker-click detail panel is filled from
`data-*` attributes already in the HTML — there is **no second request**.

```
GET https://dpsu.gov.ua/uk/map
Accept-Language: uk
User-Agent: <descriptive, e.g. border-queue-logger-recon/0.1 (+repo url; contact)>
```

**Extraction recipe:**
1. Fetch the page (~610 KB).
2. Select `select#by_name option[data-country="poland"][data-type="car"]`.
   → 10 road crossings (9 wanted + 1 to drop, see §3).
3. Read `data-*` attributes per §2; parse `data-state_of_busy` per §4.

No `<meta name="csrf-token">` is needed (that's for POST forms). `/ua/map`
301-redirects to `/uk/map`; always request `/uk/map`.

---

## 2. Field reference (per `<option>`)

| Attribute | Meaning | Type | Use it for |
|---|---|---|---|
| `value` (= option text) | crossing name, e.g. `Ягодин - Дорогуськ` | string | **the only identifier** (§3) |
| `data-state_of_busy` | HTML blob: cars + car-rate + trucks | string → parse | **freight count** (§4) |
| `data-created_at` | last update, **Kyiv-local naive** `YYYY-MM-DD HH:MM:SS` | string | timestamp; **dedupe key** |
| `data-color` | `green`/`blue`/`red`/`grey` | enum | load band — ⚠ **car load, not freight** (§4) |
| `data-state` | `відкритий` / closed | string | open/closed flag |
| `data-character` | traffic character, free text | string | metadata |
| `data-category` | e.g. `міжнародний, цілодобовий` | string | metadata |
| `data-location` | UA oblast/raion | string | metadata |
| `data-latitute` / `data-longitute` | coords (sic: "latitute") | float | metadata |
| `data-video_out`, `data-camera` | camera iframe URL | string | empty for all 10 PL crossings now |
| `data-type` | `car` (road) / `train` / etc. | enum | **filter: keep `car`** |
| `data-type_text` | `Автомобільний` etc. | string | metadata |
| `data-country` | `poland` | string | **filter: keep `poland`** |

**Raw sample** (Ягодин–Дорогуськ road option, captured 2026-06-17 ~20:08Z):

```html
<option data-country="poland"
        data-created_at="2026-06-17 18:42:36"
        data-category="міжнародний, цілодобовий"
        data-color="green"
        data-character="вантажний, лише вантажні понад 7.5 т та автобуси (пропуску пішоходів не здійснюється)"
        data-location="Волинська область, Любомильський район"
        data-camera=""
        data-longitute="23.812087"
        data-latitute="51.188238"
        data-type_text="Автомобільний"
        data-type="car"
        data-state_of_busy="Кількість легкових авто перед ППр: Пропуск легкових автомобілів тимчасово не здійснюється <br> Швидкість оформлення легкових авто: 58 авто/год <br> Кількість вантажних авто перед ППр: 2251"
        data-video_out=""
        data-state="відкритий"
        value="Ягодин - Дорогуськ">Ягодин - Дорогуськ</option>
```

---

## 3. Identifier & crossing mapping

**No numeric id or slug exists.** Key on the `value` name string (plain hyphen
`-` with spaces; eCherga uses en-dash `–`). All 10 Poland road options:

| DPSU `value`                | → canonical    | Mapping note |
|-----------------------------|----------------|--------------|
| `Ягодин - Дорогуськ`        | `dorohusk`     | PL name (right of hyphen) |
| `Устилуг - Зосін`           | `zosin`        | spelling **Зосін** (eCherga: Зосин) |
| `Угринів - Долгобичув`      | `dolhobyczow`  | PL name |
| `Рава-Руська автомобільний` | `hrebenne`     | ⚠ **no PL name in string** — needs special case |
| `Грушів - Будомєж`          | `budomierz`    | PL name |
| `Краківець - Корчова`       | `korczowa`     | PL name |
| `Шегині - Медика`           | `medyka`       | PL name |
| `Смільниця - Кросьценко`    | `kroscienko`   | PL name |
| `Нижанковичі-Мальховіце`    | `malhowice`    | no-space hyphen; **Мальховіце** (eCherga: Мальховичі) |
| `Лудин (пункт контролю)`    | *(drop)*       | ⚠ internal checkpoint, stale since 2024 |

All **9 canonical crossings present as road points** (broader than eCherga,
where `malhowice` is bus-only). `crossings.py POLISH_NAME_TO_CANONICAL` covers
8/9 but **misses `Рава-Руська автомобільний`** → add a UA-name special case.

---

## 4. Parsing `data-state_of_busy` (the headline data)

It is a single HTML string, three `<br>`-separated parts, **cars before trucks**:

```
Кількість легкових авто перед ППр: <N | sentence> <br>
Швидкість оформлення легкових авто: <N> авто/год <br>
Кількість вантажних авто перед ППр: <N>
```

| Part | Field | Notes |
|---|---|---|
| `легкових авто перед ППр` | `cars_waiting` (int, **nullable**) | may be a sentence, e.g. `Пропуск легкових автомобілів тимчасово не здійснюється` (suspended) → store `None` |
| `Швидкість оформлення легкових авто … авто/год` | `cars_per_hour` (int) | **cars only — no freight rate exists** |
| `вантажних авто перед ППр` | **`trucks_waiting` (int)** | ← the metric we want |

Suggested regex (parser must yield `None`, not crash, on non-numeric car count):

```python
import re
def parse_state_of_busy(s: str) -> dict:
    def num(pat):
        m = re.search(pat, s)
        return int(m.group(1)) if m else None
    return {
        "cars_waiting":  num(r"легкових авто перед ППр:\s*(\d+)"),   # None if suspended/sentence
        "cars_per_hour": num(r"оформлення легкових авто:\s*(\d+)"),
        "trucks_waiting": num(r"вантажних авто перед ППр:\s*(\d+)"),
    }
```

**Load band (`data-color`):** legend = `hi_zavantag`/`middle_zavantag`/
`low_zavantag`; icon suffixes `_h/_m/_l`. So **red=Висока(high), blue=Середня
(med), green=Низька(low), grey=no-data.** ⚠ **It reflects passenger-car load,
NOT freight** — Ягодин is `green` (Low) with 2251 trucks queued + cars
suspended. **Do not use `data-color` as the freight band; derive it from
`trucks_waiting`.**

---

## 5. The three target crossings (verified present)

Poll 2026-06-17 ~20:08Z; `created_at` is Kyiv-local:

| Crossing (`value`)    | created_at (Kyiv)   | color | cars | cars/hr | **trucks** | state |
|-----------------------|---------------------|-------|------|---------|------------|-------|
| `Ягодин - Дорогуськ`  | 2026-06-17 18:42:36 | green | *susp.* | 58   | **2251**   | open  |
| `Краківець - Корчова` | 2026-06-17 21:06:02 | blue  | 45   | 73      | **414**    | open  |
| `Шегині - Медика`     | 2026-06-17 21:06:13 | blue  | 40   | 68      | **966**    | open  |

---

## 6. Refresh cadence (Step-3 probe)

Live probe: 2 polls 22 min apart → identical (consistent w/ multi-hour feed).
The embedded `created_at` stamps give the real interval (now = 20:30Z = 23:30
Kyiv):

```
7 crossings updated in a batch @ ~21:06 Kyiv  (≈2.4 h before poll)
Ягодин & Устилуг still @ 18:42 Kyiv           (skipped the 21:06 batch → ~4.8 h stale)
Лудин @ 2024-09-24                            (dead, ~21 months)
batch-to-batch interval: 18:42 → 21:06 = ~2 h 24 m  (≈ stated "every 3 h")
```

**Verdict:** coarse ~2.5–3 h, **batched, staggered, occasionally skips a
crossing.** Cannot serve as a sub-hourly partner to eCherga (30 min). A logger
would over-sample; **dedupe inserts on `created_at`** and store native
resolution. (A multi-hour confirmatory probe would pin the exact period; the
two timestamp generations already indicate ~3 h.)

> **Addendum — overnight dead zone (2026-07-08, supersedes the "~2.5–3 h,
> occasionally skips" characterisation above).** With 11 days of clean-window
> data (`>= 2026-06-27`) the cadence is now measured directly by
> [`analysis/dpsu_coverage.py`](analysis/dpsu_coverage.py), and it is **not** a
> uniform ~3 h feed. **Native readings occur only in UTC hours 03–21; there are
> ZERO readings 22:00–02:59** — a recurrent overnight dead zone. The daytime
> inter-reading gap has a **~3.1 h median** (matching the original probe), but the
> evening→morning gap runs **~9 h** (≈18:0x → 03:0x) and occasionally ~18 h on a
> full-day skip. Effective rate ≈ **4–5 native readings/day** — about half what a
> flat "~3 h refresh" implies. Consequences: Series C is **daytime-conditioned**,
> the 6 h C-vs-B freshness cutoff structurally drops most night buckets (~58 % of
> stale exclusions are in 22:00–05:59 UTC), and **no diurnal claim may rest on C**.
> This is documented **normal source behaviour** — it is characterised, **not**
> alerted on. See `analysis/METHODOLOGY.md` §"Diurnal coverage".

---

## 7. Direction — UA→PL (confirmed from data)

No direction attribute. Inferred: metric is *"авто **перед ППр**"* = vehicles
queued **on the Ukrainian approach**; locations are UA oblasts; DPSU Ягодин 2251
trucks aligns with eCherga's `dorohusk` UA→PL backlog. = **Series C: UA→PL
physical.** ✅

---

## 8. robots.txt & licence

- `GET /robots.txt` → 200; `User-agent: *` / `Disallow:` **(empty → all
  allowed)**; no crawl-delay. Scraping `/uk/map` is permitted. ✅
- Footer: *"Весь контент доступний за ліцензією Creative Commons Attribution 4.0
  International license, якщо не зазначено інше."*
  Link: `https://creativecommons.org/licenses/by/4.0/deed.uk`.
- **Attribution string to display:**
  > Border-crossing data © Державна прикордонна служба України (State Border
  > Guard Service of Ukraine), dpsu.gov.ua/uk/map — licensed under
  > [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

This is our cleanest provenance (first-party, raw DPSU).

## 9. Bot-block behaviour

Not reproduced today. `/uk/map`, robots, `map.js`, both polls = 200, no
challenge. `/ua/map` → 301 to `/uk/map` (locale canonicalisation, not a block).
**No TLS fingerprinting** (unlike eCherga's Akamai) — plain `curl`/`requests`
work; no need to shell out. Keep an eCherga-style single-retry/backoff +
issue-on-failure anyway; intermittent `/ua/` blocks were noted historically.

> **⚠ Correction (2026-06-27, from CI):** this section was probed from a residential
> IP. In production the site **does** block — Cloudflare 403s GitHub's runner ASN
> (Azure), though *not* datacenters broadly (a UK hosting IP got 200). It's an
> IP/ASN block, not TLS/headers. The logger now egresses via `DPSU_PROXY_URL` (a
> clean-ASN VPS proxy). See the **Operational update** callout at the top.

---

## 10. Won't-map-cleanly checklist + build decision

**Schema friction to handle before building (each maps to a concrete TODO):**
1. **No stable id** → map on name. Add UA-name special case for
   `Рава-Руська автомобільний`=hrebenne; tolerant matching for spelling drift
   (Зосін/Зосин, Мальховіце/Мальховичі).
2. **Drop `Лудин (пункт контролю)`** (not one of 9; frozen since 2024).
3. **`state_of_busy` = unstructured blob** → regex (§4); car count may be a
   sentence → `None`, never crash.
4. **Units = vehicle counts** (trucks/cars) + cars/hr — a *third* unit family
   (granica=min, eCherga=s+count). Closest to eCherga `vehicles_waiting`. Put in
   its **own table** `dpsu_records`; never merge into a "wait" column.
5. **`created_at` = Kyiv-local naive** → DST-aware →UTC; store raw + UTC; dedupe
   on `created_at`.
6. **`data-color` ≠ freight band** → derive freight load from `trucks_waiting`.
7. **No freight processing rate** → a `trucks_per_hour` column would be all NULL;
   omit it.
8. **Cameras empty now** → keep `camera_url` column, expect mostly NULL.

**Suggested `dpsu_records` schema (mirrors `echerha_db.py` discipline):**

```sql
CREATE TABLE dpsu_records (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at           TEXT NOT NULL,   -- our poll time, UTC ...Z
    crossing_id          TEXT NOT NULL,   -- canonical (crossings.py)
    crossing_name        TEXT NOT NULL,
    dpsu_name            TEXT NOT NULL,   -- the raw `value` string
    trucks_waiting       INTEGER,         -- HEADLINE freight count
    cars_waiting         INTEGER,         -- nullable (suspended -> NULL)
    cars_per_hour        INTEGER,
    load_color           TEXT,            -- green/blue/red/grey (car load)
    load_band            TEXT,            -- High/Medium/Low (from color)
    state                TEXT,            -- open/closed
    character            TEXT,
    category             TEXT,
    location             TEXT,
    lat                  REAL,
    lng                  REAL,
    camera_url           TEXT,
    source_updated_kyiv  TEXT,            -- raw data-created_at
    source_updated_utc   TEXT,            -- DST-converted
    state_of_busy_raw    TEXT             -- keep the blob for re-parsing
)
```

**Decision — is a scheduled logger here reliable?** *Technically yes:* robots
allows it, no bot fight, first-party CC-BY data, all 9 crossings with explicit
freight counts in the wanted UA→PL direction. *But cadence is coarse (~3 h,
staggered, skips).* So DPSU = an authoritative **low-frequency physical
truck-count baseline**, not a sub-hourly eCherga partner.

**Three options to choose between (decide before any code):**
- **A. DPSU-direct only** — cleanest provenance, simplest scrape; ~3 h cadence.
- **B. nakordoni.eu only** — 1–15 min, both directions, JSON, also DPSU-sourced;
  needs a free API key + attribution (see `RECON_ua_pl_physical.md`).
- **C. Both** — DPSU as the authoritative ~3 h baseline + nakordoni for cadence.

Recommendation: **C** if cadence matters for the eCherga join; **A** if first-
party provenance + simplicity is the priority and ~3 h is acceptable.

---

### Recon scope honoured
Public no-login `/uk/map` only; descriptive UA; ~7 polite requests; robots.txt
read & respected; no accounts / `cabinet.dpsu.gov.ua`. **No logger written.**
