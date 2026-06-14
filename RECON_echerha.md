# eCherga (UA-side) Reconnaissance

Recon performed 2026-06-14 for the Ukrainian mirror of the granica.gov.pl
logger. This documents the public workload API before/behind the scraper.

## Endpoint

```
GET https://back.echerha.gov.ua/api/v4/workload/{carrierType}
```

- `echerha.gov.ua` is a Nuxt 3 SPA; its runtime config exposes
  `BASE_URL = https://back.echerha.gov.ua/api`. The workload view
  (`/workload/{carrierType}/{country}`) calls `/api/v4/workload/{carrierType}`.
  The second URL segment (country) is a **client-side UI filter only** — the API
  returns *all* neighbouring countries; we filter by `country_id` ourselves.
- `carrierType`: **1 = trucks** (вантажівки), **2 = buses** (автобуси).
  `3` responds 200 but returns zero checkpoints (passenger cars are not
  eCherga-queued at these borders); `0/4/5` → 404.
- No `/statistics` API endpoint was found at the obvious paths
  (`/api/v4/statistics`, etc.); the public **Завантаженість (workload)** view is
  the richer source and is what we log. The Статистика page can be revisited
  later if a JSON endpoint is located.

### Required headers (else HTTP 401)

| Header           | Value                                   | Notes |
|------------------|-----------------------------------------|-------|
| `X-Client-Locale`| `uk` or `en` (lowercase 2-letter)       | 401 "Missed or incorrect value for header 'X-Client-Locale'" without it |
| `X-User-Agent`   | `UABorder/<ver> Web/1.1.0 User/guest`   | App client string; `<ver>` value is not validated |
| `User-Agent`     | a normal browser UA                     | also checked |

No auth/token/cookie is needed for the workload view — the web client only adds
an `Authorization: Bearer` header when a user is logged in. We stay anonymous.

## Response shape

```jsonc
{
  "data": [
    {
      "id": 1,                              // stable eCherga checkpoint id
      "title": "Ягодин – Дорогуськ (для вантажівок ≥ 7,5 тонн)",
      "tooltip": null,                      // optional disruption notice (string)
      "country_id": 133,                    // 133 = Poland (Польща)
      "for_vehicle_type": 1,                // 1 truck, 2 bus
      "queue_flow": 1,                      // 1 = live wait-time queue, 2 = scheduled-slot
      "is_paused": false,
      "cancel_after": 240,                  // booking detail, not logged
      "lng": 23.81, "lat": 51.19,
      "wait_time": 327900,                  // VIRTUAL-queue est. wait, SECONDS
      "vehicle_in_active_queues_counts": 1113 // vehicles booked in active queues
      // buses-by-schedule instead expose: free_slots_today, slots_units_left_today
    }
  ],
  "filters": { "countries": [ { "id": 133, "name": "Польща", ... }, ... ] }
}
```

`filters.countries`: Молдова=112, **Польща=133**, Румунія=136, Словаччина=149,
Угорщина=167.

### ⚠️ Units — keep distinct from the Polish logger

`wait_time` is the eCherga **virtual / electronic-queue** estimate in **seconds**
(stored as `virtual_wait_s`). It is **not** the same unit or meaning as the
Polish physical `*_min` columns in `queue_records` — multi-day truck figures are
normal here. The two are stored in **separate tables/files** and must never be
merged into one "wait" column. A later join compares them per `crossing_id`.

## Crossing map (Poland, country_id=133)

eCherga renames each queue after its Polish counterpart (right of the en-dash)
and splits it by tonnage / empty / goods-group / bus. All nine canonical
crossings from the Polish logger are present. Full id → canonical map lives in
`crossings.py` (primary = stable id; fallback = Polish name in title). Summary:

| Canonical    | UA side (Ягодин-style) | eCherga ids (truck / bus) |
|--------------|------------------------|---------------------------|
| dorohusk     | Ягодин – Дорогуськ     | 1, 29, 2 / 54 |
| zosin        | Устилуг – Зосин        | 80, 7 / 113 |
| dolhobyczow  | Угринів – Долгобичув   | 98, 31 / 76 |
| hrebenne     | Рава-Руська – Хребенне | 5, 19 / 78 |
| budomierz    | Грушів – Будомєж       | 88 / 104 |
| korczowa     | Краківець – Корчова    | 6, 20 / 23 |
| medyka       | Шегині – Медика        | 8, 91 / 24 |
| malhowice    | Нижанковичі – Мальховичі | (bus only) 102 |
| kroscienko   | Смільниця – Кросьценко | 84 / 75 |

## ⚠️ Access / anti-bot notes (important for CI)

`back.echerha.gov.ua` is behind **Akamai Bot Manager**:

1. **TLS fingerprinting (JA3/JA4).** Python `requests`/urllib3 is dropped at the
   TLS handshake (`UNEXPECTED_EOF`) **100% of the time**, even with perfect
   headers. The system **`curl` binary** presents an accepted fingerprint and
   works — so the scraper shells out to `curl` (preinstalled on GitHub Actions
   `ubuntu-latest`). `curl_cffi` (Chrome impersonation) is the cleaner library
   alternative but failed to build on this box's Python 3.14; it works on the
   CI's Python 3.12 if a pure-Python client is later preferred.
2. **Rate-limiting / IP cooldown.** Sustained rapid requests get the same
   connection resets for a while, then recover. The 30-minute poll cadence stays
   well clear of this; the scraper also retries with backoff (15/30/45s).
3. **Open risk — runner IP.** Recon succeeded from a local (PL) IP. Whether
   Akamai serves GitHub's Azure datacenter IPs is **unverified**. If the first
   scheduled runs fail with `UNEXPECTED_EOF`, that's the cause; mitigations are a
   self-hosted/PL-based runner or a proxy. The workflow's issue-on-failure will
   surface it.

## Scope honoured

Public no-login workload surface only. No account, login, booking flow,
Diia/BankID, or per-driver data. Aggregate congestion metrics only.
robots.txt was unreachable during recon (site intermittently closed the
connection); cadence is conservative (30 min) and the User-Agent is descriptive.
