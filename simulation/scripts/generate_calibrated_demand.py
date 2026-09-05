#!/usr/bin/env python3
"""CLI: TomTom-calibrated demand -> validated .rou.xml, via
pravah.sumo_twin.demand.generate_calibrated_routes(). Previously this
function was only reachable through an inline `python3 -c` invocation --
this closes that gap.

Usage (whole-network, honest calibration):
    python scripts/generate_calibrated_demand.py \
        --net sumo/network/pravah.net.xml \
        --segment-map sumo/network/pravah.net.segment_map.json \
        --csv data/pravah_900to1000_balanced.csv \
        --out sumo/routes/pravah_calibrated.rou.xml

Usage (ITO-focused dense demand, for the "before/after PRAVAH" demo):
    python scripts/generate_calibrated_demand.py \
        --net sumo/network/pravah.net.xml \
        --segment-map sumo/network/pravah.net.segment_map.json \
        --csv data/pravah_900to1000_balanced.csv \
        --out sumo/routes/pravah_calibrated_dense.rou.xml \
        --hotspot-edges 1285520201747431424,1285520202073505792,1285520202709368832,\
1285520202504470528,1285520202525605888,1285520202575904768 \
        --hotspot-multiplier 5

Usage (graduated demand, distinctly different volume per ITO approach,
for the queue-based-controller demo):
    python scripts/generate_calibrated_demand.py \
        --net sumo/network/pravah.net.xml \
        --segment-map sumo/network/pravah.net.segment_map.json \
        --csv data/pravah_900to1000_balanced.csv \
        --out sumo/routes/pravah_calibrated_graduated.rou.xml \
        --hotspot-multipliers 1285520201747431424:10,1285520202073505792:4,1285520202709368832:1

Usage (a demand PULSE on one edge -- quiet, then a sharp surge, then quiet
again, for demonstrating a controller actually clearing a backlog rather
than just running permanently oversaturated -- pipe-separate a schedule,
one multiplier per --interval-sized chunk):
    python scripts/generate_calibrated_demand.py \
        --net sumo/network/pravah.net.xml \
        --segment-map sumo/network/pravah.net.segment_map.json \
        --csv data/pravah_900to1000_balanced.csv \
        --out sumo/routes/pravah_calibrated_pulse.rou.xml \
        --duration 300 --interval 30 \
        --hotspot-multipliers "1285520201747431424:1|1|8|8|8|1|1|1|1|1"
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pravah.sumo_twin.demand import generate_calibrated_routes


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--net", required=True)
    parser.add_argument("--segment-map", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", required=True, help="output .rou.xml path")
    parser.add_argument("--duration", type=int, default=3600, help="simulated seconds of demand to generate")
    parser.add_argument("--interval", type=int, default=300, help="piecewise-constant chunk size, seconds")
    parser.add_argument("--target-vph", type=float, default=1200,
                         help="target network-wide vehicles/hour, shared out per edge by TomTom sample_size")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--hotspot-edges", default=None,
        help="comma-separated edge ids to artificially stress (e.g. a junction's approach/exit edges) -- "
             "see generate_calibrated_routes()'s docstring for exactly how this is applied.",
    )
    parser.add_argument("--hotspot-multiplier", type=float, default=1.0,
                         help="multiplier applied to --hotspot-edges' demand; 1.0 (default) is a no-op")
    parser.add_argument(
        "--hotspot-multipliers", default=None,
        help="comma-separated edge_id:multiplier pairs for giving several edges distinctly different "
             "multipliers in one run, e.g. '1285520201747431424:10,1285520202073505792:4'. A multiplier can "
             "also be a pipe-separated schedule, one value per --interval-sized chunk (e.g. "
             "'edge_id:1|1|8|8|8|1|1|1|1|1' for a quiet/surge/quiet demand pulse) -- see "
             "generate_calibrated_routes()'s docstring for exactly how this combines with --hotspot-edges.",
    )
    args = parser.parse_args()

    hotspot_edges = set(args.hotspot_edges.split(",")) if args.hotspot_edges else None
    hotspot_multipliers = None
    if args.hotspot_multipliers:
        hotspot_multipliers = {}
        for pair in args.hotspot_multipliers.split(","):
            edge_id, mult = pair.split(":")
            hotspot_multipliers[edge_id] = [float(m) for m in mult.split("|")] if "|" in mult else float(mult)

    out = generate_calibrated_routes(
        net_path=args.net, segment_map_path=args.segment_map, csv_path=args.csv, route_path=args.out,
        duration=args.duration, interval=args.interval, target_network_vehicles_per_hour=args.target_vph,
        seed=args.seed, hotspot_edges=hotspot_edges, hotspot_multiplier=args.hotspot_multiplier,
        hotspot_multipliers=hotspot_multipliers,
    )
    print(f"Wrote calibrated routes: {out}")


if __name__ == "__main__":
    main()
