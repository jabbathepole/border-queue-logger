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
import bisect
import csv
import datetime as dt
import math
import os
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

# Fixed direction labels for the feeds (see module docstring / METHODOLOGY).
PHYSICAL_DIRECTION = "PL_to_UA_outbound"   # granica wyjazd
VIRTUAL_DIRECTION = "UA_to_PL_inbound"     # eCherga (leaving UA -> entering PL)
DPSU_DIRECTION = "UA_to_PL_physical"       # DPSU trucks queued in UA to exit to PL
VEHICLE_CLASS = "truck"                    # trucks-to-trucks only

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
@dataclass
class Params:
    queues_db: str
    echerha_db: str
    dpsu_db: str
    out_dir: str
    bucket_hours: float = 1.0
    elevated_pct: float = 75.0      # per-crossing percentile = "elevated"
    decouple_pct: float = 90.0      # per-crossing |divergence| percentile = event threshold
    min_buckets: int = 24           # min complete buckets per crossing for steps 2-5
    dpsu_max_age_hours: float = 6.0  # C-vs-B freshness cutoff: a forward-filled DPSU
                                     # reading older than this is too stale to rank
                                     # (~2x the nominal ~3h DPSU refresh; tunable)
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
# DPSU UA->PL physical truck count — sparse (~3 h) feed, forward-filled (FIX 2)
# with a per-bucket reading age (FIX 1)
# --------------------------------------------------------------------------- #
def load_dpsu(p: Params) -> dict[str, list[tuple[dt.datetime, float]]]:
    """Per crossing, the time-ordered DPSU truck readings keyed on the SOURCE's
    own update time (source_updated_utc), not our poll time. These DISTINCT native
    readings are also the percentile baseline for dpsu_rank (§2a) — one weight per
    real source update, NOT the forward-filled bucket series. Returns {} if the
    DPSU db isn't present (the join still runs without it).

    Readings are NOT window-filtered here: a reading from just before the window
    is needed to forward-fill the window's first buckets. Staleness is surfaced
    later via reading age, not by dropping early readings.

    ts_synthetic=1 rows are EXCLUDED (FIX 1.1): their source_updated_utc is our
    poll time, not a real source update, so they must not anchor a baseline or be
    smeared across buckets by forward-fill."""
    if not os.path.exists(p.dpsu_db):
        return {}
    series: dict[str, list[tuple[dt.datetime, float]]] = defaultdict(list)
    with _connect_ro(p.dpsu_db) as conn:
        # Guard for dbs written before the ts_synthetic migration (this is a
        # read-only consumer; it cannot ALTER, so it adapts to the schema it finds).
        has_synth = any(
            row["name"] == "ts_synthetic"
            for row in conn.execute("PRAGMA table_info(dpsu_records)")
        )
        synth_filter = "AND COALESCE(ts_synthetic, 0) = 0" if has_synth else ""
        rows = conn.execute(
            "SELECT crossing_id, source_updated_utc, trucks_waiting "
            f"FROM dpsu_records WHERE trucks_waiting IS NOT NULL {synth_filter}"
        ).fetchall()
    seen: set[tuple[str, str]] = set()
    for r in rows:
        key = (r["crossing_id"], r["source_updated_utc"])
        if key in seen:
            continue  # same source reading polled multiple times
        seen.add(key)
        series[r["crossing_id"]].append((_parse_ts(r["source_updated_utc"]), float(r["trucks_waiting"])))
    for c in series:
        series[c].sort(key=lambda t: t[0])
    return series


