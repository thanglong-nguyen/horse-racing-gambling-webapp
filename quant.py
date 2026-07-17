"""
quant.py — calibrate the bookmaker against the real race engine.

Runs N full Triomphe simulations with production-identical horse
generation, then reports:

  1. Win rate by lane        -> is the inner-lane bias real? how big?
                                (sets LANE_WEIGHT empirically)
  2. Estimator calibration   -> bucket horses by estimate_strengths'
                                predicted p, compare predicted vs actual
                                win rate. Well-calibrated = the 20%
                                bucket wins ~20% of the time.
  3. Favorite hit rate       -> how often the estimator's top pick wins.

Run from the project root (same place you launch uvicorn):
    python quant.py            # default 300 races
    python quant.py 1000       # more races, tighter numbers
"""
import random
import sys
from collections import defaultdict

from api import (make_track, generate_random_horses, seeded_run,
                 estimate_strengths, NUM_LANES)

# N_RACES = int(sys.argv[1]) if len(sys.argv) > 1 else 300
N_RACES = 1000

print(f"Building track...")
track, _ = make_track()

lane_wins = defaultdict(int)
lane_runs = defaultdict(int)

# Calibration buckets: predicted p rounded to nearest 5%
bucket_predicted = defaultdict(float)   # sum of predicted p
bucket_wins = defaultdict(int)
bucket_count = defaultdict(int)

favorite_hits = 0

print(f"Simulating {N_RACES} races (this takes a while — the real engine runs)...")
for i in range(N_RACES):
    rng = random.Random(10_000 + i)          # reproducible fields
    horses_with_lanes = generate_random_horses(rng)

    rows = [
        {
            "lane": lane,
            "base_speed": h.base_speed,
            "stamina": h.stamina,
            "loss_rate": h.stamina_loss_per_meter,
        }
        for h, lane in horses_with_lanes
    ]
    strengths = estimate_strengths(rows)

    result = seeded_run(horses_with_lanes, track)
    winning_lane = result["results"][0]["lane"]

    lane_wins[winning_lane] += 1
    for _, lane in horses_with_lanes:
        lane_runs[lane] += 1

    for lane, p in strengths.items():
        bucket = round(p * 20) / 20          # 5% buckets
        bucket_predicted[bucket] += p
        bucket_count[bucket] += 1
        if lane == winning_lane:
            bucket_wins[bucket] += 1

    if winning_lane == max(strengths, key=strengths.get):
        favorite_hits += 1

    if (i + 1) % 25 == 0:
        print(f"  {i + 1}/{N_RACES} done")

print(f"\n=== 1. Win rate by lane (uniform would be {1 / NUM_LANES:.1%}) ===")
for lane in range(NUM_LANES):
    runs = lane_runs[lane] or 1
    rate = lane_wins[lane] / runs
    bar = "#" * round(rate * 100)
    print(f"  lane {lane}: {lane_wins[lane]:4d}/{runs} = {rate:6.1%}  {bar}")
print("  -> If this slopes down from lane 0, inner bias is real.")
print("  -> Tune LANE_WEIGHT until estimate_strengths' per-lane averages")
print("     match this slope, then re-run to confirm.")

print(f"\n=== 2. Estimator calibration (predicted vs actual win rate) ===")
print(f"  {'predicted':>10}  {'actual':>8}  {'n':>5}")
for bucket in sorted(bucket_count):
    n = bucket_count[bucket]
    avg_pred = bucket_predicted[bucket] / n
    actual = bucket_wins[bucket] / n
    flag = ""
    if n >= 20 and abs(actual - avg_pred) > 0.05:
        flag = "  <-- off by >5pts"
    print(f"  {avg_pred:>9.1%}  {actual:>7.1%}  {n:>5}{flag}")
print("  -> Rows flagged 'off' are where sharp bettors beat the house.")
print("  -> Actual consistently sharper than predicted: raise k.")
print("     Actual flatter than predicted: lower k.")

print(f"\n=== 3. Favorite hit rate ===")
print(f"  estimator's top pick won {favorite_hits}/{N_RACES} "
      f"= {favorite_hits / N_RACES:.1%}")
