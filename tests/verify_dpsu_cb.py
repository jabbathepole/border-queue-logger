"""Offline unit test for the C-vs-B same-direction divergence in
analysis/join_divergence.py (PR 2). Synthetic data; no network.

Covers the three things the build brief calls out for verification:
  - the dpsu_rank percentile baseline is over each crossing's DISTINCT NATIVE
    readings, NOT the forward-filled bucket series (a value repeated across many
    buckets must not skew the distribution) — §2a;
  - load_dpsu() EXCLUDES ts_synthetic=1 rows from the baseline — FIX 1.1;
  - a bucket past the freshness cutoff gets dpsu_stale=True and dpsu_rank=None
    while its raw dpsu_trucks / age columns are kept — §2b;
  - cb_divergence_rank = dpsu_rank - virt_rank and cb_quadrant only where a fresh
    DPSU rank AND an eCherga rank both exist — §2c;
  - a crossing with too few native readings gets no dpsu_rank (sufficiency gate).
"""
import datetime as dt
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.join_divergence import (
    Params,
    JoinedRow,
    _percentile_rank,
    load_dpsu,
    normalise_dpsu_and_diverge,
)
from dpsu_db import init_db, insert_records

UTC = dt.timezone.utc
failures: list[str] = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{(' — ' + detail) if detail and not cond else ''}")
    if not cond:
        failures.append(label)


_BASE = dt.datetime(2026, 6, 20, 0, 0, 0, tzinfo=UTC)


def bkt(h):
    """A bucket `h` hours after a fixed base (h may exceed 23)."""
    return _BASE + dt.timedelta(hours=h)


def mkrow(crossing, bucket, *, trucks=None, age_s=None, virt_rank=None, virt_elev=None):
    r = JoinedRow(
        crossing=crossing, bucket=bucket,
        phys_mean=None, phys_min=None, phys_max=None,
        virt_mean=None, virt_min=None, virt_max=None, complete=virt_rank is not None,
    )
    r.dpsu_trucks = trucks
    r.dpsu_reading_age_s = age_s
    r.virt_rank = virt_rank
    r.virt_elevated = virt_elev
    return r


# --- 1. baseline over native readings, not forward-filled repeats (§2a) ------
# Native distinct readings: 10, 20, 30. The value 10 is then forward-filled
# across MANY buckets. If the baseline were (wrongly) built from the bucket
# series, rank(20) would be ~0.98 (fifty 10s below it); over the native set it
# must be 2/3 ≈ 0.667.
p = Params(queues_db="", echerha_db="", dpsu_db="", out_dir="",
           min_buckets=3, dpsu_max_age_hours=6.0)
native = {"dorohusk": [(bkt(0), 10.0), (bkt(1), 20.0), (bkt(2), 30.0)]}

rows = [mkrow("dorohusk", bkt(10 + i), trucks=10.0, age_s=0) for i in range(50)]
row20 = mkrow("dorohusk", bkt(9), trucks=20.0, age_s=0, virt_rank=0.5, virt_elev=False)
row30 = mkrow("dorohusk", bkt(8), trucks=30.0, age_s=0, virt_rank=0.1, virt_elev=False)
rows += [row20, row30]

report = normalise_dpsu_and_diverge(rows, native, p)

expect20 = _percentile_rank(20.0, [10.0, 20.0, 30.0])
check("baseline uses 3 distinct native readings, not 52 bucket values",
      abs(row20.dpsu_rank - expect20) < 1e-9 and abs(expect20 - 2 / 3) < 1e-9,
      f"rank(20)={row20.dpsu_rank} expected {expect20}")
check("native-readings count reported", report["dorohusk"][0] == 3, str(report["dorohusk"]))

# --- 2. staleness gate (§2b): raw kept, normalised dropped -------------------
stale = mkrow("dorohusk", bkt(7), trucks=30.0, age_s=7 * 3600)  # 7h > 6h cutoff
fresh = mkrow("dorohusk", bkt(6), trucks=30.0, age_s=1 * 3600)  # 1h < cutoff
normalise_dpsu_and_diverge([stale, fresh], native, p)
check("stale bucket: dpsu_stale=True, dpsu_rank=None",
      stale.dpsu_stale is True and stale.dpsu_rank is None)