def attach_dpsu(rows: list[JoinedRow], dpsu: dict[str, list[tuple[dt.datetime, float]]]) -> int:
    """Forward-fill the most recent DPSU reading onto each joined bucket and tag
    it with its age (bucket_start - source_updated_utc). Uses bucket START as the
    reference so no future reading leaks backwards. Returns #rows filled."""
    times_by_crossing = {c: [t for t, _ in series] for c, series in dpsu.items()}
    filled = 0
    for r in rows:
        series = dpsu.get(r.crossing)
        if not series:
            continue
        idx = bisect.bisect_right(times_by_crossing[r.crossing], r.bucket) - 1
        if idx < 0:
            continue  # bucket precedes the first DPSU reading
        ts, trucks = series[idx]
        r.dpsu_trucks = trucks
        r.dpsu_src_updated_utc = ts.strftime(TS_FMT)
        r.dpsu_reading_age_s = int((r.bucket - ts).total_seconds())
        filled += 1
    return filled


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
    # DPSU UA->PL PHYSICAL truck count, forward-filled onto this bucket (FIX 2),
    # always carrying the source reading's age (FIX 1). This is the same-direction
    # physical partner to eCherga's UA->PL virtual queue. NOT folded into the
    # granica-vs-eCherga divergence above — exposed as raw columns so analysis can
    # filter on dpsu_reading_age_s and decide what staleness is acceptable.
    dpsu_trucks: float | None = None
    dpsu_src_updated_utc: str | None = None
    dpsu_reading_age_s: int | None = None
    # C-vs-B (same-direction UA->PL physical-vs-virtual) — PR 2. dpsu_rank is the
    # DPSU truck count's per-crossing percentile, baselined over DISTINCT NATIVE
    # readings (§2a) and assigned only to fresh-enough fills (age within
    # --dpsu-max-age-hours); stale fills get dpsu_rank=None, dpsu_stale=True while
    # the raw dpsu_trucks/age columns above stay unfiltered. cb_divergence_rank =
    # dpsu_rank - virt_rank; cb_quadrant labels the physical-vs-virtual 2x2.
    dpsu_rank: float | None = None
    dpsu_elevated: bool | None = None
    dpsu_stale: bool | None = None
    cb_divergence_rank: float | None = None
    cb_quadrant: str | None = None


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
# Steps 2c-3c — C-vs-B: same-direction (UA->PL) physical-vs-virtual divergence
# (DPSU physical truck count vs eCherga virtual queue). Additive; the A-vs-B
# granica-vs-eCherga divergence above is untouched.
# --------------------------------------------------------------------------- #
CB_QUADRANTS = {
    (True, True): "aligned_busy",      # both elevated
    (True, False): "physical_only",    # real trucks on the ground not in the booking queue
    (False, True): "virtual_only",     # booking queue inflated vs what's physically present
    (False, False): "aligned_quiet",   # neither elevated
}


