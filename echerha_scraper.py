"""
UA-side eCherga Mirror Logger
Polls the public echerha.gov.ua workload JSON API and stores the Ukrainian
electronic-queue (VIRTUAL queue) metrics for the PL-UA crossings in
data/echerha.db. Mirror of scraper.py (the granica.gov.pl physical-wait logger).

Run directly (python echerha_scraper.py) or via GitHub Actions.

Scope: public, no-login "Завантаженість" (workload) surface only. No accounts,
no auth, no booking flow, no per-driver data. Aggregate congestion only.

HTTP NOTE: back.echerha.gov.ua sits behind Akamai Bot Manager, which fingerprints
the TLS ClientHello (JA3/JA4) and drops Python's urllib3/`requests` handshake
outright, while accepting a real browser's. The system `curl` binary presents a
browser-acceptable fingerprint and is preinstalled on GitHub Actions runners, so
we shell out to it rather than use requests here. The required app headers
(X-Client-Locale, X-User-Agent) were captured during reconnaissance; without them
the API returns 401.
"""
import datetime
import json
import logging
import subprocess
import sys
import time

from crossings import (
    CANONICAL_NAMES,
    POLAND_COUNTRY_ID,
    classify_vehicle,
    map_canonical,
)
from echerha_db import export_daily_csv, init_db, insert_records
from echerha_validate import validate_records

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("echerha_scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

API_BASE = "https://back.echerha.gov.ua/api/v4/workload"
DB_PATH = "data/echerha.db"

# carrierType: 1=trucks (вантажівки), 2=buses (автобуси). 3 exists but returns
# no checkpoints (passenger cars are not eCherga-queued at these borders).
CARRIER_TYPES = (1, 2)

# Headers the eCherga web client sends; the API 401s without the first two.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
API_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json",
    "Accept-Language": "uk,en;q=0.8",
    "X-Client-Locale": "uk",
    "X-User-Agent": "UABorder/3.0.0 Web/1.1.0 User/guest",
    "Origin": "https://echerha.gov.ua",
    "Referer": "https://echerha.gov.ua/",
}

# Akamai occasionally rate-limits/closes connections; retry with backoff before
# giving up (and letting the workflow open an issue), same as the PL logger.
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 15
REQUEST_TIMEOUT = 30


def _curl_json(url: str) -> dict:
    """GET a URL with curl (browser-acceptable TLS fingerprint) -> parsed JSON."""
    cmd = ["curl", "-sS", "--fail", "--max-time", str(REQUEST_TIMEOUT)]
    for key, value in API_HEADERS.items():
        cmd += ["-H", f"{key}: {value}"]
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=REQUEST_TIMEOUT + 10)
    if proc.returncode != 0:
        raise RuntimeError(
            f"curl exit {proc.returncode}: {proc.stderr.strip() or 'no stderr'}"
        )
    return json.loads(proc.stdout)


def fetch_workload(carrier_type: int) -> dict:
    """Fetch one carrierType's workload, retrying transient failures."""
    url = f"{API_BASE}/{carrier_type}"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _curl_json(url)
        except Exception as exc:
            if attempt == MAX_ATTEMPTS:
                log.error(
                    "Failed to fetch carrierType=%d after %d attempts: %s",
                    carrier_type,
                    MAX_ATTEMPTS,
                    exc,
                )
                raise
            wait = BACKOFF_SECONDS * attempt
            log.warning(
                "Fetch carrierType=%d attempt %d/%d failed (%s) — retrying in %ds",
                carrier_type,
                attempt,
                MAX_ATTEMPTS,
                exc,
                wait,
            )
            time.sleep(wait)


def scrape_all(now: datetime.datetime) -> list[dict]:
    records: list[dict] = []
    scraped_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    for carrier_type in CARRIER_TYPES:
        payload = fetch_workload(carrier_type)
        checkpoints = payload.get("data") or []
        log.info("carrierType=%d returned %d checkpoints", carrier_type, len(checkpoints))

        for cp in checkpoints:
            if cp.get("country_id") != POLAND_COUNTRY_ID:
                continue  # Poland border only

            echerha_id = cp.get("id")
            title = cp.get("title") or ""
            crossing_id = map_canonical(echerha_id, title)
            if crossing_id is None:
                log.warning(
                    "Unmapped Poland checkpoint id=%s title=%r — add it to crossings.py",
                    echerha_id,
                    title,
                )
                continue

            records.append(
                {
                    "scraped_at":       scraped_at,
                    "crossing_id":      crossing_id,
                    "crossing_name":    CANONICAL_NAMES[crossing_id],
                    "echerha_id":       echerha_id,
                    "echerha_title":    title,
                    "vehicle_class":    classify_vehicle(title, cp.get("for_vehicle_type")),
                    "vehicle_type":     cp.get("for_vehicle_type"),
                    "queue_flow":       cp.get("queue_flow"),
                    "is_paused":        int(bool(cp.get("is_paused"))),
                    "virtual_wait_s":   cp.get("wait_time"),
                    "vehicles_waiting": cp.get("vehicle_in_active_queues_counts"),
                    "free_slots":       cp.get("free_slots_today"),
                    "slots_units_left": cp.get("slots_units_left_today"),
                    "tooltip":          cp.get("tooltip"),
                    "country_id":       cp.get("country_id"),
                    "carrier_type":     carrier_type,
                }
            )

    return records


def main() -> None:
    now_utc = datetime.datetime.now(datetime.timezone.utc).replace(
        second=0, microsecond=0, tzinfo=None
    )
    log.info("=== eCherga scrape run starting %s UTC ===", now_utc.isoformat())

    try:
        records = scrape_all(now_utc)
    except Exception:
        log.error("Scrape aborted — API unreachable or structure changed")
        sys.exit(1)

    if not validate_records(records):
        log.error("Validation failed — aborting insert")
        sys.exit(1)

    init_db(DB_PATH)
    insert_records(DB_PATH, records)
    log.info("Inserted %d records", len(records))

    csv_path = export_daily_csv(DB_PATH)
    if csv_path:
        log.info("Daily CSV updated: %s", csv_path)

    log.info("=== Run complete ===")


if __name__ == "__main__":
    main()
