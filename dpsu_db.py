import csv
import datetime
import os
import sqlite3

# DPSU PHYSICAL UA->PL congestion (trucks queued IN Ukraine waiting to exit into
# Poland). Deliberately a separate table/db from queue_records (granica PL->UA
# physical, minutes) and echerha_records (UA->PL virtual, seconds + booked
# count). The headline metric here is trucks_waiting (a vehicle COUNT), so it is
# never merged into a "wait" column.
#
# Schema notes tied to the build brief's six fixes:
#   FIX 1 — source_updated_utc + reading_age_seconds carry the reading's age.
#   FIX 3 — UNIQUE(crossing_id, source_updated_utc): dedupe on the source's own
#           update time, never our poll time. Over-sampling is harmless.
#   FIX 4 — NO load_band column. load_color is kept ONLY as raw passenger-car
#           metadata (it reflects car load, not freight); freight banding is done
#           downstream by percentile normalisation on raw trucks_waiting.
#   FIX 6 — closure_flag / parse_miss_flag give a NULL truck count meaning.
#
# PR-2 fixes:
#   1.1 — ts_synthetic: 1 when source_updated_utc was *synthesised* from our poll
#         time (data-created_at absent). Such a row's timing is unreliable, so the
#         analysis must exclude it from baselines and forward-fill.
#   1.2 — restricted_flag: 1 for a LIMITED/partial state (neither a full closure
#         nor a parse miss). Keeps a partial state from being miscoded as closed.
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS dpsu_records (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at           TEXT    NOT NULL,   -- our poll time, UTC, ...Z
    crossing_id          TEXT    NOT NULL,   -- canonical (crossings.py)
    crossing_name        TEXT    NOT NULL,   -- canonical display name
    dpsu_name            TEXT    NOT NULL,   -- raw `value` string
    trucks_waiting       INTEGER,            -- HEADLINE freight count; NULL allowed
    cars_waiting         INTEGER,            -- NULL when suspended / sentence
    cars_per_hour        INTEGER,            -- cars only; no freight rate exists
    load_color           TEXT,               -- raw car-load colour ONLY (metadata)
    state                TEXT,               -- open / closed / limited (raw UA string)
    closure_flag         INTEGER DEFAULT 0,  -- 1 if closed + NULL trucks
    parse_miss_flag      INTEGER DEFAULT 0,  -- 1 if open + NULL trucks
    restricted_flag      INTEGER DEFAULT 0,  -- 1 if a LIMITED/partial state (FIX 1.2)
    character            TEXT,
    category             TEXT,
    location             TEXT,
    lat                  REAL,
    lng                  REAL,
    camera_url           TEXT,               -- mostly NULL today; keep the column
    source_updated_kyiv  TEXT,               -- raw data-created_at (naive, Kyiv local)
    source_updated_utc   TEXT    NOT NULL,   -- DST-converted, UTC, ...Z
    reading_age_seconds  INTEGER,            -- scraped_at - source_updated_utc
    ts_synthetic         INTEGER DEFAULT 0,  -- 1 if source_updated_utc==poll time (FIX 1.1)
    state_of_busy_raw    TEXT,               -- keep the blob for re-parsing later
    UNIQUE(crossing_id, source_updated_utc)
)
"""

_COLUMNS = [
    "scraped_at", "crossing_id", "crossing_name", "dpsu_name",
    "trucks_waiting", "cars_waiting", "cars_per_hour", "load_color",
    "state", "closure_flag", "parse_miss_flag", "restricted_flag",
    "character", "category", "location", "lat", "lng", "camera_url",
    "source_updated_kyiv", "source_updated_utc", "reading_age_seconds",
    "ts_synthetic", "state_of_busy_raw",
]

# Columns added after the initial schema shipped; ALTER-in for pre-existing dbs
# (cheap — the dataset is young). name -> column definition.
_MIGRATIONS = {
    "restricted_flag": "INTEGER DEFAULT 0",
    "ts_synthetic": "INTEGER DEFAULT 0",
}


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(CREATE_TABLE)
        existing = {r[1] for r in conn.execute("PRAGMA table_info(dpsu_records)")}
        for col, decl in _MIGRATIONS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE dpsu_records ADD COLUMN {col} {decl}")


def insert_records(db_path: str, records: list) -> int:
    """INSERT OR IGNORE so re-polls of an unchanged source are dropped by the
    UNIQUE(crossing_id, source_updated_utc) constraint. Returns rows added."""
    cols = ", ".join(_COLUMNS)
    placeholders = ", ".join(f":{c}" for c in _COLUMNS)
    with sqlite3.connect(db_path) as conn:
        before = conn.total_changes
        conn.executemany(
            f"INSERT OR IGNORE INTO dpsu_records ({cols}) VALUES ({placeholders})",
            records,
        )
        return conn.total_changes - before


def export_daily_csv(db_path: str, output_dir: str = "data") -> str | None:
    today = datetime.date.today().isoformat()
    csv_path = os.path.join(output_dir, f"dpsu_{today}.csv")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM dpsu_records WHERE scraped_at LIKE ? "
            "ORDER BY scraped_at, crossing_id",
            (f"{today}%",),
        ).fetchall()

    if not rows:
        return None

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(dict(r) for r in rows)

    return csv_path
