"""Post-merge sanity check (read-only): do the DPSU physical truck queue and the
eCherga virtual truck queue for the SAME crossing and SAME direction (UA->PL)
co-move?

They will NOT be equal — DPSU `trucks_waiting` is the physical approach count and
eCherga `vehicles_waiting` is the booked virtual-queue count, different
populations — but on day one they should be the same order of magnitude and rise
/ fall together. Wild, sustained divergence on day one is more likely a
direction/unit bug than a real signal; treat it as the former until proven
otherwise.

Usage:
    python tests/dpsu_echerha_comovement.py [--crossing dorohusk] [--bucket-hours 1]
Needs a day or so of data in data/dpsu.db and data/echerha.db. Prints an aligned
table + Pearson r over overlapping buckets; saves a PNG if matplotlib is present.
"""
import argparse
import datetime as dt
import math
import os
import sqlite3
import statistics
import sys
from collections import defaultdict

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _ro(path):
    if not os.path.exists(path):
        sys.exit(f"{path} not found — run from a checkout with data/*.db present.")
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _bucket(ts_str, h):
    ts = dt.datetime.strptime(ts_str, TS_FMT).replace(tzinfo=dt.timezone.utc)
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    size = h * 3600
    secs = (ts - epoch).total_seconds()
    return epoch + dt.timedelta(seconds=math.floor(secs / size) * size)


def load_dpsu(crossing, h, window_start=None):
    """DPSU physical truck count per bucket (mean of readings in the bucket),
    keyed on the source update time. Native readings only (ts_synthetic=0 when the
    column exists). Optional window_start (ISO 'YYYY-MM-DDTHH:MM:SSZ') drops
    readings before that instant — pass the clean-window floor to exclude the
    pre-blackout stub (INC-003)."""
    cells = defaultdict(list)
    with _ro("data/dpsu.db") as c:
        has_synth = any(r["name"] == "ts_synthetic"
                        for r in c.execute("PRAGMA table_info(dpsu_records)"))
        synth = "AND COALESCE(ts_synthetic,0)=0" if has_synth else ""
        for r in c.execute(
            "SELECT source_updated_utc, trucks_waiting FROM dpsu_records "
            f"WHERE crossing_id=? AND trucks_waiting IS NOT NULL {synth}", (crossing,)
        ):
            if window_start and r["source_updated_utc"] < window_start:
                continue
            cells[_bucket(r["source_updated_utc"], h)].append(r["trucks_waiting"])
    return {b: statistics.fmean(v) for b, v in cells.items()}


def load_echerha(crossing, h, window_start=None, include_paused=False):
    """eCherga virtual booked truck count per bucket: sum sub-queues per poll
    (total trucks queued at the crossing), then mean across the bucket.

    include_paused: by default paused sub-queues are dropped (matching the wait-
    metric collapse in join_divergence). For a COUNT comparison, pass True — a
    paused sub-queue's `vehicles_waiting` is still a real physical backlog, and
    excluding it decorrelates crossings with heavy paused queues (e.g. dorohusk
    r(C,B) collapses from ~0.99 to ~0.05). See analysis/METHODOLOGY.md.

    window_start (ISO) drops polls before that instant."""
    paused = "" if include_paused else "AND COALESCE(is_paused,0)=0 "
    per_poll = defaultdict(float)
    with _ro("data/echerha.db") as c:
        for r in c.execute(
            "SELECT scraped_at, vehicles_waiting FROM echerha_records "
            "WHERE crossing_id=? AND vehicle_class LIKE 'truck%' "
            f"{paused}AND vehicles_waiting IS NOT NULL", (crossing,)
        ):
            if window_start and r["scraped_at"] < window_start:
                continue
            per_poll[(r["scraped_at"])] += r["vehicles_waiting"]
    cells = defaultdict(list)
    for scraped_at, total in per_poll.items():
        cells[_bucket(scraped_at, h)].append(total)
    return {b: statistics.fmean(v) for b, v in cells.items()}


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crossing", default="dorohusk")
    ap.add_argument("--bucket-hours", type=float, default=1.0)
    a = ap.parse_args()

    phys = load_dpsu(a.crossing, a.bucket_hours)
    virt = load_echerha(a.crossing, a.bucket_hours)
    common = sorted(set(phys) & set(virt))

    print(f"crossing={a.crossing}  bucket={a.bucket_hours}h  "
          f"dpsu buckets={len(phys)}  echerha buckets={len(virt)}  overlap={len(common)}")
    if not common:
        print("No overlapping buckets yet — let both loggers accumulate a day, then re-run.")
        return
    print(f"\n{'bucket_utc':<22} {'dpsu_phys':>10} {'echerha_virt':>13}")
    print("-" * 47)
    for b in common:
        print(f"{b.strftime(TS_FMT):<22} {phys[b]:>10.0f} {virt[b]:>13.0f}")

    xs = [phys[b] for b in common]
    ys = [virt[b] for b in common]
    r = pearson(xs, ys)
    ratio = (statistics.fmean(ys) / statistics.fmean(xs)) if statistics.fmean(xs) else float("nan")
    print(f"\nPearson r = {r if r is None else round(r, 3)}  "
          f"(n={len(common)});  mean virt/phys ratio = {ratio:.2f}")
    print("Expectation: same order of magnitude (ratio within ~0.1x–10x) and r > 0. "
          "If r is strongly negative or the ratio is wild, suspect a direction/unit "
          "bug before celebrating a signal.")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax1 = plt.subplots(figsize=(10, 4))
        ax1.plot(common, xs, "b-o", label="DPSU physical trucks")
        ax1.set_ylabel("DPSU physical trucks", color="b")
        ax2 = ax1.twinx()
        ax2.plot(common, ys, "r-s", label="eCherga virtual booked trucks")
        ax2.set_ylabel("eCherga virtual booked", color="r")
        ax1.set_title(f"{a.crossing} UA->PL: physical (DPSU) vs virtual (eCherga) — co-movement check")
        out = f"analysis/output/comovement_{a.crossing}.png"
        os.makedirs("analysis/output", exist_ok=True)
        fig.autofmt_xdate()
        fig.savefig(out, dpi=110, bbox_inches="tight")
        print(f"wrote {out}")
    except ImportError:
        print("(matplotlib not installed — skipped PNG)")


if __name__ == "__main__":
    main()
