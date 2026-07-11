"""DPSU diurnal-coverage characterisation (READ-ONLY diagnostic).

Series C (DPSU) is not a uniform ~3 h feed: it has a recurrent OVERNIGHT DEAD
ZONE. This script measures it directly from `data/dpsu.db`, using only NATIVE
readings — distinct `(crossing_id, source_updated_utc)` with `ts_synthetic=0` —
over the clean window (default `>= 2026-06-27`, after the INC-003 403 blackout).

It prints two things per crossing (and pooled):
  1. hour-of-day (UTC) histogram of native readings — where in the day DPSU
     actually publishes;
  2. the distribution of inter-reading gaps (median / p90 / max) and the
     readings-per-day rate.

This is DOCUMENTED NORMAL SOURCE BEHAVIOUR, not a fault: the source simply does
not refresh overnight. It is therefore characterised here (and in
RECON_dpsu_map.md + analysis/METHODOLOGY.md), NOT alerted on. Do not add staleness
alerting for the overnight gap.

Usage:
    python -m analysis.dpsu_coverage [--window-start 2026-06-27T00:00:00Z]
                                     [--dpsu-db data/dpsu.db] [--png]
Run where data/dpsu.db lives. Opens it read-only and writes nothing to it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crossings import CANONICAL_NAMES  # noqa: E402

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_WINDOW_START = "2026-06-27T00:00:00Z"  # clean window floor (post-INC-003)


def _ro(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        sys.exit(f"{path} not found — run from a checkout with data/dpsu.db present.")
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _parse(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, TS_FMT).replace(tzinfo=dt.timezone.utc)


def load_native(db: str, window_start: str | None) -> dict[str, list[dt.datetime]]:
    """Per crossing, the sorted distinct NATIVE source update times (ts_synthetic=0,
    trucks_waiting present). These are the real DPSU publications, not our polls."""
    series: dict[str, set[str]] = defaultdict(set)
    with _ro(db) as c:
        has_synth = any(r["name"] == "ts_synthetic"
                        for r in c.execute("PRAGMA table_info(dpsu_records)"))
        synth = "AND COALESCE(ts_synthetic,0)=0" if has_synth else ""
        q = ("SELECT crossing_id, source_updated_utc FROM dpsu_records "
             f"WHERE trucks_waiting IS NOT NULL {synth}")
        for r in c.execute(q):
            if window_start and r["source_updated_utc"] < window_start:
                continue
            series[r["crossing_id"]].add(r["source_updated_utc"])
    return {k: sorted(_parse(s) for s in v) for k, v in series.items()}


def _percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dpsu-db", default="data/dpsu.db")
    ap.add_argument("--window-start", default=DEFAULT_WINDOW_START,
                    help=f"ISO floor for native readings (default {DEFAULT_WINDOW_START}; "
                         "pass '' for the full history including the pre-blackout stub)")
    ap.add_argument("--png", action="store_true", help="also write a pooled histogram PNG")
    a = ap.parse_args()
    ws = a.window_start or None

    series = load_native(a.dpsu_db, ws)
    if not series:
        sys.exit("no native DPSU readings in window — check --window-start / db path.")

    span_lo = min(t for v in series.values() for t in v)
    span_hi = max(t for v in series.values() for t in v)
    span_days = (span_hi - span_lo).total_seconds() / 86400.0

    print("DPSU diurnal-coverage characterisation (READ-ONLY)")
    print(f"  native readings only (ts_synthetic=0); window-start={ws or 'ALL'}")
    print(f"  observed span: {span_lo.strftime(TS_FMT)} -> {span_hi.strftime(TS_FMT)} "
          f"({span_days:.1f} days)\n")

    # ---- 1. hour-of-day (UTC) histogram ----
    pooled = [0] * 24
    per_hour: dict[str, list[int]] = {}
    for c in sorted(series):
        h = [0] * 24
        for t in series[c]:
            h[t.hour] += 1
            pooled[t.hour] += 1
        per_hour[c] = h

    print("Hour-of-day (UTC) histogram of native readings (pooled across crossings):")
    peak = max(pooled) or 1
    for hr in range(24):
        bar = "#" * round(40 * pooled[hr] / peak)
        flag = "   <-- DEAD ZONE" if pooled[hr] == 0 else ""
        print(f"  {hr:02d}:00  {pooled[hr]:4d} {bar}{flag}")
    dead = [hr for hr in range(24) if pooled[hr] == 0]
    active = [hr for hr in range(24) if pooled[hr] > 0]
    if active:
        print(f"\n  active UTC hours: {active[0]:02d}-{active[-1]:02d}; "
              f"zero-reading hours: {sorted(dead)}")

    # ---- 2. inter-reading gaps + rate, per crossing ----
    print(f"\n{'crossing':<14} {'n':>4} {'reads/day':>9} {'gap_med_h':>9} "
          f"{'gap_p90_h':>9} {'gap_max_h':>9}")
    print("-" * 60)
    for c in sorted(series):
        ts = series[c]
        n = len(ts)
        gaps = [(ts[i] - ts[i - 1]).total_seconds() / 3600.0 for i in range(1, n)]
        rate = n / span_days if span_days else float("nan")
        gmed = _percentile(gaps, 50) if gaps else float("nan")
        gp90 = _percentile(gaps, 90) if gaps else float("nan")
        gmax = max(gaps) if gaps else float("nan")
        print(f"  {c:<12} {n:>4} {rate:>9.1f} {gmed:>9.1f} {gp90:>9.1f} {gmax:>9.1f}")

    print("\nRead this as documented NORMAL source behaviour: DPSU publishes in a "
          "daytime band with a recurrent overnight dead zone, so the effective rate "
          "is well below what the nominal '~3 h refresh' implies. The 6 h C-vs-B "
          "freshness cutoff therefore structurally drops most night buckets. This is "
          "characterised, NOT alerted on (see RECON_dpsu_map.md / METHODOLOGY.md).")

    if a.png:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            os.makedirs("analysis/output", exist_ok=True)
            fig, ax = plt.subplots(figsize=(9, 4))
            ax.bar(range(24), pooled)
            ax.set_xlabel("hour of day (UTC)")
            ax.set_ylabel("native DPSU readings")
            ax.set_title("DPSU native readings by UTC hour — the overnight dead zone")
            ax.set_xticks(range(0, 24, 2))
            out = "analysis/output/dpsu_coverage_hours.png"
            fig.savefig(out, dpi=110, bbox_inches="tight")
            print(f"\nwrote {out}")
        except ImportError:
            print("\n(matplotlib not installed — PNG skipped)")


if __name__ == "__main__":
    main()
