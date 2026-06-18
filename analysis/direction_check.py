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


def load_granica(crossing: str, h: float) -> dict:
    """granica PL->UA outbound physical truck wait (minutes) per bucket — the
    OPPOSITE direction to DPSU. Mean of readings in each bucket."""
    cells = defaultdict(list)
    with _ro("data/queues.db") as c:
        for r in c.execute(
            "SELECT scraped_at, trucks_exit_min FROM queue_records "
            "WHERE crossing_id=? AND trucks_exit_min IS NOT NULL", (crossing,)
        ):
            cells[_bucket(r["scraped_at"], h)].append(r["trucks_exit_min"])
    return {b: statistics.fmean(v) for b, v in cells.items()}


def _corr(a: dict, b: dict):
    """Pearson over the buckets a and b share; (corr, n_overlap)."""
    common = sorted(set(a) & set(b))
    if len(common) < 3:
        return None, len(common)
    return pearson([a[k] for k in common], [b[k] for k in common]), len(common)


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
    a = ap.parse_args()
    h = a.bucket_hours

    print("DPSU direction validation (READ-ONLY)")
    print(f"  bucket={h}h  C=DPSU(UA->PL phys)  B=eCherga(UA->PL virt)  "
          f"A=granica(PL->UA phys)")
    print(f"  expectation: r(C,B) positive AND >= |r(C,A)|; large DPSU truck "
          f"counts corroborate the westbound (UA->PL) backlog.\n")

    hdr = (f"{'crossing':<12} {'med_trucks':>10} {'r(C,B) same':>12} {'n':>4} "
           f"{'r(C,A) opp':>12} {'n':>4}  verdict")
    print(hdr)
    print("-" * len(hdr))

    flagged, supported, thin = [], [], []
    for crossing in sorted(CANONICAL_NAMES):
        dpsu = load_dpsu(crossing, h)
        ech = load_echerha(crossing, h)
        gran = load_granica(crossing, h)

        med = statistics.median(dpsu.values()) if dpsu else float("nan")
        r_cb, n_cb = _corr(dpsu, ech)
        r_ca, n_ca = _corr(dpsu, gran)
        verdict = _verdict(r_cb, r_ca)

        if verdict.startswith("⚠"):
            flagged.append(crossing)
        elif verdict.startswith("direction SUPPORTED"):
            supported.append(crossing)
        else:
            thin.append(crossing)

        def f(x):
            return "   n/a" if x is None else f"{x:>+.3f}"

        print(f"{crossing:<12} {med:>10.0f} {f(r_cb):>12} {n_cb:>4} "
              f"{f(r_ca):>12} {n_ca:>4}  {verdict}")

    print()
    print("Magnitude sanity: a westbound (UA->PL) exit backlog reaches thousands "
          "of trucks (recon: ~2,251 at Dorohusk). Large med_trucks corroborates "
          "the direction; small counts everywhere would be a warning.")
    print(f"\nSummary: {len(supported)} supported, {len(flagged)} FLAGGED, "
          f"{len(thin)} too thin to judge.")
    if flagged:
        print(f"  ⚠ investigate (possible wrong direction): {sorted(flagged)}")
    print("\nNote: correlations are thin while the loggers' overlap is short. "
          "Re-run as data accumulates before drawing conclusions.")


if __name__ == "__main__":
    main()
