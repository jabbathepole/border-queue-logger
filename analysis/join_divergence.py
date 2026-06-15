"""
PL-physical vs UA-virtual join & divergence analysis.

READ-ONLY analysis layer over the two loggers in this repo:
  - granica.gov.pl  -> Polish PHYSICAL wait (minutes), `data/queues.db`
  - eCherga          -> Ukrainian VIRTUAL queue (seconds + vehicle count),
                        `data/echerha.db`

It opens both loggers' SQLite files in read-only mode and writes its own outputs
to a SEPARATE analysis db / CSVs. It never writes to, locks, or otherwise
interferes with the loggers' databases or the live pipelines.

⚠️ DIRECTIONAL ASYMMETRY (read METHODOLOGY.md before interpreting output):
granica publishes only the `wyjazd` direction = trucks LEAVING Poland = PL->UA.
The `wjazd` (entering Poland = UA->PL) columns are always NULL — the Polish
source does not publish them. eCherga's queue is for trucks LEAVING Ukraine =
UA->PL. So the only data-bearing pairing crosses directions: outbound PL->UA
physical wait vs inbound UA->PL virtual queue. This is therefore NOT a
same-flow divergence; it is a directional-asymmetry comparison and every output
is labelled as such. See METHODOLOGY.md §Direction.

Steps implemented (per the project brief):
  1. resample both feeds onto a common time grid (bucket size parameterised)
  2. normalise per crossing (percentile rank primary, z-score cross-check)
  3. classify each joined bucket into the 2x2 interpretation quadrant
  4. rank decoupling events (contiguous |divergence| > threshold runs)
  5. lead/lag cross-correlation per crossing
  6. write joined table, events table, per-crossing summary (+ optional charts)

Nothing is forward-filled or fabricated: a bucket missing either side is marked
incomplete and excluded from divergence. Crossings with fewer than
--min-buckets complete observations are reported but skipped for steps 2-5, so a
thin observation window produces an honest "insufficient data" result rather
than degenerate statistics.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import os
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

# Fixed direction labels for the two feeds (see module docstring / METHODOLOGY).
PHYSICAL_DIRECTION = "PL_to_UA_outbound"   # granica wyjazd
VIRTUAL_DIRECTION = "UA_to_PL_inbound"     # eCherga (leaving UA -> entering PL)
VEHICLE_CLASS = "truck"                    # trucks-to-trucks only

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
@dataclass
class Params:
    queues_db: str
    echerha_db: str
    out_dir: str
    bucket_hours: float = 1.0
    elevated_pct: float = 75.0      # per-crossing percentile = "elevated"
    decouple_pct: float = 90.0      # per-crossing |divergence| percentile = event threshold
    min_buckets: int = 24           # min complete buckets per crossing for steps 2-5
    lag_hours: int = 24             # cross-correlation lag scan range (+/-)
    virtual_metric: str = "virtual_wait_s"   # or "vehicles_waiting"
    truck_wait_agg: str = "max"     # how to collapse eCherga truck sub-queues' wait: max|mean
    window_start: str | None = None  # ISO 'YYYY-MM-DDTHH:MM:SSZ' inclusive
    window_end: str | None = None
    make_charts: bool = False


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _connect_ro(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Run this where the loggers' data lives (e.g. the "
            f"`master` branch / a checkout with data/*.db present)."
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_ts(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, TS_FMT).replace(tzinfo=dt.timezone.utc)


def _bucket_start(ts: dt.datetime, bucket_hours: float) -> dt.datetime:
    """Floor a timestamp to the start of its bucket (UTC), bucket sized in hours."""
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    secs = (ts - epoch).total_seconds()
    size = bucket_hours * 3600.0
    return epoch + dt.timedelta(seconds=math.floor(secs / size) * size)


def _percentile_rank(value: float, sorted_vals: list[float]) -> float:
    """Fraction of the distribution <= value, in [0,1]. Ties counted at-or-below.

    Window-dependent by construction (see METHODOLOGY §Normalisation limits)."""
    n = len(sorted_vals)
    if n == 0:
        return float("nan")
    if n == 1:
        return 0.5
    # count of values <= value
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] <= value:
            lo = mid + 1
        else:
            hi = mid
    return lo / n


def _zscore(value: float, mean: float, sd: float) -> float:
    if sd == 0 or math.isnan(sd):
        return 0.0
    return (value - mean) / sd


def _pearson(xs: list[float], ys: list[float]) -> float | None:
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


# --------------------------------------------------------------------------- #
# Step 1 — load + resample each feed onto the common grid
# --------------------------------------------------------------------------- #
@dataclass
class Cell:
    """One (crossing, bucket) aggregate for one feed."""
    values: list[float] = field(default_factory=list)

    def add(self, v: float) -> None:
        self.values.append(v)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.values)

    @property
    def vmin(self) -> float:
        return min(self.values)

    @property
    def vmax(self) -> float:
        return max(self.values)


def load_physical(p: Params) -> dict[tuple[str, dt.datetime], Cell]:
    """granica wyjazd truck wait (minutes) -> bucketed mean per (crossing, bucket)."""
    out: dict[tuple[str, dt.datetime], Cell] = defaultdict(Cell)
    with _connect_ro(p.queues_db) as conn:
        rows = conn.execute(
            "SELECT scraped_at, crossing_id, trucks_exit_min "
            "FROM queue_records WHERE trucks_exit_min IS NOT NULL"
        ).fetchall()
    for r in rows:
        ts = _parse_ts(r["scraped_at"])
        if not _in_window(ts, p):
            continue
        b = _bucket_start(ts, p.bucket_hours)
        out[(r["crossing_id"], b)].add(float(r["trucks_exit_min"]))
    return out


def load_virtual(p: Params) -> dict[tuple[str, dt.datetime], Cell]:
    """eCherga truck virtual queue -> bucketed mean per (crossing, bucket).

    Per poll, a crossing has several truck sub-queues (tonnage / empty / goods);
    granica gives a single truck figure, so we first collapse eCherga's truck
    sub-queues to one value per (crossing, poll), then bucket. Paused sub-queues
    are excluded (their wait estimate is stale while metering is suspended)."""
    metric = p.virtual_metric  # virtual_wait_s | vehicles_waiting
    # gather per (crossing, exact scraped_at): list of sub-queue metric values
    per_poll: dict[tuple[str, str], list[float]] = defaultdict(list)
    with _connect_ro(p.echerha_db) as conn:
        rows = conn.execute(
            f"SELECT scraped_at, crossing_id, vehicle_class, is_paused, {metric} AS m "
            "FROM echerha_records "
            "WHERE vehicle_class LIKE 'truck%' AND COALESCE(is_paused,0)=0 "
            f"AND {metric} IS NOT NULL"
        ).fetchall()
    for r in rows:
        ts = _parse_ts(r["scraped_at"])
        if not _in_window(ts, p):
            continue
        per_poll[(r["crossing_id"], r["scraped_at"])].append(float(r["m"]))

    out: dict[tuple[str, dt.datetime], Cell] = defaultdict(Cell)
    for (crossing, scraped_at), vals in per_poll.items():
        if not vals:
            continue
        if metric == "vehicles_waiting":
            collapsed = float(sum(vals))            # total trucks queued at crossing
        elif p.truck_wait_agg == "mean":
            collapsed = statistics.fmean(vals)
        else:  # "max": worst-case wait a trucker faces among the crossing's queues
            collapsed = max(vals)
        b = _bucket_start(_parse_ts(scraped_at), p.bucket_hours)
        out[(crossing, b)].add(collapsed)
    return out


def _in_window(ts: dt.datetime, p: Params) -> bool:
    if p.window_start and ts < _parse_ts(p.window_start):
        return False
    if p.window_end and ts > _parse_ts(p.window_end):
        return False
    return True


# --------------------------------------------------------------------------- #
# Steps 2-3 — normalise + join + quadrant
# --------------------------------------------------------------------------- #
@dataclass
class JoinedRow:
    crossing: str
    bucket: dt.datetime
    phys_mean: float | None
    phys_min: float | None
    phys_max: float | None
    virt_mean: float | None
    virt_min: float | None
    virt_max: float | None
    complete: bool
    # filled in steps 2-3 (only for complete rows in sufficient-data crossings):
    phys_rank: float | None = None
    virt_rank: float | None = None
    phys_z: float | None = None
    virt_z: float | None = None
    divergence: float | None = None       # rank_phys - rank_virt, in [-1, 1]
    divergence_z: float | None = None      # z_phys - z_virt
    phys_elevated: bool | None = None
    virt_elevated: bool | None = None
    quadrant: str | None = None


QUADRANTS = {
    (True, True): "BOTH_ELEVATED",                # corridor saturation
    (True, False): "PHYSICAL_HIGH_VIRTUAL_NORMAL",  # constraint AT the crossing
    (False, True): "VIRTUAL_HIGH_PHYSICAL_NORMAL",  # hidden upstream backlog
    (False, False): "BOTH_NORMAL",                  # flowing freely
}


def build_joined(
    physical: dict[tuple[str, dt.datetime], Cell],
    virtual: dict[tuple[str, dt.datetime], Cell],
) -> list[JoinedRow]:
    keys = set(physical) | set(virtual)
    rows: list[JoinedRow] = []
    for crossing, bucket in sorted(keys, key=lambda k: (k[0], k[1])):
        pc = physical.get((crossing, bucket))
        vc = virtual.get((crossing, bucket))
        rows.append(
            JoinedRow(
                crossing=crossing,
                bucket=bucket,
                phys_mean=pc.mean if pc else None,
                phys_min=pc.vmin if pc else None,
                phys_max=pc.vmax if pc else None,
                virt_mean=vc.mean if vc else None,
                virt_min=vc.vmin if vc else None,
                virt_max=vc.vmax if vc else None,
                complete=bool(pc and vc),
            )
        )
    return rows


def normalise_and_classify(rows: list[JoinedRow], p: Params) -> dict[str, int]:
    """Fill rank/z/divergence/quadrant per crossing over its complete buckets.

    Returns {crossing: n_complete} so the caller can report which crossings were
    skipped for insufficient data."""
    by_crossing: dict[str, list[JoinedRow]] = defaultdict(list)
    for r in rows:
        if r.complete:
            by_crossing[r.crossing].append(r)

    n_complete = {}
    for crossing, crows in by_crossing.items():
        n_complete[crossing] = len(crows)
        if len(crows) < p.min_buckets:
            continue  # insufficient data: leave steps 2-3 fields as None

        phys_vals = sorted(r.phys_mean for r in crows)
        virt_vals = sorted(r.virt_mean for r in crows)
        phys_mean = statistics.fmean(phys_vals)
        virt_mean = statistics.fmean(virt_vals)
        phys_sd = statistics.pstdev(phys_vals)
        virt_sd = statistics.pstdev(virt_vals)
        phys_thr = _value_at_pct(phys_vals, p.elevated_pct)
        virt_thr = _value_at_pct(virt_vals, p.elevated_pct)

        for r in crows:
            r.phys_rank = _percentile_rank(r.phys_mean, phys_vals)
            r.virt_rank = _percentile_rank(r.virt_mean, virt_vals)
            r.phys_z = _zscore(r.phys_mean, phys_mean, phys_sd)
            r.virt_z = _zscore(r.virt_mean, virt_mean, virt_sd)
            r.divergence = r.phys_rank - r.virt_rank
            r.divergence_z = r.phys_z - r.virt_z
            r.phys_elevated = r.phys_mean > phys_thr
            r.virt_elevated = r.virt_mean > virt_thr
            r.quadrant = QUADRANTS[(r.phys_elevated, r.virt_elevated)]
    return n_complete


def _value_at_pct(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolated percentile value (numpy-free)."""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


