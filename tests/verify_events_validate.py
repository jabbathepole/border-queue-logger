"""Offline test for events_validate.validate_file. Synthetic CSVs; no network.

Verifies the hard-failing validator:
  - a well-formed file (incl. both system-wide sentinels) PASSES,
  - the committed data/corridor_events.csv (header-only or populated) PASSES,
  - an unknown crossing code FAILS,
  - inverted start/end dates FAIL,
  - a sentinel mixed with a specific code FAILS,
  - a missing source_url FAILS,
  - an out-of-vocab event_type FAILS.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from events_log import DEFAULT_PATH, FIELDS
from events_validate import validate_file

failures: list[str] = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{(' — ' + detail) if detail and not cond else ''}")
    if not cond:
        failures.append(label)


HEADER = ",".join(FIELDS)

# A well-formed row template (fields in FIELDS order). Override per-case.
GOOD = {
    "event_id": "evt-001",
    "start_date": "2026-06-13",
    "end_date": "2026-06-15",
    "date_precision": "day",
    "event_type": "blockade",
    "direction_affected": "UA_PL",
    "crossings_affected": "korczowa;dorohusk",
    "severity": "high",
    "confidence": "confirmed",
    "below_materiality_floor": "false",
    "description": "Synthetic test event.",
    "source_url": "https://example.gov.pl/notice",
    "source_type": "gov",
    "date_recorded": "2026-06-28",
    "last_verified": "2026-06-28",
}


def row(**over):
    r = dict(GOOD, **over)
    return ",".join(r[f] for f in FIELDS)


def write_csv(rows: list[str]) -> str:
    fh = tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    )
    fh.write(HEADER + "\n")
    for r in rows:
        fh.write(r + "\n")
    fh.close()
    return fh.name


# --- PASS cases ---------------------------------------------------------------
good_file = write_csv([
    row(event_id="evt-001"),
    row(event_id="evt-002", crossings_affected="corridor", event_type="policy",
        end_date="ongoing"),
    row(event_id="evt-003", crossings_affected="unknown", event_type="security",
        below_materiality_floor="true"),
])
check("well-formed file (codes + corridor + unknown) passes", validate_file(good_file) is True)

check("committed corridor_events.csv passes", validate_file(DEFAULT_PATH) is True)

# --- FAIL cases ---------------------------------------------------------------
check("unknown crossing code fails",
      validate_file(write_csv([row(crossings_affected="korczowa;atlantis")])) is False)

check("inverted dates fail",
      validate_file(write_csv([row(start_date="2026-06-20", end_date="2026-06-15")])) is False)

check("sentinel mixed with code fails",
      validate_file(write_csv([row(crossings_affected="corridor;medyka")])) is False)

check("missing source_url fails",
      validate_file(write_csv([row(source_url="")])) is False)

check("out-of-vocab event_type fails",
      validate_file(write_csv([row(event_type="meteor")])) is False)

check("duplicate event_id fails",
      validate_file(write_csv([row(event_id="dup"), row(event_id="dup", start_date="2026-06-14")])) is False)

check("bad bool fails",
      validate_file(write_csv([row(below_materiality_floor="maybe")])) is False)


print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("all event-validator checks passed")
