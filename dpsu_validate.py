import logging

from crossings import CANONICAL_NAMES

log = logging.getLogger(__name__)

# The three priority PL-UA crossings from the project brief; the rest are
# nice-to-have. DPSU exposes all nine as road crossings.
PRIORITY_CROSSINGS = {"dorohusk", "korczowa", "medyka"}
EXPECTED_CROSSINGS = set(CANONICAL_NAMES)

# A single physical truck queue above this is extreme-but-plausible here
# (multi-thousand-truck backlogs occur). Flag, do not reject.
MAX_TRUCKS_WAITING = 20000


def validate_records(records: list) -> bool:
    if not records:
        log.error("VALIDATION FAIL: zero records returned")
        return False

    found = {r["crossing_id"] for r in records}

    missing_priority = PRIORITY_CROSSINGS - found
    if missing_priority:
        log.error(
            "VALIDATION FAIL: priority crossings absent: %s",
            sorted(missing_priority),
        )
        return False

    missing = EXPECTED_CROSSINGS - found
    if missing:
        log.warning("Non-priority crossings absent from this scrape: %s", sorted(missing))

    for rec in records:
        trucks = rec.get("trucks_waiting")
        if trucks is not None and (trucks < 0 or trucks > MAX_TRUCKS_WAITING):
            log.warning(
                "ANOMALY %s [%s] trucks_waiting=%s — may be real news, not a bug",
                rec["crossing_id"],
                rec.get("dpsu_name"),
                trucks,
            )
        cars = rec.get("cars_waiting")
        if cars is not None and cars < 0:
            log.warning(
                "ANOMALY %s [%s] cars_waiting=%s (negative)",
                rec["crossing_id"],
                rec.get("dpsu_name"),
                cars,
            )
        # An open crossing that still failed to yield a truck count is worth a
        # log line every run (parse_miss_flag is also persisted for querying).
        if rec.get("parse_miss_flag"):
            log.warning(
                "PARSE MISS %s [%s] open but trucks_waiting unparsed: %r",
                rec["crossing_id"],
                rec.get("dpsu_name"),
                rec.get("state_of_busy_raw"),
            )

    return True
