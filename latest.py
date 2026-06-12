"""Print the most recent queue snapshot per crossing, plus record count.

Usage: python latest.py
"""
import sqlite3

conn = sqlite3.connect("data/queues.db")
conn.row_factory = sqlite3.Row

total, runs = conn.execute(
    "SELECT COUNT(*), COUNT(DISTINCT scraped_at) FROM queue_records"
).fetchone()
first, last = conn.execute(
    "SELECT MIN(scraped_at), MAX(scraped_at) FROM queue_records"
).fetchone()

print(f"{total} records from {runs} scrape runs ({first} -> {last})")
print()
print(f"{'crossing':<14} {'trucks out':>10} {'cars out':>9} {'buses out':>9}   last update")
print("-" * 60)

rows = conn.execute(
    """
    SELECT crossing_name, trucks_exit_min, cars_exit_min, buses_exit_min, scraped_at
    FROM queue_records
    WHERE scraped_at = (SELECT MAX(scraped_at) FROM queue_records)
    ORDER BY trucks_exit_min IS NULL, trucks_exit_min DESC
    """
).fetchall()


def fmt(minutes):
    if minutes is None:
        return "-"
    return f"{minutes // 60}:{minutes % 60:02d}"


for r in rows:
    print(
        f"{r['crossing_name']:<14} {fmt(r['trucks_exit_min']):>10} "
        f"{fmt(r['cars_exit_min']):>9} {fmt(r['buses_exit_min']):>9}   {r['scraped_at']}"
    )
