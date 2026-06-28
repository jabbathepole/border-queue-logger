"""Offline test for events_log.events_for (the join helper). In-memory; no I/O.

Verifies:
  - an explicit-code event annotates only its listed crossings,
  - the `corridor` and `unknown` sentinels BOTH annotate every crossing,
    while remaining distinguishable in the raw row (Call 3),
  - the date window is inclusive; out-of-window observations are excluded;
    an `ongoing` event is open-ended,
  - a sub-6h event is PRESENT with below_materiality_floor=true and is EXCLUDED
    by the query-time materiality filter (Call 1: tag-not-delete).
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crossings import CANONICAL_NAMES
from events_log import Event, events_for

failures: list[str] = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{(' — ' + detail) if detail and not cond else ''}")
    if not cond:
        failures.append(label)


def mk(event_id, scope, start, end, *, below_floor=False):
    return Event(
        event_id=event_id,
        start_date=dt.date.fromisoformat(start),
        end_date=None if end == "ongoing" else dt.date.fromisoformat(end),
        date_precision="day",
        event_type="blockade",
        direction_affected="UA_PL",
        crossings_affected=scope,
        severity="high",
        confidence="confirmed",
        below_materiality_floor=below_floor,
        description="synthetic",
        source_url="https://example.gov/notice",
        source_type="gov",
        date_recorded=dt.date(2026, 6, 28),
        last_verified=dt.date(2026, 6, 28),
    )


ALL = list(CANONICAL_NAMES)

specific = mk("e-specific", "korczowa;dorohusk", "2026-06-13", "2026-06-16")
corridor = mk("e-corridor", "corridor", "2026-06-14", "ongoing")
unknown = mk("e-unknown", "unknown", "2026-06-14", "2026-06-20")
shortev = mk("e-short", "medyka", "2026-06-17", "2026-06-17", below_floor=True)
events = [specific, corridor, unknown, shortev]


def ids(evs):
    return {e.event_id for e in evs}


# --- scope: explicit codes ----------------------------------------------------
d = dt.date(2026, 6, 15)
check("explicit event annotates a listed crossing (korczowa)",
      "e-specific" in ids(events_for(events, "korczowa", d)))
check("explicit event does NOT annotate an unlisted crossing (zosin)",
      "e-specific" not in ids(events_for([specific], "zosin", d)))

# --- scope: both sentinels cover every crossing -------------------------------
corridor_everywhere = all("e-corridor" in ids(events_for([corridor], c, d)) for c in ALL)
unknown_everywhere = all("e-unknown" in ids(events_for([unknown], c, d)) for c in ALL)
check("corridor sentinel annotates ALL 9 crossings", corridor_everywhere)
check("unknown sentinel annotates ALL 9 crossings", unknown_everywhere)
# ...but they remain distinguishable in the raw row.
check("corridor vs unknown stay distinguishable in the raw row",
      corridor.crossings_affected == "corridor"
      and unknown.crossings_affected == "unknown"
      and corridor.is_system_wide and unknown.is_system_wide)

# --- date window --------------------------------------------------------------
check("inclusive start boundary", "e-specific" in ids(events_for([specific], "korczowa", dt.date(2026, 6, 13))))
check("inclusive end boundary", "e-specific" in ids(events_for([specific], "korczowa", dt.date(2026, 6, 16))))
check("before window excluded", "e-specific" not in ids(events_for([specific], "korczowa", dt.date(2026, 6, 12))))
check("after window excluded", "e-specific" not in ids(events_for([specific], "korczowa", dt.date(2026, 6, 17))))
check("ongoing event open-ended (far future still matches)",
      "e-corridor" in ids(events_for([corridor], "medyka", dt.date(2027, 1, 1))))
check("unknown (bounded) excluded after its end_date",
      "e-unknown" not in ids(events_for([unknown], "medyka", dt.date(2026, 6, 21))))

# --- materiality: tag-not-delete (Call 1) -------------------------------------
on_short_day = dt.date(2026, 6, 17)
default_q = events_for(events, "medyka", on_short_day)  # include_below_floor=True
filtered_q = events_for(events, "medyka", on_short_day, include_below_floor=False)
check("sub-6h event PRESENT in default (unfiltered) query", "e-short" in ids(default_q))
check("sub-6h event EXCLUDED by materiality filter", "e-short" not in ids(filtered_q))
check("the short event is actually flagged below_materiality_floor", shortev.below_materiality_floor is True)


print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("all event-join checks passed")
