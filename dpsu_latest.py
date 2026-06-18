"""Print the most recent DPSU physical truck-queue snapshot per crossing.

Usage: python dpsu_latest.py
"""
import sqlite3

conn = sqlite3.connect("data/dpsu.db")
conn.row_factory = sqlite3.Row

total, runs = conn.execute(
    "SELECT COUNT(*), COUNT(DISTINCT scraped_at) FROM dpsu_records"
).fetchone()
first, last = conn.execute(
    "SELECT MIN(scraped_at), MAX(scraped_at) FROM dpsu_records"
).fetchone()

print(f"{total} records from {runs} scrape runs ({first} -> {last})")
print()
print(f"{'crossing':<13} {'trucks':>7} {'cars':>6} {'cars/h':>7} {'color':>6} "
      f"{'age':>6}  {'updated (UTC)':<21} state")
print("-" * 88)

rows = conn.execute(
    """
    SELECT crossing_name, trucks_waiting, cars_waiting, cars_per_hour, load_color,
           reading_age_seconds, source_updated_utc, state, closure_flag, parse_miss_flag
    FROM dpsu_records
    WHERE scraped_at = (SELECT MAX(scraped_at) FROM dpsu_records)
    ORDER BY trucks_waiting IS NULL, trucks_waiting DESC
    """
).fetchall()


def fmt(v):
    return "-" if v is None else str(v)


def age(seconds):
    if seconds is None:
        return "-"
    h, m = divmod(seconds // 60, 60)
    return f"{h}:{m:02d}"


for r in rows:
    flag = " CLOSED" if r["closure_flag"] else (" PARSE-MISS" if r["parse_miss_flag"] else "")
    print(
        f"{r['crossing_name']:<13} {fmt(r['trucks_waiting']):>7} {fmt(r['cars_waiting']):>6} "
        f"{fmt(r['cars_per_hour']):>7} {fmt(r['load_color']):>6} {age(r['reading_age_seconds']):>6}  "
        f"{fmt(r['source_updated_utc']):<21} {fmt(r['state'])}{flag}"
    )
