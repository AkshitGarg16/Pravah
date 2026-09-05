#!/usr/bin/env python3
"""CLI: runs the PRAVAH digital twin over TraCI and prints a live edge-speed summary.

Connects to SUMO headless (no sumo-gui -- this build has no Fox/X toolkit),
steps the simulation to completion, and periodically samples
traci.edge.getLastStepMeanSpeed() per edge -- the same quantity as the CSV's
`average_speed` column -- so later work can compare simulated vs. real
speeds on a like-for-like basis. No control logic runs here yet: signals
use the static timing netconvert generated. This is the wiring point where
Max-Pressure / GNN-RL control gets added in a later pass.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("SUMO_HOME", ""), "tools"))
import traci
import sumolib


def run(sumocfg, duration, sample_interval):
    sumo_binary = sumolib.checkBinary("sumo")
    traci.start([sumo_binary, "-c", str(sumocfg), "--end", str(duration)])

    edge_ids = [e for e in traci.edge.getIDList() if not e.startswith(":")]  # skip internal junction edges
    speed_sums = {e: 0.0 for e in edge_ids}
    speed_samples = {e: 0 for e in edge_ids}

    step = 0
    while traci.simulation.getMinExpectedNumber() > 0 and step < duration:
        traci.simulationStep()
        if step % sample_interval == 0:
            for e in edge_ids:
                v = traci.edge.getLastStepMeanSpeed(e)
                if v >= 0:  # -1 means no vehicle was on the edge this step
                    speed_sums[e] += v
                    speed_samples[e] += 1
        step += 1

    traci.close()

    return {e: speed_sums[e] / speed_samples[e] for e in edge_ids if speed_samples[e] > 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sumocfg", required=True)
    parser.add_argument("--duration", type=int, default=3600)
    parser.add_argument("--sample-interval", type=int, default=10, help="seconds between edge-speed samples")
    args = parser.parse_args()

    speeds = run(args.sumocfg, args.duration, args.sample_interval)

    if not speeds:
        print("No edges recorded any traffic during the run.")
        return

    values = list(speeds.values())
    print(f"Edges with traffic: {len(speeds)}")
    print(f"Mean speed across active edges: {sum(values) / len(values):.2f} m/s")
    print(f"Min: {min(values):.2f} m/s, Max: {max(values):.2f} m/s")

    slowest = sorted(speeds.items(), key=lambda kv: kv[1])[:5]
    print("Slowest 5 edges:")
    for eid, v in slowest:
        print(f"  {eid}: {v:.2f} m/s")


if __name__ == "__main__":
    main()
