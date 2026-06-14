"""
PL-UA Border Queue Logger
Polls granica.gov.pl SOAP API and stores results in data/queues.db.
Run directly (python scraper.py) or via GitHub Actions.
"""
import datetime
import logging
import sys
import time

from zeep import Client
from zeep.exceptions import Fault, TransportError

from db import export_daily_csv, init_db, insert_records
from validate import validate_records

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

WSDL = "https://granica.gov.pl/Services/czasyService/granica.wsdl"
DB_PATH = "data/queues.db"

# granica.gov.pl is a government site prone to transient 503s. Retry the WSDL
# load a few times with exponential backoff before giving up and firing an issue.
WSDL_MAX_ATTEMPTS = 4
WSDL_BACKOFF_SECONDS = 15

# ASCII crossing IDs expected by the SOAP service (no Polish diacritics).
# Polish originals: Dorohusk, Zosin, Dołhobyczów, Hrebenne, Budomierz,
#                   Korczowa, Medyka, Małhowice, Krościenko
PL_UA_CROSSINGS: dict[str, str] = {
    "dorohusk":    "Dorohusk",
    "zosin":       "Zosin",
    "dolhobyczow": "Dołhobyczów",
    "hrebenne":    "Hrebenne",
    "budomierz":   "Budomierz",
    "korczowa":    "Korczowa",
    "medyka":      "Medyka",
    "malhowice":   "Małhowice",
    "kroscienko":  "Krościenko",
}


def _parse_wait(value) -> int | None:
    """Convert 'HH:MM' or numeric string → minutes, or None if unavailable."""
    if value is None:
        return None
    s = str(value).strip()
    if s in ("-", ""):
        return None
    try:
        if ":" in s:
            h, m = s.split(":", 1)
            return int(h) * 60 + int(m)
        return round(float(s) * 60)
    except (ValueError, AttributeError):
        return None


def scrape_all(client: Client, now: datetime.datetime) -> list[dict]:
    records: list[dict] = []

    for crossing_id, display_name in PL_UA_CROSSINGS.items():
        try:
            result = client.service.getCzasyWszystko(
                dane_in_wszystko={
                    "jednostka": crossing_id,
                    "rok":       str(now.year),
                    "miesiac":   str(now.month),
                    "dzien":     str(now.day),
                    "godzina":   str(now.hour),
                }
            )
        except (Fault, TransportError) as exc:
            log.error("SOAP error for %s: %s", crossing_id, exc)
            continue
        except Exception as exc:
            log.error("Unexpected error for %s: %s", crossing_id, exc)
            continue

        if result is None:
            log.warning("No data returned for %s at hour %d", crossing_id, now.hour)
            continue

        def g(attr):
            return _parse_wait(getattr(result, attr, None))

        records.append(
            {
                "scraped_at":       now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "crossing_id":      crossing_id,
                "crossing_name":    display_name,
                "trucks_exit_min":  g("czas_ciezarowe_wyjazd"),
                "trucks_entry_min": g("czas_ciezarowe_wjazd"),
                "cars_exit_min":    g("czas_osobowe_wyjazd"),
                "cars_entry_min":   g("czas_osobowe_wjazd"),
                "buses_exit_min":   g("czas_autokary_wyjazd"),
                "buses_entry_min":  g("czas_autokary_wjazd"),
                "data_timestamp":   str(getattr(result, "data", None)),
                "source_hour":      getattr(result, "godzina", None),
            }
        )
        log.info(
            "%-15s  trucks out=%s  trucks in=%s",
            crossing_id,
            records[-1]["trucks_exit_min"],
            records[-1]["trucks_entry_min"],
        )

    return records


def load_client() -> Client:
    """Load the SOAP client, retrying transient failures with backoff."""
    for attempt in range(1, WSDL_MAX_ATTEMPTS + 1):
        try:
            return Client(WSDL)
        except Exception as exc:
            if attempt == WSDL_MAX_ATTEMPTS:
                log.error(
                    "Failed to load WSDL after %d attempts: %s",
                    WSDL_MAX_ATTEMPTS,
                    exc,
                )
                raise
            wait = WSDL_BACKOFF_SECONDS * attempt
            log.warning(
                "WSDL load attempt %d/%d failed (%s) — retrying in %ds",
                attempt,
                WSDL_MAX_ATTEMPTS,
                exc,
                wait,
            )
            time.sleep(wait)


def main() -> None:
    now_utc = datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0, tzinfo=None)
    log.info("=== Scrape run starting %s UTC ===", now_utc.isoformat())

    try:
        client = load_client()
    except Exception:
        sys.exit(1)

    init_db(DB_PATH)
    records = scrape_all(client, now_utc)

    if not validate_records(records):
        log.error("Validation failed — aborting insert")
        sys.exit(1)

    insert_records(DB_PATH, records)
    log.info("Inserted %d records", len(records))

    csv_path = export_daily_csv(DB_PATH)
    if csv_path:
        log.info("Daily CSV updated: %s", csv_path)

    log.info("=== Run complete ===")


if __name__ == "__main__":
    main()
