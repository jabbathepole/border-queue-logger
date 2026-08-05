"""Direction validation (read-only diagnostic): is the DPSU feed really UA->PL?

Co-movement of DPSU (C) with eCherga (B) alone CANNOT prove direction — a bad
border day pushes both directions up together, so C would track B even if C were
secretly the PL->UA flow. The discriminator is the OPPOSITE direction: granica
(A) publishes the PL->UA outbound physical wait at the same crossing. If DPSU
tracks granica (opposite) *more tightly* than it tracks eCherga (same), the feed
may be pointing the wrong way.

Reports, per crossing:
  1. same-direction corr   r(C, B) = DPSU physical vs eCherga virtual (UA->PL)
  2. opposite-direction corr r(C, A) = DPSU physical vs granica physical (PL->UA)
  3. verdict: flag if |r(C,A)| > |r(C,B)| (tracks the opposite direction tighter)
  4. magnitude sanity: typical DPSU trucks_waiting — the recon's ~2,251 at
     Dorohusk is the known WESTBOUND (UA->PL) exit backlog; into-Ukraine queues
     don't reach that scale, so large counts corroborate direction.

Correlations may be thin or absent early (the loggers only recently began
overlapping); the script prints what it can and says so where it can't. It opens
all three loggers' SQLite files READ-ONLY and writes nothing.

Reuses the bucketing + Pearson + eCherga/DPSU loaders from
tests/dpsu_echerha_comovement.py; this script adds the granica leg and the verdict.

Usage:
    python -m analysis.direction_check [--bucket-hours 1]
Run where data/{dpsu,echerha,queues}.db live (e.g. a checkout with data present).
"""
import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crossings import CANONICAL_NAMES
from tests.dpsu_echerha_comovement import _bucket, _ro, load_dpsu, load_echerha, pearson

DEFAULT_WINDOW_START = "2026-06-27T00:00:00Z"  # clean window floor (post-INC-003 blackout)


def load_granica(crossing: str, h: float, window_start: str | None = None) -> dict:
    """granica PL->UA outbound physical truck wait (minutes) per bucket — the
    OPPOSITE direction to DPSU. Mean of readings in each bucket."""
    cells = defaultdict(list)
    with _ro("data/queues.db") as c:
        for r in c.execute(
            "SELECT scraped_at, trucks_exit_min FROM queue_records "
            "WHERE crossing_id=? AND trucks_exit_min IS NOT NULL", (crossing,)
        ):
            if window_start and r["scraped_at"] < window_start:
                continue
            cells[_bucket(r["scraped_at"], h)].append(r["trucks_exit_min"])
    return {b: statistics.fmean(v) for b, v in cells.items()}


def _corr(a: dict, b: dict):
    """Pearson over the buckets a and b share; (corr, n_overlap)."""
    common = sorted(set(a) & set(b))
    if len(common) < 3:
        return None, len(common)
    return pearson([a[k] for k in common], [b[k] for k in common]), len(common)


def _corr_diff(a: dict, b: dict):
    """Pearson of FIRST DIFFERENCES over the buckets a and b share (sorted).

    Levels correlations on highly persistent (autocorrelated) queue series
    overstate evidential weight — two slow-moving series drift together almost
    by construction. The differenced correlation asks the harder question: do the
    hour-to-hour *changes* move together? It is the version of the corroboration
    claim that survives the persistence objection. Returns (corr, n_diff_pairs)."""
    common = sorted(set(a) & set(b))
    if len(common) < 4:
        return None, max(0, len(common) - 1)
    da = [a[common[i]] - a[common[i - 1]] for i in range(1, len(common))]
    db = [b[common[i]] - b[common[i - 1]] for i in range(1, len(common))]
    return pearson(da, db), len(da)


def _verdict(r_cb, r_ca) -> str:
    if r_cb is None and r_ca is None:
        return "insufficient overlap on both legs — cannot judge yet"
    if r_ca is None:
        return "no opposite-direction (granica) overlap yet — nothing to contradict"
    if r_cb is None:
        return "no same-direction (eCherga) overlap yet — inconclusive"
    if abs(r_ca) > abs(r_cb):
        return ("⚠ FLAG: tracks granica (OPPOSITE direction) more tightly than "
                "eCherga (same) — DPSU direction is SUSPECT")
    return "direction SUPPORTED: tracks eCherga (same) at least as tightly as granica"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket-hours", type=float, default=1.0)
    ap.add_argument("--window-start", default=DEFAULT_WINDOW_START,
                    help=f"ISO floor (default {DEFAULT_WINDOW_START}, the post-INC-003 "
                         "clean window; pass '' for full history incl. the blackout stub)")
    a = ap.parse_args()
    h = a.bucket_hours
    ws = a.window_start or None

    print("DPSU direction validation (READ-ONLY)")
    print(f"  bucket={h}h  window-start={ws or 'ALL'}  C=DPSU(UA->PL phys, native)  "
          f"B=eCherga(UA->PL virt, paused INCLUDED)  A=granica(PL->UA phys)")
    print(f"  expectation: r(C,B) positive AND >= |r(C,A)|; large DPSU truck "
          f"counts corroborate the westbound (UA->PL) backlog.")
    print(f"  r_lvl = levels; r_diff = first differences (survives the persistence "
          f"objection — see docstring / METHODOLOGY).\n")

    hdr = (f"{'crossing':<12} {'med_trk':>8} {'r(C,B)lvl':>10} {'r(C,B)dif':>10} "
           f"{'n':>4} {'r(C,A)lvl':>10} {'r(C,A)dif':>10} {'n':>4}  verdict")
    print(hdr)
    print("-" * len(hdr))

    flagged, supported, thin = [], [], []
    for crossing in sorted(CANONICAL_NAMES):
        # C-vs-B is a COUNT comparison -> paused sub-queues INCLUDED (see loader).
        dpsu = load_dpsu(crossing, h, window_start=ws)
        ech = load_echerha(crossing, h, window_start=ws, include_paused=True)
        gran = load_granica(crossing, h, window_start=ws)

        med = statistics.median(dpsu.values()) if dpsu else float("nan")
        r_cb, n_cb = _corr(dpsu, ech)
        rd_cb, _ = _corr_diff(dpsu, ech)
        r_ca, n_ca = _corr(dpsu, gran)
        rd_ca, _ = _corr_diff(dpsu, gran)
        verdict = _verdict(r_cb, r_ca)

        if verdict.startswith("⚠"):
            flagged.append(crossing)
        elif verdict.startswith("direction SUPPORTED"):
            supported.append(crossing)
        else:
            thin.append(crossing)

        def f(x):
            return "     n/a" if x is None else f"{x:>+.3f}"

        print(f"{crossing:<12} {med:>8.0f} {f(r_cb):>10} {f(rd_cb):>10} {n_cb:>4} "
              f"{f(r_ca):>10} {f(rd_ca):>10} {n_ca:>4}  {verdict}")

    print()
    print("Magnitude sanity: a westbound (UA->PL) exit backlog reaches thousands "
          "of trucks (recon: ~2,251 at Dorohusk). Large med_trk corroborates "
          "the direction; small counts everywhere would be a warning.")
    print(f"\nSummary: {len(supported)} supported, {len(flagged)} FLAGGED, "
          f"{len(thin)} too thin to judge.")
    if flagged:
        print(f"  ⚠ investigate (possible wrong direction): {sorted(flagged)}")
    print("\nNote: levels correlations on persistent series overstate evidence; "
          "the r_diff column is the version that survives that objection.")


if __name__ == "__main__":
    main()
