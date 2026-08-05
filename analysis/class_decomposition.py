"""DPSU-vs-eCherga per-vehicle-class decomposition (READ-ONLY diagnostic).

Series C (DPSU `trucks_waiting`) is ONE physical truck count per crossing.
Series B (eCherga) splits trucks into sub-queues (`truck_empty`, `truck_ge_7_5t`,
`truck_le_7_5t`, `truck_goods_1_24`). At most crossings C matches the eCherga
class SUM. This script tests, per class, whether C tracks a *single* eCherga
sub-population instead — the Dołhobyczów case, where C locks onto `truck_empty`
and ignores the loaded 3.5–7.5 t queue.

Method (clean window, hourly buckets):
  - B_<class>: per poll, `vehicles_waiting` for that class -> hourly bucket mean.
  - B_sum: per poll, sum over ALL truck sub-queues -> hourly bucket mean.
  - C: native DPSU readings (ts_synthetic=0) -> hourly bucket mean.
  - For each B series, over buckets it shares with C: n, median B, median C,
    median |B-C|/C %, median (B-C), Pearson r (levels).

The class whose median |gap| % is smallest is the population C is tracking.

PAUSED SUB-QUEUES ARE INCLUDED BY DEFAULT. `vehicles_waiting` is a *count* of
booked trucks; when a sub-queue's metering is paused (`is_paused=1`) those trucks
are still physically queued, so they belong in a physical-vs-virtual count
comparison. (This differs from the join_divergence pipeline, which drops paused
sub-queues for ALL metrics because its default metric is a *wait time*, stale
while metering is suspended. Excluding paused here would drop ~25-30% of the
dorohusk truck count on paused polls and break the C-vs-B level match — see the
"DPSU population definition" note in analysis/METHODOLOGY.md.) Pass
`--exclude-paused` to reproduce the pipeline's collapse instead.

Usage:
    python -m analysis.class_decomposition --crossing dolhobyczow
                                           [--window-start 2026-06-27T00:00:00Z]
                                           [--bucket-hours 1]
Run where data/{dpsu,echerha}.db live. Opens them read-only; writes nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.dpsu_echerha_comovement import _bucket, _ro, pearson  # noqa: E402

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_WINDOW_START = "2026-06-27T00:00:00Z"  # clean window floor (post-INC-003)


def _in_window(ts: str, ws: str | None) -> bool:
    return not ws or ts >= ws


def load_dpsu_native(crossing: str, h: float, ws: str | None) -> dict:
    """C: native DPSU truck count per bucket (mean), native readings only."""
    cells = defaultdict(list)
    with _ro("data/dpsu.db") as c:
        has_synth = any(r["name"] == "ts_synthetic"
                        for r in c.execute("PRAGMA table_info(dpsu_records)"))
        synth = "AND COALESCE(ts_synthetic,0)=0" if has_synth else ""
        seen = set()
        for r in c.execute(
            "SELECT source_updated_utc, trucks_waiting FROM dpsu_records "
            f"WHERE crossing_id=? AND trucks_waiting IS NOT NULL {synth}", (crossing,)
        ):
            if not _in_window(r["source_updated_utc"], ws):
                continue
            if r["source_updated_utc"] in seen:
                continue
            seen.add(r["source_updated_utc"])
            cells[_bucket(r["source_updated_utc"], h)].append(r["trucks_waiting"])
    return {b: statistics.fmean(v) for b, v in cells.items()}


def load_echerha_by_class(crossing: str, h: float, ws: str | None,
                          exclude_paused: bool = False) -> dict[str, dict]:
    """B per class AND B_sum: per poll vehicles_waiting -> hourly bucket mean.
    Returns {class_label: {bucket: value}} plus a synthetic '__sum__' series.

    Paused sub-queues are INCLUDED by default (a paused queue's booked count is
    still a real physical queue); pass exclude_paused=True for the pipeline's
    wait-metric collapse."""
    paused_filter = "AND COALESCE(is_paused,0)=0 " if exclude_paused else ""
    per_class_poll: dict[tuple[str, str], float] = defaultdict(float)
    per_sum_poll: dict[str, float] = defaultdict(float)
    with _ro("data/echerha.db") as c:
        for r in c.execute(
            "SELECT scraped_at, vehicle_class, vehicles_waiting FROM echerha_records "
            "WHERE crossing_id=? AND vehicle_class LIKE 'truck%' "
            f"{paused_filter}AND vehicles_waiting IS NOT NULL", (crossing,)
        ):
            if not _in_window(r["scraped_at"], ws):
                continue
            per_class_poll[(r["vehicle_class"], r["scraped_at"])] += r["vehicles_waiting"]
            per_sum_poll[r["scraped_at"]] += r["vehicles_waiting"]

    by_class: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for (cls, scraped_at), v in per_class_poll.items():
        by_class[cls][_bucket(scraped_at, h)].append(v)
    sum_cells = defaultdict(list)
    for scraped_at, v in per_sum_poll.items():
        sum_cells[_bucket(scraped_at, h)].append(v)

    out = {cls: {b: statistics.fmean(vs) for b, vs in cells.items()}
           for cls, cells in by_class.items()}
    out["__sum__"] = {b: statistics.fmean(vs) for b, vs in sum_cells.items()}
    return out


def _compare(b_series: dict, c_series: dict) -> dict | None:
    common = sorted(set(b_series) & set(c_series))
    if len(common) < 3:
        return {"n": len(common), "insufficient": True}
    bs = [b_series[k] for k in common]
    cs = [c_series[k] for k in common]
    gaps = [abs(b - c) / c * 100.0 for b, c in zip(bs, cs) if c]
    diffs = [b - c for b, c in zip(bs, cs)]
    return {
        "n": len(common),
        "med_b": statistics.median(bs),
        "med_c": statistics.median(cs),
        "med_gap_pct": statistics.median(gaps) if gaps else float("nan"),
        "med_b_minus_c": statistics.median(diffs),
        "r_levels": pearson(bs, cs),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crossing", required=True)
    ap.add_argument("--window-start", default=DEFAULT_WINDOW_START,
                    help=f"ISO floor (default {DEFAULT_WINDOW_START}; '' for full history)")
    ap.add_argument("--bucket-hours", type=float, default=1.0)
    ap.add_argument("--exclude-paused", action="store_true",
                    help="drop is_paused=1 sub-queues (pipeline wait-metric collapse); "
                         "default INCLUDES them (count comparison)")
    a = ap.parse_args()
    ws = a.window_start or None

    c_series = load_dpsu_native(a.crossing, a.bucket_hours, ws)
    b_by_class = load_echerha_by_class(a.crossing, a.bucket_hours, ws, a.exclude_paused)

    print(f"Class decomposition - {a.crossing}  (READ-ONLY)")
    print(f"  window-start={ws or 'ALL'}  bucket={a.bucket_hours}h  "
          f"C native buckets={len(c_series)}")
    print("  C = DPSU native truck count; B_<class> = eCherga vehicles_waiting "
          "per sub-queue; B_sum = cross-class sum.\n")

    hdr = (f"{'B series':<18} {'n':>4} {'med_B':>8} {'med_C':>8} "
           f"{'med|gap|%':>10} {'med(B-C)':>9} {'r_lvl':>7}")
    print(hdr)
    print("-" * len(hdr))

    # order: each class, then the sum, then flag the best-matching single class
    order = sorted(k for k in b_by_class if k != "__sum__") + ["__sum__"]
    best_cls, best_gap = None, float("inf")
    for cls in order:
        res = _compare(b_by_class[cls], c_series)
        label = "B_sum (all classes)" if cls == "__sum__" else f"B_{cls}"
        if res.get("insufficient"):
            print(f"  {label:<18} {res['n']:>4}   (insufficient overlap)")
            continue
        r = res["r_levels"]
        print(f"  {label:<18} {res['n']:>4} {res['med_b']:>8.1f} {res['med_c']:>8.1f} "
              f"{res['med_gap_pct']:>10.1f} {res['med_b_minus_c']:>+9.1f} "
              f"{('  n/a' if r is None else f'{r:+.3f}'):>7}")
        if cls != "__sum__" and res["med_gap_pct"] < best_gap:
            best_gap, best_cls = res["med_gap_pct"], cls

    if best_cls is not None:
        sum_res = _compare(b_by_class["__sum__"], c_series)
        sum_gap = sum_res.get("med_gap_pct", float("nan"))
        print(f"\n  Best single-class match to C: B_{best_cls} "
              f"(median |gap| {best_gap:.1f}%) vs B_sum ({sum_gap:.1f}%).")
        if best_gap + 5 < sum_gap:
            print(f"  => C tracks the '{best_cls}' sub-population, NOT the class sum "
                  f"at this crossing. Use B_{best_cls} for C-vs-B here.")
        else:
            print("  => C tracks the class SUM here (control behaviour).")


if __name__ == "__main__":
    main()
