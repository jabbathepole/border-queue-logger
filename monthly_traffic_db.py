"""
Schema + UPSERT for the Border-Guard monthly traffic BASELINE (Series A
enrichment, dane.gov.pl dataset 2708).

Deliberately a SEPARATE table/db from queue_records (granica PL->UA physical
wait, MINUTES, timestamp-level) and the other loggers. The metric here is a
monthly vehicle COUNT per crossing/direction — a coarse, restated baseline, not
a live reading — so it is never merged into a "wait" column or row-joined to the
minute-level series. See METHODOLOGY.md.

Restatement model (the reason this is UPSERT, not INSERT OR IGNORE):
dataset 2708 publishes recent months as zero, then BACKFILLS them in a later
vintage of the same per-crossing file. So re-pulling the current year and
upserting must let a backfilled month OVERWRITE the earlier zero. The conflict
key is (crossing_id, month, vehicle_type, direction, registration); last write
wins, and source_resource_id records which vintage the current value came from.
The archived raw XLSX files (data/raw_traffic/) preserve every vintage so any
prior restatement stays recomputable.
"""
import csv
import os
import sqlite3

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS monthly_traffic (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    crossing_id          TEXT NOT NULL,              -- canonical (crossings.py)
    crossing_label       TEXT NOT NULL,              -- raw 2708 row label
    month                TEXT NOT NULL,              -- 'YYYY-MM'
    vehicle_type         TEXT NOT NULL,              -- 'truck' | 'total' | 'car' | 'bus'
    direction            TEXT NOT NULL,              -- 'z_RP' (PL->UA east) | 'do_RP' (UA->PL west)
    registration         TEXT NOT NULL DEFAULT 'all',-- 'all' | 'foreign' | 'polish'
    count                INTEGER,                    -- NULL when joint-reported absent
    joint_reported_with  TEXT,                       -- e.g. 'medyka' for malhowice rows
    source_resource_id   TEXT NOT NULL,              -- dane.gov.pl resource this came from
    source_dataset       TEXT NOT NULL DEFAULT '2708',
    fetched_at           TEXT NOT NULL,              -- our pull time, UTC ...Z
    UNIQUE(crossing_id, month, vehicle_type, direction, registration)
)
"""

_COLUMNS = [
    "crossing_id", "crossing_label", "month", "vehicle_type", "direction",
    "registration", "count", "joint_reported_with", "source_resource_id",
    "source_dataset", "fetched_at",
]


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(CREATE_TABLE)


def upsert_records(db_path: str, records: list) -> dict:
    """UPSERT on the natural key. A re-pull of a backfilled month overwrites the
    earlier (often zero) value; source_resource_id/fetched_at track the vintage.

    Returns {'inserted': n, 'updated': n}. total_changes counts an
    ON CONFLICT...DO UPDATE as 1 change just like an insert, so we distinguish
    the two by reading rows_before/after.
    """
    cols = ", ".join(_COLUMNS)
    placeholders = ", ".join(f":{c}" for c in _COLUMNS)
    sql = f"""
        INSERT INTO monthly_traffic ({cols}) VALUES ({placeholders})
        ON CONFLICT(crossing_id, month, vehicle_type, direction, registration)
        DO UPDATE SET
            count               = excluded.count,
            crossing_label      = excluded.crossing_label,
            joint_reported_with = excluded.joint_reported_with,
            source_resource_id  = excluded.source_resource_id,
            source_dataset      = excluded.source_dataset,
            fetched_at          = excluded.fetched_at
    """
    with sqlite3.connect(db_path) as conn:
        rows_before = conn.execute("SELECT COUNT(*) FROM monthly_traffic").fetchone()[0]
        conn.executemany(sql, records)
        rows_after = conn.execute("SELECT COUNT(*) FROM monthly_traffic").fetchone()[0]
    inserted = rows_after - rows_before
    return {"inserted": inserted, "updated": len(records) - inserted}


def export_csv(db_path: str, output_dir: str = "data") -> str | None:
    """Full-table snapshot to data/monthly_traffic.csv for git-visible provenance
    (the table is small — a few hundred rows). The archived XLSX hold history;
    this is the current-truth flat view."""
    csv_path = os.path.join(output_dir, "monthly_traffic.csv")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM monthly_traffic "
            "ORDER BY month, crossing_id, vehicle_type, direction, registration"
        ).fetchall()
    if not rows:
        return None
    os.makedirs(output_dir, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(dict(r) for r in rows)
    return csv_path