def normalise_dpsu_and_diverge(
    rows: list[JoinedRow],
    dpsu: dict[str, list[tuple[dt.datetime, float]]],
    p: Params,
) -> dict[str, tuple[int, int]]:
    """Percentile-normalise the forward-filled DPSU truck count into `dpsu_rank`
    and compute the C-vs-B divergence on fresh-enough buckets only.

    §2a crux: the percentile baseline is each crossing's DISTINCT NATIVE readings
    (`dpsu` already holds exactly these — one weight per real source_updated_utc),
    NOT the forward-filled bucket series. We then ASSIGN those ranks to the
    fresh-enough filled buckets. This avoids a reading that precedes a long gap
    being counted dozens of times in the distribution.

    §2b staleness: a bucket whose dpsu_reading_age_s exceeds
    --dpsu-max-age-hours is excluded from dpsu_rank / C-vs-B (dpsu_rank=None,
    dpsu_stale=True). The raw dpsu_trucks / dpsu_reading_age_s columns are kept
    unfiltered for transparency.

    Sufficiency: a crossing needs >= min_buckets distinct native readings before
    its dpsu_rank is trusted (same gate philosophy as phys/virt rank), else the
    percentile distribution is degenerate.

    Returns {crossing: (n_native_readings, n_cb_buckets)} for reporting."""
    baselines = {c: sorted(v for _, v in series) for c, series in dpsu.items()}
    cutoff_s = p.dpsu_max_age_hours * 3600.0
    by_crossing: dict[str, list[JoinedRow]] = defaultdict(list)
    for r in rows:
        by_crossing[r.crossing].append(r)

    report: dict[str, tuple[int, int]] = {}
    for crossing, crows in by_crossing.items():
        native = baselines.get(crossing, [])
        n_native = len(native)
        sufficient = n_native >= p.min_buckets
        thr = _value_at_pct(native, p.elevated_pct) if sufficient else None

        n_cb = 0
        for r in crows:
            if r.dpsu_trucks is None:
                continue  # no fill on this bucket — leave normalised fields None
            stale = r.dpsu_reading_age_s is not None and r.dpsu_reading_age_s > cutoff_s
            r.dpsu_stale = stale
            if stale or not sufficient:
                continue  # raw columns kept; only the normalised/compared values gated
            r.dpsu_rank = _percentile_rank(r.dpsu_trucks, native)
            r.dpsu_elevated = r.dpsu_trucks > thr
            # C-vs-B needs eCherga's virt_rank on the same bucket (set by
            # normalise_and_classify on complete, sufficient-data buckets).
            if r.virt_rank is not None:
                r.cb_divergence_rank = r.dpsu_rank - r.virt_rank
                if r.virt_elevated is not None:
                    r.cb_quadrant = CB_QUADRANTS[(r.dpsu_elevated, r.virt_elevated)]
                n_cb += 1
        report[crossing] = (n_native, n_cb)
    return report


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