# --------------------------------------------------------------------------- #
# Step 4 — rank decoupling events
# --------------------------------------------------------------------------- #
@dataclass
class Event:
    crossing: str
    direction_note: str
    start: dt.datetime
    end: dt.datetime
    duration_h: float
    n_buckets: int
    peak_abs_divergence: float
    mean_divergence: float
    elevated_side: str
    dominant_quadrant: str
    score: float  # peak_abs * duration_h, for ranking


def find_events(rows: list[JoinedRow], p: Params, n_complete: dict[str, int]) -> list[Event]:
    by_crossing: dict[str, list[JoinedRow]] = defaultdict(list)
    for r in rows:
        if r.complete and r.divergence is not None:
            by_crossing[r.crossing].append(r)

    events: list[Event] = []
    for crossing, crows in by_crossing.items():
        if n_complete.get(crossing, 0) < p.min_buckets:
            continue
        crows.sort(key=lambda r: r.bucket)
        abs_divs = sorted(abs(r.divergence) for r in crows)
        thr = _value_at_pct(abs_divs, p.decouple_pct)

        run: list[JoinedRow] = []
        contiguous_gap = dt.timedelta(hours=p.bucket_hours * 1.5)

        def flush(run: list[JoinedRow]) -> None:
            if not run:
                return
            divs = [r.divergence for r in run]
            peak = max(abs(d) for d in divs)
            mean_d = statistics.fmean(divs)
            elevated_side = "physical" if mean_d > 0 else "virtual"
            quad_counts = defaultdict(int)
            for r in run:
                quad_counts[r.quadrant] += 1
            dom_quad = max(quad_counts, key=quad_counts.get)
            duration_h = (run[-1].bucket - run[0].bucket).total_seconds() / 3600.0 + p.bucket_hours
            events.append(
                Event(
                    crossing=crossing,
                    direction_note=f"{PHYSICAL_DIRECTION} phys vs {VIRTUAL_DIRECTION} virt",
                    start=run[0].bucket,
                    end=run[-1].bucket,
                    duration_h=duration_h,
                    n_buckets=len(run),
                    peak_abs_divergence=peak,
                    mean_divergence=mean_d,
                    elevated_side=elevated_side,
                    dominant_quadrant=dom_quad,
                    score=peak * duration_h,
                )
            )

        prev_bucket: dt.datetime | None = None
        for r in crows:
            over = abs(r.divergence) >= thr and thr > 0
            broken = prev_bucket is not None and (r.bucket - prev_bucket) > contiguous_gap
            if over and not broken:
                run.append(r)
            elif over and broken:
                flush(run)
                run = [r]
            else:
                flush(run)
                run = []
            prev_bucket = r.bucket
        flush(run)

    events.sort(key=lambda e: e.score, reverse=True)
    return events