check("stale bucket keeps raw trucks + age (unfiltered)",
      stale.dpsu_trucks == 30.0 and stale.dpsu_reading_age_s == 7 * 3600)
check("fresh bucket: dpsu_stale=False, dpsu_rank set",
      fresh.dpsu_stale is False and fresh.dpsu_rank is not None)

# --- 3. cb_divergence + quadrant only where virt_rank present (§2c) ----------
# row20 had virt_rank 0.5, virt_elev False; dpsu_rank 0.667, dpsu_elevated?
# elevated_pct default 75 -> threshold over [10,20,30] = 25.0, so 20 is NOT
# elevated. (False phys, False virt) -> aligned_quiet.
check("cb_divergence_rank = dpsu_rank - virt_rank",
      abs(row20.cb_divergence_rank - (row20.dpsu_rank - 0.5)) < 1e-9,
      str(row20.cb_divergence_rank))
check("cb_quadrant aligned_quiet (neither elevated)",
      row20.cb_quadrant == "aligned_quiet", str(row20.cb_quadrant))
# row30: trucks 30 IS elevated (> p75=25), virt_elev False -> physical_only.
check("cb_quadrant physical_only (phys elevated, virt not)",
      row30.cb_quadrant == "physical_only", str(row30.cb_quadrant))
# a fresh fill with NO eCherga rank gets no C-vs-B comparison
no_virt = mkrow("dorohusk", bkt(5), trucks=30.0, age_s=0)
normalise_dpsu_and_diverge([no_virt], native, p)
check("no virt_rank -> dpsu_rank set but cb_divergence_rank None",
      no_virt.dpsu_rank is not None and no_virt.cb_divergence_rank is None)

# --- 4. sufficiency gate: too few native readings -> no dpsu_rank ------------
thin_native = {"zosin": [(bkt(0), 5.0), (bkt(1), 6.0)]}  # 2 < min_buckets(3)
thin = mkrow("zosin", bkt(10), trucks=6.0, age_s=0)
rpt = normalise_dpsu_and_diverge([thin], thin_native, p)
check("thin crossing: fresh fill but dpsu_rank None (insufficient baseline)",
      thin.dpsu_stale is False and thin.dpsu_rank is None,
      f"stale={thin.dpsu_stale} rank={thin.dpsu_rank}")
check("thin native count reported", rpt["zosin"][0] == 2, str(rpt["zosin"]))

# --- 5. load_dpsu excludes ts_synthetic=1 (FIX 1.1) -------------------------
tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, "dpsu.db")
init_db(db_path)


def _rec(src_utc, trucks, synthetic):
    return {
        "scraped_at": "2026-06-20T12:00:00Z", "crossing_id": "dorohusk",
        "crossing_name": "Dorohusk", "dpsu_name": "x", "trucks_waiting": trucks,
        "cars_waiting": None, "cars_per_hour": None, "load_color": None,
        "state": "відкритий", "closure_flag": 0, "parse_miss_flag": 0,
        "restricted_flag": 0, "character": None, "category": None, "location": None,
        "lat": None, "lng": None, "camera_url": None, "source_updated_kyiv": None,
        "source_updated_utc": src_utc, "reading_age_seconds": 0,
        "ts_synthetic": synthetic, "state_of_busy_raw": None,
    }


insert_records(db_path, [
    _rec("2026-06-20T10:00:00Z", 100, 0),   # native -> kept
    _rec("2026-06-20T11:00:00Z", 999, 1),   # synthetic -> excluded
])
pp = Params(queues_db="", echerha_db="", dpsu_db=db_path, out_dir="")
loaded = load_dpsu(pp)
vals = [v for _, v in loaded.get("dorohusk", [])]
check("load_dpsu keeps native reading, drops ts_synthetic=1",
      vals == [100.0], str(vals))

print()
print("RESULT:", "ALL CHECKS PASS" if not failures else f"{len(failures)} FAILURES: {failures}")
if failures:
    sys.exit(1)
