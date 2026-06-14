import logging

from crossings import CANONICAL_NAMES

log = logging.getLogger(__name__)

# The three priority PL-UA crossings from the brief; the rest are nice-to-have.
PRIORITY_CROSSINGS = {"dorohusk", "korczowa", "medyka"}
EXPECTED_CROSSINGS = set(CANONICAL_NAMES)

# eCherga virtual-queue waits run far longer than physical waits (multi-day
# truck queues are normal here). 14 days is an extreme-but-plausible ceiling —
# flag, don't reject.
MAX_WAIT_SECONDS = 14 * 24 * 3600


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
        wait = rec.get("virtual_wait_s")
        if wait is not None and (wait < 0 or wait > MAX_WAIT_SECONDS):
            log.warning(
                "ANOMALY %s [%s] virtual_wait_s=%s (%.1f h) — may be real news, not a bug",
                rec["crossing_id"],
                rec.get("echerha_title"),
                wait,
                wait / 3600,
            )
        count = rec.get("vehicles_waiting")
        if count is not None and count < 0:
            log.warning(
                "ANOMALY %s [%s] vehicles_waiting=%s (negative)",
                rec["crossing_id"],
                rec.get("echerha_title"),
                count,
            )

    return True