# --------------------------------------------------------------------------- #
# Step 5 — lead/lag cross-correlation per crossing
# --------------------------------------------------------------------------- #
@dataclass
class LagResult:
    crossing: str
    baseline_corr: float | None     # lag 0
    peak_lag_h: float | None
    peak_corr: float | None
    n_pairs_at_peak: int
    note: str


def lead_lag(rows: list[JoinedRow], p: Params, n_complete: dict[str, int]) -> list[LagResult]:
    by_crossing: dict[str, dict[dt.datetime, tuple[float, float]]] = defaultdict(dict)
    for r in rows:
        if r.complete and r.phys_rank is not None:
            by_crossing[r.crossing][r.bucket] = (r.phys_rank, r.virt_rank)

    results: list[LagResult] = []
    max_lag_buckets = int(round(p.lag_hours / p.bucket_hours))
    bucket_delta = dt.timedelta(hours=p.bucket_hours)

    for crossing, series in by_crossing.items():
        if n_complete.get(crossing, 0) < p.min_buckets:
            results.append(LagResult(crossing, None, None, None, 0, "insufficient data"))
            continue
        baseline = _corr_at_lag(series, 0, bucket_delta)
        best_lag, best_corr, best_n = None, None, 0
        for lag in range(-max_lag_buckets, max_lag_buckets + 1):
            c = _corr_at_lag(series, lag, bucket_delta)
            if c is None:
                continue
            corr, npairs = c
            if best_corr is None or abs(corr) > abs(best_corr):
                best_lag, best_corr, best_n = lag, corr, npairs
        note = ""
        if best_corr is not None and abs(best_corr) < 0.3:
            note = "weak correlation — treat best lag as noise, not a finding"
        results.append(
            LagResult(
                crossing=crossing,
                baseline_corr=baseline[0] if baseline else None,
                peak_lag_h=(best_lag * p.bucket_hours) if best_lag is not None else None,
                peak_corr=best_corr,
                n_pairs_at_peak=best_n,
                note=note,
            )
        )
    return results


