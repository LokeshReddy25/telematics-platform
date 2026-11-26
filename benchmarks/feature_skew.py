"""
benchmarks/feature_skew.py
--------------------------
Compares the per-trip features the LIVE pipeline feeds the model (computed
by ml/risk_scorer._aggregate_features over whole simulated trips from
simulator/trip_generator.py) with the synthetic TRAINING distributions in
ml/train_model.py, and reports the risk-category mix the model produces for
each simulated driver profile.

Note: in the real stream, Spark splits a trip across 60 s event-time windows,
so live windows are often SHORTER than the whole trips scored here.

Usage: python benchmarks/feature_skew.py --drivers 300 --trips 4 --seed 7
"""

import argparse
import json
import os
import random
import statistics
import sys
from collections import Counter, defaultdict

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "simulator"))
sys.path.insert(0, os.path.join(ROOT, "ml"))

import trip_generator  # noqa: E402
import risk_scorer  # noqa: E402
from train_model import FEATURE_COLUMNS, PROFILE_FEATURE_DISTRIBUTIONS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drivers", type=int, default=300)
    ap.add_argument("--trips", type=int, default=4)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    random.seed(args.seed)

    trips = defaultdict(list)
    for ev in trip_generator.stream_events(args.drivers, args.trips):
        trips[ev["trip_id"]].append(ev)

    feats = defaultdict(lambda: defaultdict(list))
    cats = defaultdict(Counter)
    scores = defaultdict(list)
    for evs in trips.values():
        profile = evs[0]["driver_profile"]
        f = risk_scorer._aggregate_features(evs)
        for c in FEATURE_COLUMNS:
            feats[profile][c].append(f[c])
        s = risk_scorer.score_trip(evs)
        cats[profile][s["risk_category"]] += 1
        scores[profile].append(s["risk_score"])

    out = {"trips_scored": len(trips), "per_profile": {}}
    for p in ("safe", "moderate", "aggressive"):
        if p not in feats:
            continue
        out["per_profile"][p] = {
            "n_trips": len(scores[p]),
            "simulator_feature_mean": {c: round(statistics.mean(feats[p][c]), 2) for c in FEATURE_COLUMNS},
            "training_feature_mean": {c: PROFILE_FEATURE_DISTRIBUTIONS[p][c][0] for c in FEATURE_COLUMNS},
            "risk_score_median": round(statistics.median(scores[p]), 2),
            "risk_category_counts": dict(cats[p]),
        }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
