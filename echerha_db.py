import csv
import datetime
import os
import sqlite3

# eCherga VIRTUAL-queue metrics. These are deliberately NOT the same unit as the
# Polish physical wait times in queue_records: virtual_wait_s is the electronic
# queue's estimated wait (seconds), vehicles_waiting is the booked-queue length.
# Kept in a separate table/db so the two are never silently merged.
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS echerha_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at       TEXT NOT NULL,
    crossing_id      TEXT NOT NULL,
    crossing_name    TEXT NOT NULL,
    echerha_id       INTEGER NOT NULL,
    echerha_title    TEXT NOT NULL,
    vehicle_class    TEXT,
    vehicle_type     INTEGER,
    queue_flow       INTEGER,
    is_paused        INTEGER,
    virtual_wait_s   INTEGER,
    vehicles_waiting INTEGER,
    free_slots       INTEGER,
    slots_units_left INTEGER,
    tooltip          TEXT,
    country_id       INTEGER,
    carrier_type     INTEGER
)
"""


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(CREATE_TABLE)


def insert_records(db_path: str, records: list) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO echerha_records (
                scraped_at, crossing_id, crossing_name,
                echerha_id, echerha_title, vehicle_class, vehicle_type,
                queue_flow, is_paused,
                virtual_wait_s, vehicles_waiting,
                free_slots, slots_units_left,
                tooltip, country_id, carrier_type
            ) VALUES (
                :scraped_at, :crossing_id, :crossing_name,
                :echerha_id, :echerha_title, :vehicle_class, :vehicle_type,
                :queue_flow, :is_paused,
                :virtual_wait_s, :vehicles_waiting,
                :free_slots, :slots_units_left,
                :tooltip, :country_id, :carrier_type
            )
            """,
            records,
        )


def export_daily_csv(db_path: str, output_dir: str = "data") -> str | None:
    today = datetime.date.today().isoformat()
    csv_path = os.path.join(output_dir, f"echerha_{today}.csv")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM echerha_records WHERE scraped_at LIKE ? "
            "ORDER BY scraped_at, crossing_id, echerha_id",
            (f"{today}%",),
        ).fetchall()

    if not rows:
        return None

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(dict(r) for r in rows)

    return csv_path
