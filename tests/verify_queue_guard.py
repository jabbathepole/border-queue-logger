"""Offline verification of the eCherga queue-set guard (echerha_queue_guard.py).

No network, no writes to data/. Drives:
  - the pure diff `evaluate()` for the four required scenarios:
      (a) a queue removed ONCE            -> warn only, no alert
      (b) a queue removed TWICE in a row  -> alert (log.error)
      (c) a NEW queue                     -> ingested + alert
      (d) a PAUSED queue                  -> present, so NO alert
  - `scrape_all()` with a stubbed payload, to prove a paused sub-queue and a
    new (fallback-mapped) sub-queue are both INGESTED and both appear in `seen`
    (ingestion is never blocked);
  - `run_guard()` end-to-end against a throwaway temp DB + temp drop-file, to
    prove the drop-file is written on alert and cleared when clean, and that the
    guard never raises.

Run: python -m tests.verify_queue_guard
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import echerha_scraper
import echerha_queue_guard as guard
from echerha_db import CREATE_TABLE

_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


def _alerts(events):
    return [e for e in events if e["alert"]]


def _by_id(events, i):
    return next((e for e in events if e["echerha_id"] == i), None)


# --- (a) removed once -> warn only -------------------------------------------
# id 5 in baseline, absent from payload now, but PRESENT in the previous scrape.
ev = guard.evaluate(present_ids={1}, base_ids={1, 5}, prev_ids={1, 5})
e5 = _by_id(ev, 5)
check("(a) removed-once flags id 5 as missing", e5 is not None and e5["kind"] == "missing_queue")
check("(a) removed-once is WARN not alert", e5 and e5["alert"] is False and e5["log_level"] == "warning")
check("(a) removed-once raises no alert overall", _alerts(ev) == [])

# --- (b) removed twice consecutively -> alert --------------------------------
# id 5 absent now AND absent from the previous scrape.
ev = guard.evaluate(present_ids={1}, base_ids={1, 5}, prev_ids={1})
e5 = _by_id(ev, 5)
check("(b) removed-twice is an ALERT", e5 and e5["alert"] is True and e5["log_level"] == "error")
check("(b) removed-twice reason mentions consecutive", e5 and "consecutive" in e5["reason"])

# --- (c) new queue -> alert (and, below, ingested) ---------------------------
ev = guard.evaluate(present_ids={1, 999}, base_ids={1}, prev_ids={1})
e999 = _by_id(ev, 999)
check("(c) new queue flagged", e999 and e999["kind"] == "new_queue")
check("(c) new queue is an ALERT (warn-level)", e999 and e999["alert"] is True and e999["log_level"] == "warning")

# --- (d) paused queue -> present, so NO alert --------------------------------
# A paused sub-queue's id is still in the payload => still in present_ids.
ev = guard.evaluate(present_ids={1, 5}, base_ids={1, 5}, prev_ids={1, 5})
check("(d) paused-but-present queue produces no events", ev == [])


# --- scrape_all: paused + new sub-queues are INGESTED and SEEN ---------------
POLAND = echerha_scraper.POLAND_COUNTRY_ID


def _cp(cid, title, vt, paused=0):
    return {
        "country_id": POLAND, "id": cid, "title": title, "for_vehicle_type": vt,
        "is_paused": paused, "wait_time": 3600, "vehicle_in_active_queues_counts": 10,
        "queue_flow": 1, "free_slots_today": 0, "slots_units_left_today": 0, "tooltip": "",
    }


def _fake_fetch(carrier_type):
    if carrier_type == 1:  # trucks
        return {"data": [
            _cp(1, "Ягодин – Дорогуськ (для вантажівок ≥ 7,5 тонн)", 1, paused=0),
            _cp(5, "Рава-Руська – Хребенне (для вантажівок ≥ 7,5 тонн)", 1, paused=1),   # PAUSED
            _cp(999, "Ягодин – Дорогуськ (нова черга)", 1, paused=0),                     # NEW id, fallback-maps
            {"country_id": 42, "id": 7000, "title": "not poland"},                        # filtered out
        ]}
    return {"data": []}  # buses


import datetime  # noqa: E402
_orig_fetch = echerha_scraper.fetch_workload
echerha_scraper.fetch_workload = _fake_fetch
try:
    recs, seen = echerha_scraper.scrape_all(datetime.datetime(2026, 7, 8, 12, 0, 0))
finally:
    echerha_scraper.fetch_workload = _orig_fetch

seen_ids = set(seen)
rec_ids = {r["echerha_id"] for r in recs}
check("scrape_all sees the paused id 5", 5 in seen_ids)
check("scrape_all INGESTS the paused id 5 (not blocked)", 5 in rec_ids)
check("scrape_all sees + ingests the new fallback-mapped id 999", 999 in seen_ids and 999 in rec_ids)
check("scrape_all maps new id 999 to dorohusk via fallback",
      any(r["echerha_id"] == 999 and r["crossing_id"] == "dorohusk" for r in recs))
check("scrape_all drops the non-Poland checkpoint", 7000 not in seen_ids)


# --- run_guard end-to-end: temp DB + temp drop-file, no data/ writes ---------
with tempfile.TemporaryDirectory() as td:
    db = os.path.join(td, "echerha.db")
    drop = os.path.join(td, "queue_guard_alert.txt")
    base = os.path.join(td, "baseline.json")
    # baseline of {1,5}; previous scrape in the DB has only {1} (so 5 is already
    # absent) -> a payload that also lacks 5 = two consecutive absences = alert.
    import json
    with open(base, "w", encoding="utf-8") as f:
        json.dump({"queues": [
            {"echerha_id": 1, "crossing_id": "dorohusk", "vehicle_class": "truck_ge_7_5t", "echerha_title": "t1"},
            {"echerha_id": 5, "crossing_id": "hrebenne", "vehicle_class": "truck_ge_7_5t", "echerha_title": "t5"},
        ]}, f)
    conn = sqlite3.connect(db)
    conn.execute(CREATE_TABLE)
    conn.execute("INSERT INTO echerha_records (scraped_at,crossing_id,crossing_name,echerha_id,echerha_title) "
                 "VALUES ('2026-07-08T11:30:00Z','dorohusk','Dorohusk',1,'t1')")
    conn.commit(); conn.close()

    ev = guard.run_guard({1}, present_meta={1: {"crossing_id": "dorohusk", "vehicle_class": "x", "echerha_title": "t1"}},
                         scraped_at="2026-07-08T12:00:00Z", db_path=db, baseline_path=base, dropfile=drop)
    check("run_guard alerts on 2-consecutive-absence of id 5", any(e["echerha_id"] == 5 and e["alert"] for e in ev))
    check("run_guard WROTE the drop-file", os.path.exists(drop))
    check("run_guard did NOT write to data/", not os.path.exists("data/queue_guard_alert.txt"))

    # now a clean payload (1 and 5 both present) clears the stale drop-file
    ev2 = guard.run_guard({1, 5}, scraped_at="2026-07-08T12:30:00Z", db_path=db, baseline_path=base, dropfile=drop)
    check("run_guard clears the drop-file when clean", not os.path.exists(drop))
    check("run_guard returns no alerts when clean", _alerts(ev2) == [])

print("\nRESULT:", "ALL CHECKS PASS" if not _fails else f"{len(_fails)} FAILURE(S): {_fails}")
sys.exit(1 if _fails else 0)
