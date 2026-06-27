"""
DPSU border-map Logger (Series C — UA->PL physical)
Scrapes the State Border Guard (ДПСУ) interactive map and stores the PHYSICAL
truck-queue figures for the PL-UA road crossings in data/dpsu.db.

This is the UA->PL physical partner to echerha_scraper.py (UA->PL virtual) and
scraper.py (granica, PL->UA physical). It logs trucks physically queued IN
Ukraine waiting to exit into Poland.

Run directly (python dpsu_scraper.py) or via GitHub Actions.

HTTP NOTE: there is NO API. dpsu.gov.ua/uk/map is a server-rendered page; every
crossing's live figures ship inline as data-* attributes on <option> elements
under <select id="by_name">. We fetch the page and read those attributes. There is
no TLS fingerprinting (unlike eCherga), so plain `requests` works from a normal IP.
BUT the site is behind Cloudflare and 403s the big-cloud ASNs that GitHub-hosted
runners use (Azure/AWS/GCP) — it does NOT block datacenters broadly (a small/regional
hosting IP passes fine). So in CI we route the fetch through DPSU_PROXY_URL (a tiny
proxy on a clean-ASN VPS); when that env var is unset (e.g. local runs from a normal
IP) we go direct. Single-retry/backoff + issue-on-failure pattern throughout.
See RECON_dpsu_map.md.

Scope: public, no-login /uk/map surface only. robots.txt allows all. Data is
CC BY 4.0 (see README attribution).
"""
import datetime
import logging
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from crossings import CANONICAL_NAMES
from dpsu_db import export_daily_csv, init_db, insert_records
from dpsu_validate import validate_records

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("dpsu_scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

MAP_URL = "https://dpsu.gov.ua/uk/map"
DB_PATH = "data/dpsu.db"
KYIV = ZoneInfo("Europe/Kyiv")

# Descriptive UA identifying the project (not a spoofed browser string).
USER_AGENT = (
    "border-queue-logger/0.1 "
    "(+https://github.com/jabbathepole/border-queue-logger; "
    "contact d.jablonski97@gmail.com)"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "uk"}

# Transient-failure handling, mirroring the other two loggers.
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 15
REQUEST_TIMEOUT = 30

# DPSU map `value` (crossing name string) -> canonical crossing id. Explicit on
# purpose: fuzzy matching across Ukrainian spellings could silently mis-assign a
# crossing, which is worse than failing loudly (FIX 5).
DPSU_NAME_TO_CANONICAL: dict[str, str] = {
    "Ягодин - Дорогуськ":        "dorohusk",
    "Устилуг - Зосін":           "zosin",        # note: Зосін (eCherga uses Зосин)
    "Угринів - Долгобичув":      "dolhobyczow",
    "Рава-Руська автомобільний": "hrebenne",     # special case: NO PL name in string
    "Грушів - Будомєж":          "budomierz",
    "Краківець - Корчова":       "korczowa",
    "Шегині - Медика":           "medyka",
    "Смільниця - Кросьценко":    "kroscienko",
    "Нижанковичі-Мальховіце":    "malhowice",    # note: Мальховіце (eCherga uses Мальховичі)
}
# Real PL road points that are intentionally NOT logged.
DPSU_NAMES_TO_DROP: set[str] = {"Лудин (пункт контролю)"}  # internal checkpoint, stale since 2024

# FIX 1.2 — explicit `data-state` allowlists (lower-cased for comparison). The
# old logic was "anything != відкритий ⇒ closed", which miscodes a *limited*
# state (e.g. обмежений, a trucks-only suspension) as a full closure and injects
# a closure that never happened into the event layer. Now each state is bucketed
# explicitly; an UNRECOGNISED state hard-fails (UnknownStateError) so a new state
# surfaces rather than silently becoming a false closure.
#
# Observed in data as of 2026-06-18: only `відкритий` (open). The closed/limited
# strings below are the documented DPSU vocabulary; any string outside all three
# sets is treated as unknown and raises.
OPEN_STATES: set[str] = {"відкритий"}
CLOSED_STATES: set[str] = {"зачинений", "закритий", "тимчасово зачинений"}
LIMITED_STATES: set[str] = {"обмежений", "обмежений рух", "частково відкритий"}


class UnknownCrossingError(RuntimeError):
    """A poland+car option whose name is neither mapped nor in the drop-set.
    Hard-fails the run so CI opens an issue rather than silently dropping data."""


class UnknownStateError(RuntimeError):
    """A `data-state` string in none of OPEN/CLOSED/LIMITED_STATES. Hard-fails the
    run (same philosophy as UnknownCrossingError) so a new/renamed state surfaces
    in CI rather than silently being miscoded as a closure (FIX 1.2)."""


def parse_state_of_busy(s: str | None) -> dict:
    """Parse the cars/rate/trucks blob. Returns None for any sub-field that is a
    sentence (e.g. 'suspended') rather than a number — never raises."""
    s = s or ""

    def num(pat):
        m = re.search(pat, s)
        return int(m.group(1)) if m else None

    return {
        "cars_waiting":   num(r"легкових авто перед ППр:\s*(\d+)"),
        "cars_per_hour":  num(r"оформлення легкових авто:\s*(\d+)"),
        "trucks_waiting": num(r"вантажних авто перед ППр:\s*(\d+)"),
    }


def kyiv_to_utc(naive_str: str | None) -> str | None:
    """'YYYY-MM-DD HH:MM:SS' (naive Kyiv local) -> 'YYYY-MM-DDTHH:MM:SSZ' UTC."""
    if not naive_str:
        return None
    try:
        dt = datetime.datetime.strptime(naive_str.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    dt = dt.replace(tzinfo=KYIV)
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _proxies() -> dict | None:
    """Route the fetch through DPSU_PROXY_URL if set (a clean-ASN VPS proxy, needed
    to get past Cloudflare's big-cloud-ASN block from GitHub runners). Unset =>
    direct connection (fine from a normal/residential IP for local runs)."""
    url = os.getenv("DPSU_PROXY_URL")
    return {"http": url, "https": url} if url else None


def fetch_map_html() -> str:
    """GET /uk/map, retrying transient failures with backoff."""
    proxies = _proxies()
    if proxies:
        log.info("Fetching via DPSU_PROXY_URL proxy")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(
                MAP_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT, proxies=proxies
            )
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            if attempt == MAX_ATTEMPTS:
                log.error("Failed to fetch %s after %d attempts: %s", MAP_URL, MAX_ATTEMPTS, exc)
                raise
            wait = BACKOFF_SECONDS * attempt
            log.warning(
                "Fetch attempt %d/%d failed (%s) — retrying in %ds",
                attempt, MAX_ATTEMPTS, exc, wait,
            )
            time.sleep(wait)


def scrape_all(html: str, now: datetime.datetime) -> list[dict]:
    scraped_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    soup = BeautifulSoup(html, "html.parser")
    options = soup.select('select#by_name option[data-country="poland"][data-type="car"]')
    log.info("Found %d Poland road options", len(options))

    records: list[dict] = []
    for opt in options:
        name = (opt.get("value") or opt.get_text(strip=True) or "").strip()

        if name in DPSU_NAMES_TO_DROP:
            log.info("Dropping non-logged point: %s", name)
            continue

        crossing_id = DPSU_NAME_TO_CANONICAL.get(name)
        if crossing_id is None:
            # FIX 5: do not guess, do not silently skip — hard-fail.
            raise UnknownCrossingError(
                f"Unknown DPSU crossing name {name!r}: not in DPSU_NAME_TO_CANONICAL "
                f"or DPSU_NAMES_TO_DROP. A renamed/new crossing — add it to "
                f"dpsu_scraper.py and record an INCIDENTS.md entry before resuming."
            )

        sob_raw = opt.get("data-state_of_busy")
        parsed = parse_state_of_busy(sob_raw)
        trucks = parsed["trucks_waiting"]

        state = (opt.get("data-state") or "").strip()
        state_key = state.lower()
        if state_key in OPEN_STATES:
            is_open, is_closed, is_limited = True, False, False
        elif state_key in CLOSED_STATES:
            is_open, is_closed, is_limited = False, True, False
        elif state_key in LIMITED_STATES:
            is_open, is_closed, is_limited = False, False, True
        else:
            # FIX 1.2: do not assume "not open ⇒ closed" — surface the new state.
            raise UnknownStateError(
                f"Unrecognised DPSU data-state {state!r} at crossing {crossing_id!r}: "
                f"not in OPEN_STATES/CLOSED_STATES/LIMITED_STATES. A new/renamed "
                f"state — classify it in dpsu_scraper.py and record an INCIDENTS.md "
                f"entry before resuming (do NOT let it become a false closure)."
            )
        # FIX 6 + 1.2: closure_flag only for a *full* closure with no truck digits;
        # a LIMITED state is neither a closure nor a parse-miss (restricted_flag).
        closure_flag = 1 if (is_closed and trucks is None) else 0
        parse_miss_flag = 1 if (is_open and trucks is None) else 0
        restricted_flag = 1 if is_limited else 0

        created_kyiv = (opt.get("data-created_at") or "").strip() or None
        updated_utc = kyiv_to_utc(created_kyiv)
        # FIX 1.1: a missing source timestamp must not masquerade as fresh. Fall
        # back to our poll time so the truck count is still stored once, but mark
        # it synthetic so the analysis excludes it from baselines / forward-fill.
        ts_synthetic = 0
        if updated_utc is None:
            updated_utc = scraped_at
            ts_synthetic = 1

        reading_age = int(
            (now - datetime.datetime.strptime(updated_utc, "%Y-%m-%dT%H:%M:%SZ")
             .replace(tzinfo=datetime.timezone.utc)).total_seconds()
        )

        camera = (opt.get("data-video_out") or opt.get("data-camera") or "").strip() or None

        records.append(
            {
                "scraped_at":          scraped_at,
                "crossing_id":         crossing_id,
                "crossing_name":       CANONICAL_NAMES[crossing_id],
                "dpsu_name":           name,
                "trucks_waiting":      trucks,
                "cars_waiting":        parsed["cars_waiting"],
                "cars_per_hour":       parsed["cars_per_hour"],
                "load_color":          (opt.get("data-color") or "").strip() or None,
                "state":               state or None,
                "closure_flag":        closure_flag,
                "parse_miss_flag":     parse_miss_flag,
                "restricted_flag":     restricted_flag,
                "character":           (opt.get("data-character") or "").strip() or None,
                "category":            (opt.get("data-category") or "").strip() or None,
                "location":            (opt.get("data-location") or "").strip() or None,
                "lat":                 _to_float(opt.get("data-latitute")),
                "lng":                 _to_float(opt.get("data-longitute")),
                "camera_url":          camera,
                "source_updated_kyiv": created_kyiv,
                "source_updated_utc":  updated_utc,
                "reading_age_seconds": reading_age,
                "ts_synthetic":        ts_synthetic,
                "state_of_busy_raw":   sob_raw,
            }
        )
        status = (
            " CLOSED" if closure_flag
            else " RESTRICTED" if restricted_flag
            else " PARSE-MISS" if parse_miss_flag
            else ""
        )
        log.info(
            "%-12s trucks=%s cars=%s rate=%s color=%s age=%dmin%s%s",
            crossing_id, trucks, parsed["cars_waiting"], parsed["cars_per_hour"],
            records[-1]["load_color"], reading_age // 60,
            status, " [synthetic-ts]" if ts_synthetic else "",
        )

    return records


def main() -> None:
    now_utc = datetime.datetime.now(datetime.timezone.utc).replace(
        second=0, microsecond=0
    )
    log.info("=== DPSU scrape run starting %s ===", now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"))

    try:
        html = fetch_map_html()
        records = scrape_all(html, now_utc)
    except UnknownCrossingError as exc:
        log.error("Scrape aborted — %s", exc)
        sys.exit(1)
    except Exception:
        log.exception("Scrape aborted — map unreachable or structure changed")
        sys.exit(1)

    if not validate_records(records):
        log.error("Validation failed — aborting insert")
        sys.exit(1)

    init_db(DB_PATH)
    added = insert_records(DB_PATH, records)
    log.info("Inserted %d new records (of %d scraped; rest deduped)", added, len(records))

    csv_path = export_daily_csv(DB_PATH)
    if csv_path:
        log.info("Daily CSV updated: %s", csv_path)

    log.info("=== Run complete ===")


if __name__ == "__main__":
    main()
