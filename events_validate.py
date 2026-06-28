"""Hard-failing validator for the hand-curated corridor event log.

Matches the repo convention (cf. `dpsu_validate.py`): structurally bad input
fails loudly rather than being silently coerced or stored. The event log's
failure mode is curation error, so the validator enforces the pre-registered
schema mechanically — unknown crossing codes, inverted dates, missing sources,
out-of-vocab enums all abort.

Usage:
    python events_validate.py [path]      # defaults to data/corridor_events.csv

Exit code 0 = valid, 1 = invalid (so CI / pre-commit can gate on it).
"""
from __future__ import annotations

import csv
import logging
import sys
from pathlib import Path

from events_log import (
    CANONICAL_CODES,
    CONFIDENCES,
    DATE_PRECISIONS,
    DEFAULT_PATH,
    DIRECTIONS,
    EVENT_TYPES,
    FIELDS,
    SCOPE_SENTINELS,
    SEVERITIES,
    SOURCE_TYPES,
    parse_event,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("events_validate")


def _fail(row_no: int | None, msg: str) -> None:
    where = f"row {row_no}: " if row_no is not None else ""
    log.error("VALIDATION FAIL: %s%s", where, msg)


def _validate_scope(scope: str) -> str | None:
    """Return an error string for a bad crossings_affected, else None."""
    tokens = [t.strip() for t in scope.split(";") if t.strip()]
    if not tokens:
        return "crossings_affected is empty"
    has_sentinel = any(t in SCOPE_SENTINELS for t in tokens)
    if has_sentinel and len(tokens) > 1:
        # A sentinel is system-wide; mixing it with specific codes is contradictory.
        return f"sentinel mixed with other tokens: {tokens!r}"
    if not has_sentinel:
        bad = [t for t in tokens if t not in CANONICAL_CODES]
        if bad:
            return f"unknown crossing code(s): {bad!r}"
    return None


def validate_file(path: str | Path = DEFAULT_PATH) -> bool:
    path = Path(path)
    if not path.exists():
        _fail(None, f"file not found: {path}")
        return False

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        if header != FIELDS:
            _fail(None, f"header mismatch.\n  expected: {FIELDS}\n  found:    {header}")
            return False

        ok = True
        seen_ids: set[str] = set()
        n = 0
        # DictReader line numbers: header is line 1, first data row is line 2.
        for row_no, row in enumerate(reader, start=2):
            n += 1

            # Parsing enforces dates, end_date/ongoing, and the bool field.
            try:
                ev = parse_event(row)
            except (ValueError, KeyError) as exc:
                _fail(row_no, f"unparseable: {exc}")
                ok = False
                continue

            if not ev.event_id:
                _fail(row_no, "empty event_id")
                ok = False
            elif ev.event_id in seen_ids:
                _fail(row_no, f"duplicate event_id: {ev.event_id!r}")
                ok = False
            else:
                seen_ids.add(ev.event_id)

            if not ev.is_ongoing and ev.end_date < ev.start_date:
                _fail(row_no, f"end_date {ev.end_date} < start_date {ev.start_date}")
                ok = False

            if ev.date_precision not in DATE_PRECISIONS:
                _fail(row_no, f"bad date_precision: {ev.date_precision!r}")
                ok = False
            if ev.event_type not in EVENT_TYPES:
                _fail(row_no, f"bad event_type: {ev.event_type!r}")
                ok = False
            if ev.direction_affected not in DIRECTIONS:
                _fail(row_no, f"bad direction_affected: {ev.direction_affected!r}")
                ok = False
            if ev.severity not in SEVERITIES:
                _fail(row_no, f"bad severity: {ev.severity!r}")
                ok = False
            if ev.confidence not in CONFIDENCES:
                _fail(row_no, f"bad confidence: {ev.confidence!r}")
                ok = False
            if ev.source_type not in SOURCE_TYPES:
                _fail(row_no, f"bad source_type: {ev.source_type!r}")
                ok = False

            scope_err = _validate_scope(ev.crossings_affected)
            if scope_err:
                _fail(row_no, scope_err)
                ok = False

            if not ev.description:
                _fail(row_no, "empty description")
                ok = False
            if not ev.source_url or not ev.source_url.lower().startswith("http"):
                _fail(row_no, f"missing/invalid source_url: {ev.source_url!r}")
                ok = False

        if ok:
            log.info("OK: %s - %d event row(s) valid", path.name, n)
        return ok


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    sys.exit(0 if validate_file(target) else 1)
