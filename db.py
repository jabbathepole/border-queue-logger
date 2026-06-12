import csv
import datetime
import os
import sqlite3

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS queue_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at       TEXT NOT NULL,
    crossing_id      TEXT NOT NULL,
    crossing_name    TEXT NOT NULL,
    trucks_exit_min  INTEGER,
    trucks_entry_min INTEGER,
    cars_exit_min    INTEGER,
    cars_entry_min   INTEGER,
    buses_exit_min   INTEGER,
    buses_entry_min  INTEGER,
    data_timestamp   TEXT,
    source_hour      INTEGER
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
            INSERT INTO queue_records (
                scraped_at, crossing_id, crossing_name,
                trucks_exit_min, trucks_entry_min,
                cars_exit_min,   cars_entry_min,
                buses_exit_min,  buses_entry_min,
                data_timestamp,  source_hour
            ) VALUES (
                :scraped_at, :crossing_id, :crossing_name,
                :trucks_exit_min, :trucks_entry_min,
                :cars_exit_min,   :cars_entry_min,
                :buses_exit_min,  :buses_entry_min,
                :data_timestamp,  :source_hour
            )
            """,
            records,
        )


def export_daily_csv(db_path: str, output_dir: str = "data") -> str | None:
    today = datetime.date.today().isoformat()
    csv_path = os.path.join(output_dir, f"queues_{today}.csv")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM queue_records WHERE scraped_at LIKE ? ORDER BY scraped_at, crossing_id",
            (f"{today}%",),
        ).fetchall()

    if not rows:
        return None

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(dict(r) for r in rows)

    return csv_path
