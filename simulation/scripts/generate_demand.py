#!/usr/bin/env python3
"""CLI: SUMO .net.xml -> randomTrips.py -> validated .rou.xml.

Usage:
    python scripts/generate_demand.py \
        --net sumo/network/pravah.net.xml \
        --out sumo/routes/pravah.rou.xml \
        --duration 3600
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pravah.sumo_twin.demand import generate_routes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--net", required=True, help="input .net.xml")
    parser.add_argument("--out", required=True, help="output .rou.xml path")
    parser.add_argument("--duration", type=int, default=3600, help="simulated seconds of demand (default 3600, matching the CSV's 09:00-10:00 window)")
    parser.add_argument("--period", type=float, default=3.0, help="seconds between vehicle departures (smaller = more traffic)")
    parser.add_argument("--speed-exponent", type=float, default=2.0, help="bias toward higher-speed/arterial edges")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    generate_routes(
        net_path=args.net,
        route_path=args.out,
        duration=args.duration,
        period=args.period,
        speed_exponent=args.speed_exponent,
        seed=args.seed,
    )
    print(f"Wrote routes: {args.out}")


if __name__ == "__main__":
    main()