def _corr_at_lag(series: dict[dt.datetime, tuple[float, float]], lag: int, bucket_delta: dt.timedelta):
    """Pearson corr of physical[t] vs virtual[t + lag].

    Sign convention (validated against a known-shift synthetic series): a high
    correlation at POSITIVE lag means virtual at a LATER time tracks physical now
    => physical LEADS, virtual lags. NEGATIVE lag => virtual leads physical (the
    brief's hypothesised "eCherga backlog as a leading indicator")."""
    xs, ys = [], []
    for bucket, (phys, _virt) in series.items():
        partner = series.get(bucket + lag * bucket_delta)
        if partner is None:
            continue
        xs.append(phys)
        ys.append(partner[1])  # virtual at t+lag
    corr = _pearson(xs, ys)
    if corr is None:
        return None
    return corr, len(xs)


# --------------------------------------------------------------------------- #
# Step 6 — outputs
# --------------------------------------------------------------------------- #
def write_outputs(
    rows: list[JoinedRow],
    events: list[Event],
    lags: list[LagResult],
    n_complete: dict[str, int],
    p: Params,
) -> None:
    os.makedirs(p.out_dir, exist_ok=True)
    joined_csv = os.path.join(p.out_dir, "joined_divergence.csv")
    events_csv = os.path.join(p.out_dir, "decoupling_events.csv")
    summary_csv = os.path.join(p.out_dir, "per_crossing_summary.csv")
    analysis_db = os.path.join(p.out_dir, "analysis.db")

    # joined CSV
    with open(joined_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "crossing", "bucket_utc", "vehicle_class",
            "physical_direction", "virtual_direction",
            "phys_wait_min_mean", "phys_min", "phys_max",
            "virt_metric", "virt_mean", "virt_min", "virt_max",
            "complete", "phys_rank", "virt_rank", "phys_z", "virt_z",
            "divergence_rank", "divergence_z",
            "phys_elevated", "virt_elevated", "quadrant",
        ])
        for r in rows:
            w.writerow([
                r.crossing, r.bucket.strftime(TS_FMT), VEHICLE_CLASS,
                PHYSICAL_DIRECTION, VIRTUAL_DIRECTION,
                _fmt(r.phys_mean), _fmt(r.phys_min), _fmt(r.phys_max),
                p.virtual_metric, _fmt(r.virt_mean), _fmt(r.virt_min), _fmt(r.virt_max),
                int(r.complete), _fmt(r.phys_rank), _fmt(r.virt_rank),
                _fmt(r.phys_z), _fmt(r.virt_z),
                _fmt(r.divergence), _fmt(r.divergence_z),
                _fmt_bool(r.phys_elevated), _fmt_bool(r.virt_elevated), r.quadrant or "",
            ])

    # events CSV (ranked)
    with open(events_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "rank", "crossing", "direction_note", "start_utc", "end_utc",
            "duration_h", "n_buckets", "peak_abs_divergence", "mean_divergence",
            "elevated_side", "dominant_quadrant", "score_peakxduration",
        ])
        for i, e in enumerate(events, 1):
            w.writerow([
                i, e.crossing, e.direction_note, e.start.strftime(TS_FMT),
                e.end.strftime(TS_FMT), f"{e.duration_h:.2f}", e.n_buckets,
                f"{e.peak_abs_divergence:.4f}", f"{e.mean_divergence:.4f}",
                e.elevated_side, e.dominant_quadrant, f"{e.score:.4f}",
            ])

    # per-crossing summary
    quad_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r.quadrant:
            quad_counts[r.crossing][r.quadrant] += 1
    lag_by_crossing = {l.crossing: l for l in lags}
    event_counts: dict[str, int] = defaultdict(int)
    for e in events:
        event_counts[e.crossing] += 1

    with open(summary_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "crossing", "n_complete_buckets", "sufficient_for_stats",
            "baseline_corr_lag0", "peak_corr", "peak_lag_h", "n_pairs_at_peak",
            "lag_note", "n_events",
            "q_BOTH_ELEVATED", "q_PHYSICAL_HIGH", "q_VIRTUAL_HIGH", "q_BOTH_NORMAL",
        ])
        all_crossings = sorted(set(n_complete) | set(lag_by_crossing))
        for c in all_crossings:
            lr = lag_by_crossing.get(c)
            qc = quad_counts.get(c, {})
            w.writerow([
                c, n_complete.get(c, 0),
                int(n_complete.get(c, 0) >= p.min_buckets),
                _fmt(lr.baseline_corr) if lr else "",
                _fmt(lr.peak_corr) if lr else "",
                _fmt(lr.peak_lag_h) if lr else "",
                lr.n_pairs_at_peak if lr else 0,
                lr.note if lr else "",
                event_counts.get(c, 0),
                qc.get("BOTH_ELEVATED", 0),
                qc.get("PHYSICAL_HIGH_VIRTUAL_NORMAL", 0),
                qc.get("VIRTUAL_HIGH_PHYSICAL_NORMAL", 0),
                qc.get("BOTH_NORMAL", 0),
            ])

    # analysis SQLite (joined table) — separate db, never the loggers' files
    if os.path.exists(analysis_db):
        os.remove(analysis_db)
    with sqlite3.connect(analysis_db) as conn:
        conn.execute(
            "CREATE TABLE joined_divergence ("
            "crossing TEXT, bucket_utc TEXT, vehicle_class TEXT,"
            "physical_direction TEXT, virtual_direction TEXT,"
            "phys_wait_min_mean REAL, virt_metric TEXT, virt_mean REAL,"
            "complete INTEGER, phys_rank REAL, virt_rank REAL,"
            "divergence_rank REAL, quadrant TEXT)"
        )
        conn.executemany(
            "INSERT INTO joined_divergence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    r.crossing, r.bucket.strftime(TS_FMT), VEHICLE_CLASS,
                    PHYSICAL_DIRECTION, VIRTUAL_DIRECTION,
                    r.phys_mean, p.virtual_metric, r.virt_mean,
                    int(r.complete), r.phys_rank, r.virt_rank,
                    r.divergence, r.quadrant,
                )
                for r in rows
            ],
        )

    print(f"  wrote {joined_csv}")
    print(f"  wrote {events_csv}")
    print(f"  wrote {summary_csv}")
    print(f"  wrote {analysis_db}")

    if p.make_charts:
        _maybe_charts(rows, events, p)