def _rank_events(
    rows: list[JoinedRow],
    p: Params,
    n_complete: dict[str, int],
    *,
    div_of,
    quad_of,
    gate,
    direction_note: str,
) -> list[Event]:
    """Generic contiguous-decoupling-run ranker shared by A-vs-B (find_events) and
    C-vs-B (find_cb_events). `div_of`/`quad_of` pluck the divergence / quadrant for
    the comparison; `gate` selects which rows are eligible. A crossing needs
    min_buckets complete buckets (both comparisons only populate their divergence
    on such crossings, so this gate never silently shrinks one of them)."""
    by_crossing: dict[str, list[JoinedRow]] = defaultdict(list)
    for r in rows:
        if gate(r):
            by_crossing[r.crossing].append(r)

    events: list[Event] = []
    for crossing, crows in by_crossing.items():
        if n_complete.get(crossing, 0) < p.min_buckets:
            continue
        crows.sort(key=lambda r: r.bucket)
        abs_divs = sorted(abs(div_of(r)) for r in crows)
        thr = _value_at_pct(abs_divs, p.decouple_pct)

        run: list[JoinedRow] = []
        contiguous_gap = dt.timedelta(hours=p.bucket_hours * 1.5)

        def flush(run: list[JoinedRow]) -> None:
            if not run:
                return
            divs = [div_of(r) for r in run]
            peak = max(abs(d) for d in divs)
            mean_d = statistics.fmean(divs)
            elevated_side = "physical" if mean_d > 0 else "virtual"
            quad_counts = defaultdict(int)
            for r in run:
                quad_counts[quad_of(r)] += 1
            dom_quad = max(quad_counts, key=quad_counts.get)
            duration_h = (run[-1].bucket - run[0].bucket).total_seconds() / 3600.0 + p.bucket_hours
            events.append(
                Event(
                    crossing=crossing,
                    direction_note=direction_note,
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
            over = abs(div_of(r)) >= thr and thr > 0
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


def find_events(rows: list[JoinedRow], p: Params, n_complete: dict[str, int]) -> list[Event]:
    """A-vs-B: granica (PL->UA outbound physical) vs eCherga (UA->PL virtual)."""
    return _rank_events(
        rows, p, n_complete,
        div_of=lambda r: r.divergence,
        quad_of=lambda r: r.quadrant,
        gate=lambda r: r.complete and r.divergence is not None,
        direction_note=f"{PHYSICAL_DIRECTION} phys vs {VIRTUAL_DIRECTION} virt",
    )


def find_cb_events(rows: list[JoinedRow], p: Params, n_complete: dict[str, int]) -> list[Event]:
    """C-vs-B: DPSU (UA->PL physical) vs eCherga (UA->PL virtual) — SAME direction.
    Only fresh-enough buckets carry cb_divergence_rank, so the run-ranking sees
    only trustworthy fills."""
    return _rank_events(
        rows, p, n_complete,
        div_of=lambda r: r.cb_divergence_rank,
        quad_of=lambda r: r.cb_quadrant or "",
        gate=lambda r: r.cb_divergence_rank is not None,
        direction_note=f"{DPSU_DIRECTION} phys vs {VIRTUAL_DIRECTION} virt (same direction)",
    )


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
    cb_events: list[Event],
    lags: list[LagResult],
    n_complete: dict[str, int],
    p: Params,
) -> None:
    os.makedirs(p.out_dir, exist_ok=True)
    joined_csv = os.path.join(p.out_dir, "joined_divergence.csv")
    events_csv = os.path.join(p.out_dir, "decoupling_events.csv")
    cb_events_csv = os.path.join(p.out_dir, "cb_decoupling_events.csv")
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
            # DPSU UA->PL physical truck count (forward-filled) + its reading age.
            "dpsu_trucks_ff", "dpsu_src_updated_utc", "dpsu_reading_age_s",
            # C-vs-B (same-direction UA->PL physical-vs-virtual) — gated by freshness.
            "dpsu_rank", "dpsu_stale", "cb_divergence_rank", "cb_quadrant",
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
                _fmt(r.dpsu_trucks), r.dpsu_src_updated_utc or "", _fmt(r.dpsu_reading_age_s),
                _fmt(r.dpsu_rank), _fmt_bool(r.dpsu_stale),
                _fmt(r.cb_divergence_rank), r.cb_quadrant or "",
            ])

    # events CSVs (ranked) — A-vs-B and C-vs-B kept SEPARATE so the two analyses
    # are never conflated.
    _write_events_csv(events_csv, events)
    _write_events_csv(cb_events_csv, cb_events)

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
        # list EVERY crossing seen in either feed so the sample size (incl. 0
        # paired buckets) is always visible — never silently omit thin crossings.
        all_crossings = sorted({r.crossing for r in rows} | set(n_complete) | set(lag_by_crossing))
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
            "divergence_rank REAL, quadrant TEXT,"
            "dpsu_trucks_ff REAL, dpsu_src_updated_utc TEXT, dpsu_reading_age_s INTEGER,"
            "dpsu_rank REAL, dpsu_stale INTEGER, cb_divergence_rank REAL, cb_quadrant TEXT)"
        )
        conn.executemany(
            "INSERT INTO joined_divergence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    r.crossing, r.bucket.strftime(TS_FMT), VEHICLE_CLASS,
                    PHYSICAL_DIRECTION, VIRTUAL_DIRECTION,
                    r.phys_mean, p.virtual_metric, r.virt_mean,
                    int(r.complete), r.phys_rank, r.virt_rank,
                    r.divergence, r.quadrant,
                    r.dpsu_trucks, r.dpsu_src_updated_utc, r.dpsu_reading_age_s,
                    r.dpsu_rank,
                    None if r.dpsu_stale is None else int(r.dpsu_stale),
                    r.cb_divergence_rank, r.cb_quadrant,
                )
                for r in rows
            ],
        )

    print(f"  wrote {joined_csv}")
    print(f"  wrote {events_csv}")
    print(f"  wrote {cb_events_csv}")
    print(f"  wrote {summary_csv}")
    print(f"  wrote {analysis_db}")

    if p.make_charts:
        _maybe_charts(rows, events, cb_events, p)


