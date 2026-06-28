"""Loader and join helper for the hand-curated corridor event log.

The event log (`data/corridor_events.csv`) is the repo's only hand-curated,
non-reproducible layer: a dated, sourced record of real-world disruptions
(blockades, strikes, policy changes, closures) used to annotate anomalies across
the four collected series. See `data/corridor_events.README.md` for the schema and
inclusion rule, and `RECON_event_log.md` for the design rationale.

This module is read-only over the CSV. It parses rows into `Event` objects and
provides the join helper that drafting actually calls:

    events_for(events, crossing, ts)

— given a series observation `(crossing C, time T)`, it returns every event whose
scope covers C and whose [start_date, end_date] window contains T. That is what
turns "a spreadsheet of events" into "annotated anomalies".

Scope semantics (Call 3): a row's `crossings_affected` is either a `;`-delimited
list of canonical crossing codes, or one of two system-wide sentinels —
`corridor` (asserted system-wide) or `unknown` (scope not yet pinned down). Both
sentinels annotate ALL crossings in the join (false negatives are the dangerous
error here), but they remain distinguishable in the raw row.
"""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from crossings import CANONICAL_NAMES

DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "corridor_events.csv"

# Sentinels usable in place of a crossing list. Both annotate every crossing;
# they are kept distinct so a real corridor-wide event is told apart from an
# uninvestigated one.
SCOPE_CORRIDOR = "corridor"
SCOPE_UNKNOWN = "unknown"
SCOPE_SENTINELS = frozenset({SCOPE_CORRIDOR, SCOPE_UNKNOWN})

CANONICAL_CODES = frozenset(CANONICAL_NAMES)

EVENT_TYPES = frozenset(
    {"blockade", "strike", "policy", "closure", "infrastructure", "security", "weather"}
)
DIRECTIONS = frozenset({"PL_UA", "UA_PL", "both", "unknown"})
DATE_PRECISIONS = frozenset({"exact", "day", "week", "month"})
SEVERITIES = frozenset({"low", "med", "high"})
CONFIDENCES = frozenset({"confirmed", "reported", "unconfirmed"})
SOURCE_TYPES = frozenset({"gov", "wire", "outlet", "portal_notice"})

ONGOING = "ongoing"

# Column order is the contract; the validator asserts the header matches exactly.
FIELDS = [
    "event_id",
    "start_date",
    "end_date",
    "date_precision",
    "event_type",
    "direction_affected",
    "crossings_affected",
    "severity",
    "confidence",
    "below_materiality_floor",
    "description",
    "source_url",
    "source_type",
    "date_recorded",
    "last_verified",
]


@dataclass(frozen=True)
class Event:
    event_id: str
    start_date: dt.date
    end_date: dt.date | None  # None == ongoing (open-ended)
    date_precision: str
    event_type: str
    direction_affected: str
    crossings_affected: str  # raw scope string, preserved verbatim
    severity: str
    confidence: str
    below_materiality_floor: bool
    description: str
    source_url: str
    source_type: str
    date_recorded: dt.date
    last_verified: dt.date

    @property
    def is_ongoing(self) -> bool:
        return self.end_date is None

    @property
    def scope_tokens(self) -> list[str]:
        """The `;`-split crossings_affected tokens (a sentinel returns itself)."""
        return [t.strip() for t in self.crossings_affected.split(";") if t.strip()]

    @property
    def is_system_wide(self) -> bool:
        return any(t in SCOPE_SENTINELS for t in self.scope_tokens)

    def covers(self, crossing: str) -> bool:
        """True if this event's scope annotates `crossing`.

        A sentinel (`corridor`/`unknown`) covers every crossing; otherwise the
        crossing must appear in the explicit code list.
        """
        tokens = self.scope_tokens
        if any(t in SCOPE_SENTINELS for t in tokens):
            return True
        return crossing in tokens

    def contains_time(self, when: dt.date) -> bool:
        """True if `when` falls within [start_date, end_date] (inclusive).

        An ongoing event is open-ended: only start_date bounds it.
        """
        if when < self.start_date:
            return False
        if self.is_ongoing:
            return True
        return when <= self.end_date


def _as_date(when: dt.date | dt.datetime) -> dt.date:
    return when.date() if isinstance(when, dt.datetime) else when


def parse_bool(raw: str) -> bool:
    v = (raw or "").strip().lower()
    if v == "true":
        return True
    if v == "false":
        return False
    raise ValueError(f"not a bool: {raw!r} (expected 'true'/'false')")


def parse_event(row: dict[str, str]) -> Event:
    """Parse one CSV row into an Event. Raises ValueError on malformed fields.

    Strict by design: the validator relies on this to raise on bad input rather
    than coercing silently (the hand-curated analog of hard-failing on unknown
    inputs).
    """
    end_raw = (row["end_date"] or "").strip()
    end_date = None if end_raw == ONGOING else dt.date.fromisoformat(end_raw)
    return Event(
        event_id=row["event_id"].strip(),
        start_date=dt.date.fromisoformat(row["start_date"].strip()),
        end_date=end_date,
        date_precision=row["date_precision"].strip(),
        event_type=row["event_type"].strip(),
        direction_affected=row["direction_affected"].strip(),
        crossings_affected=row["crossings_affected"].strip(),
        severity=row["severity"].strip(),
        confidence=row["confidence"].strip(),
        below_materiality_floor=parse_bool(row["below_materiality_floor"]),
        description=row["description"].strip(),
        source_url=row["source_url"].strip(),
        source_type=row["source_type"].strip(),
        date_recorded=dt.date.fromisoformat(row["date_recorded"].strip()),
        last_verified=dt.date.fromisoformat(row["last_verified"].strip()),
    )


def load_events(path: str | Path = DEFAULT_PATH) -> list[Event]:
    """Load and parse all event rows. An empty (header-only) file returns []."""
    events: list[Event] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            events.append(parse_event(row))
    return events


def events_for(
    events: list[Event],
    crossing: str,
    when: dt.date | dt.datetime,
    *,
    include_below_floor: bool = True,
) -> list[Event]:
    """Return events annotating `(crossing, when)`.

    The join payoff: an event matches if its scope covers `crossing` (an explicit
    code OR a system-wide sentinel) AND its date window contains `when`.

    `include_below_floor=False` applies the materiality filter at query time —
    excluding events flagged `below_materiality_floor` without ever deleting them
    from the raw log.
    """
    day = _as_date(when)
    out = [e for e in events if e.covers(crossing) and e.contains_time(day)]
    if not include_below_floor:
        out = [e for e in out if not e.below_materiality_floor]
    return out
