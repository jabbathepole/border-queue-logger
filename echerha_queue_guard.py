"""eCherga queue-set guard.

The validators (`echerha_validate`) check crossing-level presence only, so a
whole *sub-queue* can vanish from the payload without failing anything — and B's
truck sum silently shrinks. That has real precedent on this corridor: in 2024
Poland requested abolition of the Ukrainian e-queue at specific crossings, with
pilot abolitions at Nyzhankovychi–Małhowice and Uhryniv–Dołhobyczów. This guard
is the in-band tripwire for exactly that: a policy-driven queue-set change.

What it does, after a successful fetch (see echerha_scraper.main):
  - Diffs the live payload's Poland queue-id set against the committed baseline
    `echerha_expected_queues.json`.
  - MISSING id (in baseline, not in payload): confirmed against the DB — if the
    same id was ALSO absent from the *previous* scrape (the DB is the debounce
    state; no new state files), that is two consecutive absences -> log.error +
    ALERT. A single absence -> log.warning only (could be a transient hiccup).
  - NEW id (in payload, not in baseline): a new sub-queue is a policy event too
    -> log.warning + ALERT. It is still ingested (echerha_scraper maps it via the
    map_canonical fallback); the guard only flags it.
  - `is_paused=1` is NOT "missing": the guard keys on payload PRESENCE, never on
    activity. A paused sub-queue's id is still in the payload, so it never alerts.

Hard rules:
  - The guard NEVER blocks ingestion. Valid records always insert; the alert is
    the point. It does not touch `validate_records`' pass/fail semantics.
  - The existing failure->GitHub-issue mechanism is WORKFLOW-LEVEL (it fires when
    the scraper process exits non-zero). The guard must not exit non-zero (that
    would abort the insert and lose the scrape), so it signals out-of-band: a
    distinct `QUEUE_GUARD_ALERT` marker line in the log AND a `queue_guard_alert.txt`
    drop-file. Wiring that drop-file/marker into an actual GitHub issue needs a
    workflow step, which is out of scope here (no workflow edits) — the maintainer
    adds it.

Baseline updates are deliberate, reviewed PRs only (fail-loud, human-in-loop): a
genuine eCherga sub-queue add/remove is itself a `policy` row for
`data/corridor_events.csv` (with an external `source_url` — the guard alert is the
tripwire, never the citation).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3

log = logging.getLogger(__name__)

BASELINE_PATH = "echerha_expected_queues.json"
ALERT_MARKER = "QUEUE_GUARD_ALERT"          # grep-able marker for a future workflow step
ALERT_DROPFILE = "queue_guard_alert.txt"
DB_PATH = "data/echerha.db"


def load_baseline(path: str = BASELINE_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def baseline_ids(baseline: dict) -> set[int]:
    return {q["echerha_id"] for q in baseline.get("queues", [])}


def previous_scrape_ids(db_path: str = DB_PATH) -> set[int]:
    """The echerha_ids of the most recent scrape ALREADY in the DB — the debounce
    state (no separate state file). Read-only. Empty set if the db/table is
    absent or empty (e.g. a first-ever run)."""
    if not os.path.exists(db_path):
        return set()
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return set()
    try:
        last = conn.execute("SELECT max(scraped_at) FROM echerha_records").fetchone()[0]
        if not last:
            return set()
        return {r[0] for r in conn.execute(
            "SELECT DISTINCT echerha_id FROM echerha_records WHERE scraped_at=?", (last,)
        )}
    except sqlite3.Error:
        return set()
    finally:
        conn.close()


def evaluate(present_ids: set[int], base_ids: set[int], prev_ids: set[int]) -> list[dict]:
    """PURE diff — no I/O, no side effects (this is what the offline tests drive).

    Returns one event dict per anomaly:
      {kind, echerha_id, alert: bool, log_level: 'error'|'warning', reason}
    - missing & absent-in-prev-scrape too -> 2 consecutive -> alert, error
    - missing but present in prev-scrape   -> 1st absence   -> no alert, warning
    - new (not in baseline)                -> policy event  -> alert, warning
    """
    events: list[dict] = []
    for mid in sorted(base_ids - present_ids):
        two_in_a_row = mid not in prev_ids
        events.append({
            "kind": "missing_queue",
            "echerha_id": mid,
            "alert": two_in_a_row,
            "log_level": "error" if two_in_a_row else "warning",
            "reason": ("absent for 2 consecutive scrapes" if two_in_a_row
                       else "absent this scrape (1st) — will alert if absent again"),
        })
    for nid in sorted(present_ids - base_ids):
        events.append({
            "kind": "new_queue",
            "echerha_id": nid,
            "alert": True,
            "log_level": "warning",
            "reason": "queue id not in baseline — new/renamed sub-queue (a policy event)",
        })
    return events


def _describe(echerha_id: int, present_meta: dict | None, baseline: dict) -> str:
    meta = (present_meta or {}).get(echerha_id)
    if meta:
        return f"{meta.get('crossing_id')}/{meta.get('vehicle_class')} '{meta.get('echerha_title')}'"
    for q in baseline.get("queues", []):
        if q["echerha_id"] == echerha_id:
            return f"{q.get('crossing_id')}/{q.get('vehicle_class')} '{q.get('echerha_title')}'"
    return "(unknown)"


def _write_dropfile(path: str, scraped_at: str, alerts: list[dict],
                    baseline: dict, present_meta: dict | None) -> None:
    lines = [
        f"{ALERT_MARKER} — eCherga queue-set change detected at {scraped_at}",
        "",
        "A queue-set change is the one event class that belongs in BOTH logs:",
        "  - instrument impact (B's truck sum shifts) -> INCIDENTS.md",
        "  - the underlying policy action -> data/corridor_events.csv, with an",
        "    external source_url (this alert is the tripwire, NEVER the citation).",
        "",
    ]
    for e in alerts:
        lines.append(f"  [{e['kind']}] id={e['echerha_id']} — {e['reason']}")
        lines.append(f"      {_describe(e['echerha_id'], present_meta, baseline)}")
    lines.append("")
    lines.append("If this is a real, sourced eCherga config change, update "
                 "echerha_expected_queues.json in a reviewed PR to clear the alert.")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_guard(present_ids, *, present_meta: dict | None = None,
              scraped_at: str = "", db_path: str = DB_PATH,
              baseline_path: str = BASELINE_PATH,
              dropfile: str = ALERT_DROPFILE) -> list[dict]:
    """Load baseline + DB debounce state, evaluate, then LOG each event (at its
    level, with the ALERT_MARKER on any alerting line) and write the drop-file iff
    there are alerts (otherwise clear a stale one). NEVER raises on a queue-set
    change and NEVER blocks ingestion — returns the events for the caller/tests."""
    baseline = load_baseline(baseline_path)
    base_ids = baseline_ids(baseline)
    prev_ids = previous_scrape_ids(db_path)
    events = evaluate(set(present_ids), base_ids, prev_ids)

    for e in events:
        desc = _describe(e["echerha_id"], present_meta, baseline)
        marker = f"{ALERT_MARKER} " if e["alert"] else ""
        msg = "%s%s id=%s (%s) — %s"
        args = (marker, e["kind"], e["echerha_id"], desc, e["reason"])
        if e["log_level"] == "error":
            log.error(msg, *args)
        else:
            log.warning(msg, *args)

    alerts = [e for e in events if e["alert"]]
    if alerts:
        _write_dropfile(dropfile, scraped_at or "(unknown)", alerts, baseline, present_meta)
        log.error("%s wrote %s (%d alert(s)) — a workflow step should open a "
                  "GitHub issue from it (maintainer to wire; no workflow edit here).",
                  ALERT_MARKER, dropfile, len(alerts))
    elif os.path.exists(dropfile):
        # clear a stale alert from a previous run so the drop-file is a clean
        # per-run tripwire.
        os.remove(dropfile)

    return events
