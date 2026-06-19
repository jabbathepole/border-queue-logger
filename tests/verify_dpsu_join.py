"""Offline unit test for the DPSU forward-fill in analysis/join_divergence.py
(FIXES 1 & 2). Synthetic data; no DB, no network.

Verifies attach_dpsu():
  - a bucket BEFORE the first DPSU reading gets nothing (no backward leak),
  - a bucket AT a reading gets that value with age 0,
  - a bucket BETWEEN readings forward-fills the previous value with growing age,
  - reading age is per-(crossing,bucket), and another crossing is independent.
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.join_divergence import JoinedRow, attach_dpsu

UTC = dt.timezone.utc
failures: list[str] = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{(' — ' + detail) if detail and not cond else ''}")
    if not cond:
        failures.append(label)


def bucket(h):
    return dt.datetime(2026, 6, 20, h, 0, 0, tzinfo=UTC)


# Grid: hourly buckets 10:00..13:00 for dorohusk, plus one zosin bucket.
rows = [
    JoinedRow(crossing="dorohusk", bucket=bucket(10), phys_mean=None, phys_min=None,
              phys_max=None, virt_mean=None, virt_min=None, virt_max=None, complete=False),
    JoinedRow(crossing="dorohusk", bucket=bucket(11), phys_mean=None, phys_min=None,
              phys_max=None, virt_mean=None, virt_min=None, virt_max=None, complete=False),
    JoinedRow(crossing="dorohusk", bucket=bucket(12), phys_mean=None, phys_min=None,
              phys_max=None, virt_mean=None, virt_min=None, virt_max=None, complete=False),
    JoinedRow(crossing="dorohusk", bucket=bucket(13), phys_mean=None, phys_min=None,
              phys_max=None, virt_mean=None, virt_min=None, virt_max=None, complete=False),
    JoinedRow(crossing="zosin", bucket=bucket(12), phys_mean=None, phys_min=None,
              phys_max=None, virt_mean=None, virt_min=None, virt_max=None, complete=False),
]

# DPSU readings: dorohusk updates at 11:00 (100) and 13:00 (200); zosin at 09:30 (50).
dpsu = {
    "dorohusk": [(bucket(11), 100.0), (bucket(13), 200.0)],
    "zosin": [(dt.datetime(2026, 6, 20, 9, 30, tzinfo=UTC), 50.0)],
}

filled = attach_dpsu(rows, dpsu)
by = {(r.crossing, r.bucket.hour): r for r in rows}

check("10:00 precedes first reading -> no fill", by[("dorohusk", 10)].dpsu_trucks is None)
check("11:00 AT reading -> 100, age 0",
      by[("dorohusk", 11)].dpsu_trucks == 100.0 and by[("dorohusk", 11)].dpsu_reading_age_s == 0)
check("12:00 forward-filled -> 100, age 1h",
      by[("dorohusk", 12)].dpsu_trucks == 100.0 and by[("dorohusk", 12)].dpsu_reading_age_s == 3600,
      str(by[("dorohusk", 12)].dpsu_reading_age_s))
check("13:00 new reading -> 200, age 0",
      by[("dorohusk", 13)].dpsu_trucks == 200.0 and by[("dorohusk", 13)].dpsu_reading_age_s == 0)
check("zosin 12:00 forward-filled from 09:30 -> 50, age 2.5h",
      by[("zosin", 12)].dpsu_trucks == 50.0 and by[("zosin", 12)].dpsu_reading_age_s == 9000,
      str(by[("zosin", 12)].dpsu_reading_age_s))
check("source_updated_utc recorded as ...Z string",
      by[("dorohusk", 12)].dpsu_src_updated_utc == "2026-06-20T11:00:00Z",
      by[("dorohusk", 12)].dpsu_src_updated_utc)
check("filled count = 4 (one bucket precedes its first reading)", filled == 4, str(filled))

print()
print("RESULT:", "ALL CHECKS PASS" if not failures else f"{len(failures)} FAILURES: {failures}")
if failures:
    sys.exit(1)
