#!/usr/bin/env python3
"""Runs the same demand through FixedTimeController (baseline) and
MaxPressureController against PravahEnv, and saves both runs' metrics +
vehicle trajectories to one JSON file for comparison/visualization.

Same seed for both runs -- same demand, same network, same metric
collection code path -- so any difference in the outcome is attributable
to the controller, not to an incidental difference in how the two runs
were driven.

Usage:
    python scripts/compare_control.py \
        --sumocfg sumo/pravah.sumocfg \
        --duration 1200 --control-interval 10 --seed 1 \
        --out sumo/video/comparison.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, os.path.join(os.environ.get("SUMO_HOME", ""), "tools"))
import traci

from pravah.sumo_twin.env import PravahEnv
from pravah.sumo_twin.controllers import FixedTimeController, MaxPressureController


def run_episode(env, controller, seed):
    obs, info = env.reset(seed=seed)
    controller.reset(obs)
    tls_ids = env.tls_ids

    metrics = []
    frames = [_snapshot(env, tls_ids)]  # include the initial state as frame 0
    done = False
    while not done:
        actions = controller.decide_actions(obs)
        obs, info, done = env.step(actions)
        metrics.append(info)
        frames.append(_snapshot(env, tls_ids))

    return metrics, frames


def _snapshot(env, tls_ids):
    vehicles = []
    for vid in traci.vehicle.getIDList():
        x, y = traci.vehicle.getPosition(vid)
        vehicles.append({"x": round(x, 1), "y": round(y, 1)})
    tls_states = {tls: traci.trafficlight.getRedYellowGreenState(tls) for tls in tls_ids}
    return {"t": round(env.t, 1), "vehicles": vehicles, "tls": tls_states}


def summarize(metrics):
    speeds = [m["mean_edge_speed"] for m in metrics]
    return {
        "mean_speed": sum(speeds) / len(speeds) if speeds else 0.0,
        "final_total_waiting_time": metrics[-1]["total_waiting_time"] if metrics else 0.0,
        "total_arrived": sum(m["arrived"] for m in metrics),
        "total_teleported": sum(m["teleported"] for m in metrics),
        "speed_series": [round(s, 2) for s in speeds],
        "waiting_series": [round(m["total_waiting_time"], 1) for m in metrics],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sumocfg", required=True)
    parser.add_argument("--duration", type=int, default=1200)
    parser.add_argument("--control-interval", type=int, default=10)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    env = PravahEnv(args.sumocfg, control_interval=args.control_interval, sim_end=args.duration)

    print("Running FixedTimeController (baseline)...")
    fixed_metrics, fixed_frames = run_episode(env, FixedTimeController(env), args.seed)

    print("Running MaxPressureController...")
    mp_metrics, mp_frames = run_episode(env, MaxPressureController(env), args.seed)

    env.close()

    fixed_summary = summarize(fixed_metrics)
    mp_summary = summarize(mp_metrics)

    print(f"\n{'metric':<24}{'fixed-time':>14}{'max-pressure':>16}{'change':>10}")
    for key, label in [("mean_speed", "mean speed (m/s)"),
                        ("final_total_waiting_time", "final waiting time (s)"),
                        ("total_arrived", "vehicles arrived"),
                        ("total_teleported", "teleports")]:
        f, m = fixed_summary[key], mp_summary[key]
        pct = f"{100 * (m - f) / f:+.1f}%" if f else "n/a"
        print(f"{label:<24}{f:>14.2f}{m:>16.2f}{pct:>10}")

    out = {
        "meta": {
            "duration": args.duration,
            "control_interval": args.control_interval,
            "seed": args.seed,
        },
        "fixed_time": {"summary": fixed_summary, "frames": fixed_frames},
        "max_pressure": {"summary": mp_summary, "frames": mp_frames},
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f)
    print(f"\nWrote comparison data: {args.out}")


if __name__ == "__main__":
    main()