def _maybe_charts(
    rows: list[JoinedRow], events: list[Event], cb_events: list[Event], p: Params
) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("  [charts skipped: matplotlib not installed]")
        return

    # A-vs-B (granica vs eCherga, cross-direction asymmetry).
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
        ax.set_title(
            f"{crossing} — directional-asymmetry: physical {PHYSICAL_DIRECTION} "
            f"vs virtual {VIRTUAL_DIRECTION} (NOT same-flow; decoupling shaded)"
        )
        ax.legend()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %Hh"))
        fig.autofmt_xdate()
        path = os.path.join(p.out_dir, f"chart_{crossing}.png")
        fig.savefig(path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        print(f"  wrote {path}")

    # C-vs-B (DPSU vs eCherga, SAME UA->PL direction) — only fresh-enough buckets
    # carry dpsu_rank, so plot exactly those.
    cb_by_crossing: dict[str, list[JoinedRow]] = defaultdict(list)
    for r in rows:
        if r.dpsu_rank is not None and r.virt_rank is not None:
            cb_by_crossing[r.crossing].append(r)
    for crossing, crows in cb_by_crossing.items():
        crows.sort(key=lambda r: r.bucket)
        xs = [r.bucket for r in crows]
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(xs, [r.dpsu_rank for r in crows], label="DPSU physical (UA→PL) rank")
        ax.plot(xs, [r.virt_rank for r in crows], label="eCherga virtual (UA→PL) rank")
        for e in cb_events:
            if e.crossing == crossing:
                ax.axvspan(e.start, e.end, alpha=0.2, color="red")
        ax.set_title(
            f"{crossing} — same-direction {DPSU_DIRECTION}: physical (DPSU) vs "
            f"virtual (eCherga); fresh-enough buckets only, decoupling shaded"
        )
        ax.legend()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %Hh"))
        fig.autofmt_xdate()
        path = os.path.join(p.out_dir, f"chart_cb_{crossing}.png")
        fig.savefig(path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        print(f"  wrote {path}")


def _write_events_csv(path: str, events: list[Event]) -> None:
    """Ranked decoupling-events CSV. Same schema for A-vs-B and C-vs-B; the feed
    pairing is carried in each row's direction_note."""
    with open(path, "w", newline="", encoding="utf-8") as f:
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

    # DPSU UA->PL physical truck count: sparse ~3 h feed, forward-filled onto the
    # grid with a per-bucket reading age (FIXES 1 & 2). Exposed as raw columns;
    # the granica-vs-eCherga divergence above is unchanged.
    dpsu = load_dpsu(p)
    if dpsu:
        n_filled = attach_dpsu(rows, dpsu)
        ages = [r.dpsu_reading_age_s for r in rows if r.dpsu_reading_age_s is not None]
        med_age = sorted(ages)[len(ages) // 2] / 3600.0 if ages else float("nan")
        print(f"  DPSU forward-fill: {n_filled}/{n_total} buckets carry a truck "
              f"count (median reading age {med_age:.1f} h). Filter on "
              f"dpsu_reading_age_s downstream to drop stale fills.")
    else:
        print(f"  DPSU: {p.dpsu_db} not present — dpsu_* columns left empty.")

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

    # Steps 2c-3c — C-vs-B same-direction (UA->PL) physical-vs-virtual divergence.
    # DPSU truck count normalised over its DISTINCT NATIVE readings (§2a), gated by
    # the freshness cutoff (§2b), then compared to eCherga's virt_rank.
    cb_report = normalise_dpsu_and_diverge(rows, dpsu, p)
    print(f"\nStep 2c-3c — C-vs-B (same-direction {DPSU_DIRECTION} physical vs "
          f"{VIRTUAL_DIRECTION} virtual):")
    if not dpsu:
        print("  DPSU feed absent — C-vs-B not computed.")
    else:
        filled = [r for r in rows if r.dpsu_trucks is not None]
        n_filled = len(filled)
        n_stale = sum(1 for r in filled if r.dpsu_stale)
        n_fresh = n_filled - n_stale
        pct_stale = (100.0 * n_stale / n_filled) if n_filled else 0.0
        # The methodology figure: how much of the forward-filled feed the cutoff keeps.
        print(f"  freshness cutoff = {p.dpsu_max_age_hours} h: {n_fresh}/{n_filled} "
              f"filled buckets within cutoff ({pct_stale:.0f}% excluded as stale).")
        cb_sufficient = sorted(c for c, (nn, _) in cb_report.items() if nn >= p.min_buckets)
        cb_thin = sorted((c, nn) for c, (nn, _) in cb_report.items() if nn < p.min_buckets)
        if cb_sufficient:
            print(f"  crossings with >= {p.min_buckets} distinct native DPSU "
                  f"readings (dpsu_rank trusted): {cb_sufficient}")
        if cb_thin:
            print(f"  ⚠ too few native DPSU readings for a baseline (dpsu_rank "
                  f"skipped): {cb_thin}")
        n_cb_buckets = sum(1 for r in rows if r.cb_divergence_rank is not None)
        print(f"  C-vs-B divergence computed on {n_cb_buckets} buckets "
              f"(fresh DPSU rank AND eCherga rank both present).")

    events = find_events(rows, p, n_complete)
    print(f"\nStep 4 — A-vs-B decoupling events ranked: {len(events)}")
    for e in events[:10]:
        print(f"  {e.crossing:12} {e.start.strftime(TS_FMT)}→{e.end.strftime(TS_FMT)} "
              f"dur={e.duration_h:.1f}h peak|div|={e.peak_abs_divergence:.2f} "
              f"{e.elevated_side} {e.dominant_quadrant}")

    cb_events = find_cb_events(rows, p, n_complete)
    print(f"\nStep 4c — C-vs-B decoupling events ranked: {len(cb_events)}")
    for e in cb_events[:10]:
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
    write_outputs(rows, events, cb_events, lags, n_complete, p)
    print("\nDone. Loggers' databases were opened read-only and not modified.")


def parse_args(argv=None) -> Params:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queues-db", default="data/queues.db")
    ap.add_argument("--echerha-db", default="data/echerha.db")
    ap.add_argument("--dpsu-db", default="data/dpsu.db")
    ap.add_argument("--out-dir", default="analysis/output")
    ap.add_argument("--bucket-hours", type=float, default=1.0)
    ap.add_argument("--elevated-pct", type=float, default=75.0)
    ap.add_argument("--decouple-pct", type=float, default=90.0)
    ap.add_argument("--min-buckets", type=int, default=24)
    ap.add_argument("--dpsu-max-age-hours", type=float, default=6.0,
                    help="C-vs-B freshness cutoff: drop DPSU fills older than this "
                         "from dpsu_rank / C-vs-B divergence (default 6h ~ 2x refresh)")
    ap.add_argument("--lag-hours", type=int, default=24)
    ap.add_argument("--virtual-metric", choices=["virtual_wait_s", "vehicles_waiting"], default="virtual_wait_s")
    ap.add_argument("--truck-wait-agg", choices=["max", "mean"], default="max")
    ap.add_argument("--window-start", default=None)
    ap.add_argument("--window-end", default=None)
    ap.add_argument("--charts", action="store_true")
    a = ap.parse_args(argv)
    return Params(
        queues_db=a.queues_db, echerha_db=a.echerha_db, dpsu_db=a.dpsu_db, out_dir=a.out_dir,
        bucket_hours=a.bucket_hours, elevated_pct=a.elevated_pct,
        decouple_pct=a.decouple_pct, min_buckets=a.min_buckets,
        dpsu_max_age_hours=a.dpsu_max_age_hours, lag_hours=a.lag_hours,
        virtual_metric=a.virtual_metric, truck_wait_agg=a.truck_wait_agg,
        window_start=a.window_start, window_end=a.window_end, make_charts=a.charts,
    )


if __name__ == "__main__":
    run(parse_args())
