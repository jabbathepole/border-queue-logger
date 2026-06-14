"""Print the most recent eCherga virtual-queue snapshot per crossing.

Usage: python echerha_latest.py
"""
import sqlite3

conn = sqlite3.connect("data/echerha.db")
conn.row_factory = sqlite3.Row

total, runs = conn.execute(
    "SELECT COUNT(*), COUNT(DISTINCT scraped_at) FROM echerha_records"
).fetchone()
first, last = conn.execute(
    "SELECT MIN(scraped_at), MAX(scraped_at) FROM echerha_records"
).fetchone()

print(f"{total} records from {runs} scrape runs ({first} -> {last})")
print()
print(f"{'crossing':<13} {'class':<16} {'virt wait':>9} {'waiting':>8}   queue")
print("-" * 78)

rows = conn.execute(
    """
    SELECT crossing_name, vehicle_class, virtual_wait_s, vehicles_waiting, echerha_title
    FROM echerha_records
    WHERE scraped_at = (SELECT MAX(scraped_at) FROM echerha_records)
    ORDER BY virtual_wait_s IS NULL, virtual_wait_s DESC
    """
).fetchall()


def fmt(seconds):
    if seconds is None:
        return "-"
    h, m = divmod(seconds // 60, 60)
    return f"{h}:{m:02d}"


for r in rows:
    print(
        f"{r['crossing_name']:<13} {r['vehicle_class']:<16} "
        f"{fmt(r['virtual_wait_s']):>9} {str(r['vehicles_waiting'] if r['vehicles_waiting'] is not None else '-'):>8}"
        f"   {r['echerha_title']}"
    )