def _maybe_charts(rows: list[JoinedRow], events: list[Event], p: Params) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("  [charts skipped: matplotlib not installed]")
        return
    by_crossing: dict[str, list[JoinedRow]] = defaultdict(list)
    for r in rows:
        if r.complete and r.phys_rank is not None:
            by_crossing[r.crossing].append(r)
    for crossing, crows in by_crossing.items():
        crows.sort(key=lambda r: r.bucket)
        xs = [r.bucket for r in crows]
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(xs, [r.phys_rank for r in crows], label="physical (PL→UA) rank")
        ax.plot(xs, [r.virt_rank for r in crows], label="virtual (UA→PL) rank")
        for e in events:
            if e.crossing == crossing:
                ax.axvspan(e.start, e.end, alpha=0.2, color="red")
        ax.set_title(f"{crossing}: normalised physical vs virtual (decoupling shaded)")
        ax.legend()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %Hh"))
        fig.autofmt_xdate()
        path = os.path.join(p.out_dir, f"chart_{crossing}.png")
        fig.savefig(path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        print(f"  wrote {path}")


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _fmt_bool(v) -> str:
    return "" if v is None else int(v)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(p: Params) -> None:
    print("PL-physical vs UA-virtual join & divergence (READ-ONLY)")
    print(f"  ⚠ directional-asymmetry comparison: {PHYSICAL_DIRECTION} physical "
          f"vs {VIRTUAL_DIRECTION} virtual (see METHODOLOGY.md)")
    print(f"  bucket={p.bucket_hours}h  elevated=p{p.elevated_pct}  "
          f"decouple=p{p.decouple_pct}  min_buckets={p.min_buckets}  "
          f"virtual_metric={p.virtual_metric}  truck_wait_agg={p.truck_wait_agg}")

    physical = load_physical(p)
    virtual = load_virtual(p)
    print(f"\nStep 1 — resampled to {p.bucket_hours}h grid:")
    print(f"  physical cells: {len(physical)}  |  virtual cells: {len(virtual)}")

    rows = build_joined(physical, virtual)
    n_total = len(rows)
    n_complete_rows = sum(1 for r in rows if r.complete)
    print(f"  joined rows: {n_total}  ({n_complete_rows} complete, "
          f"{n_total - n_complete_rows} incomplete/excluded)")

    n_complete = normalise_and_classify(rows, p)
    print("\nStep 2-3 — per-crossing normalisation + quadrant:")
    sufficient = [c for c, n in n_complete.items() if n >= p.min_buckets]
    insufficient = [(c, n) for c, n in n_complete.items() if n < p.min_buckets]
    if sufficient:
        print(f"  crossings with >= {p.min_buckets} complete buckets: {sorted(sufficient)}")
    if insufficient:
        print(f"  ⚠ INSUFFICIENT DATA (steps 2-5 skipped): "
              f"{sorted((c, n) for c, n in insufficient)}")
    if not sufficient:
        print("  → No crossing has enough overlapping observations yet. "
              "The loggers need to accumulate more (eCherga just went live; "
              "granica polls every 3h). Divergence/events/lag are not computable; "
              "the joined table is written for inspection but carries no statistics.")

    events = find_events(rows, p, n_complete)
    print(f"\nStep 4 — decoupling events ranked: {len(events)}")
    for e in events[:10]:
        print(f"  {e.crossing:12} {e.start.strftime(TS_FMT)}→{e.end.strftime(TS_FMT)} "
              f"dur={e.duration_h:.1f}h peak|div|={e.peak_abs_divergence:.2f} "
              f"{e.elevated_side} {e.dominant_quadrant}")

    lags = lead_lag(rows, p, n_complete)
    print("\nStep 5 — lead/lag (NEGATIVE lag = virtual leads physical; positive = physical leads):")
    for l in lags:
        if l.peak_corr is None:
            print(f"  {l.crossing:12} {l.note}")
        else:
            print(f"  {l.crossing:12} baseline(lag0)={_fmt(l.baseline_corr)} "
                  f"peak={_fmt(l.peak_corr)}@{_fmt(l.peak_lag_h)}h "
                  f"(n={l.n_pairs_at_peak}) {l.note}")

    print("\nStep 6 — outputs:")
    write_outputs(rows, events, lags, n_complete, p)
    print("\nDone. Loggers' databases were opened read-only and not modified.")


def parse_args(argv=None) -> Params:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queues-db", default="data/queues.db")
    ap.add_argument("--echerha-db", default="data/echerha.db")
    ap.add_argument("--out-dir", default="analysis/output")
    ap.add_argument("--bucket-hours", type=float, default=1.0)
    ap.add_argument("--elevated-pct", type=float, default=75.0)
    ap.add_argument("--decouple-pct", type=float, default=90.0)
    ap.add_argument("--min-buckets", type=int, default=24)
    ap.add_argument("--lag-hours", type=int, default=24)
    ap.add_argument("--virtual-metric", choices=["virtual_wait_s", "vehicles_waiting"], default="virtual_wait_s")
    ap.add_argument("--truck-wait-agg", choices=["max", "mean"], default="max")
    ap.add_argument("--window-start", default=None)
    ap.add_argument("--window-end", default=None)
    ap.add_argument("--charts", action="store_true")
    a = ap.parse_args(argv)
    return Params(
        queues_db=a.queues_db, echerha_db=a.echerha_db, out_dir=a.out_dir,
        bucket_hours=a.bucket_hours, elevated_pct=a.elevated_pct,
        decouple_pct=a.decouple_pct, min_buckets=a.min_buckets, lag_hours=a.lag_hours,
        virtual_metric=a.virtual_metric, truck_wait_agg=a.truck_wait_agg,
        window_start=a.window_start, window_end=a.window_end, make_charts=a.charts,
    )


if __name__ == "__main__":
    run(parse_args())
