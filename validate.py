import logging

log = logging.getLogger(__name__)

EXPECTED_CROSSINGS = {
    "dorohusk",
    "zosin",
    "dolhobyczow",
    "hrebenne",
    "budomierz",
    "korczowa",
    "medyka",
    "malhowice",
    "kroscienko",  # Krościenko
}

# 72 hours is an extreme but plausible upper bound (flag, don't reject)
MAX_WAIT_MINUTES = 72 * 60


def validate_records(records: list) -> bool:
    if not records:
        log.error("VALIDATION FAIL: zero records returned")
        return False

    found = {r["crossing_id"] for r in records}
    missing = EXPECTED_CROSSINGS - found
    if missing:
        log.warning("Crossings absent from this scrape: %s", sorted(missing))

    for rec in records:
        for field in ("trucks_exit_min", "trucks_entry_min"):
            val = rec.get(field)
            if val is not None and (val < 0 or val > MAX_WAIT_MINUTES):
                log.warning(
                    "ANOMALY %s %s = %d min (%.1f h) — may be real news, not a bug",
                    rec["crossing_id"],
                    field,
                    val,
                    val / 60,
                )

    return True
