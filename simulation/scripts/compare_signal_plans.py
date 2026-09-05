#!/usr/bin/env python3
"""Runs the free-flowing and the mis-timed ITO scenarios and prints them side by side.

Both runs use the same network, the same route file and the same seed, so the
only thing that differs between the two columns is the signal programs. Any
number that moves between them moved because of signal timing.
"""

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from tune_busy_freeflow import analyse, SCRATCH  # noqa: E402


def run(cfg, seed, label):
    summary = SCRATCH / f"cmp_{label}.xml"
    stats = SCRATCH / f"cmpst_{label}.xml"
    subprocess.run(
        ["sumo", "-c", str(cfg), "--summary-output", str(summary),
         "--statistic-output", str(stats), "--seed", str(seed),
         "--no-step-log", "--no-warnings"],
        check=True, stdout=subprocess.DEVNULL, cwd=str(REPO))
    m = analyse(summary)
    root = ET.parse(stats).getroot()
    tel = root.find("teleports")
    m["teleports"] = int(tel.get("total", 0)) if tel is not None else 0
    trip = root.find("vehicleTripStatistics")
    m["waiting"] = float(trip.get("waitingTime")) if trip is not None else 0.0
    m["timeloss"] = float(trip.get("timeLoss")) if trip is not None else 0.0
    m["duration"] = float(trip.get("duration")) if trip is not None else 0.0
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--good", default=str(REPO / "sumo" / "pravah_ito_busy.sumocfg"))
    ap.add_argument("--bad", default=str(REPO / "sumo" / "pravah_ito_jam.sumocfg"))
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    g = run(args.good, args.seed, "good")
    b = run(args.bad, args.seed, "bad")

    rows = [
        ("vehicles in network (mean)", f"{g['mean_running']:.0f}", f"{b['mean_running']:.0f}"),
        ("vehicles in network (peak)", f"{g['peak_running']:.0f}", f"{b['peak_running']:.0f}"),
        ("mean speed (km/h)", f"{g['mean_speed']*3.6:.1f}", f"{b['mean_speed']*3.6:.1f}"),
        ("stopped at any instant", f"{g['halting_frac']:.1%}", f"{b['halting_frac']:.1%}"),
        ("mean wait per trip (s)", f"{g['waiting']:.1f}", f"{b['waiting']:.1f}"),
        ("mean time lost per trip (s)", f"{g['timeloss']:.1f}", f"{b['timeloss']:.1f}"),
        ("mean trip duration (s)", f"{g['duration']:.1f}", f"{b['duration']:.1f}"),
        ("teleports (gridlock events)", f"{g['teleports']}", f"{b['teleports']}"),
        ("2nd-half growth (vehicles)", f"{g['running_slope_2h']:+.0f}", f"{b['running_slope_2h']:+.0f}"),
        ("inserted", f"{g['inserted']:.0f}", f"{b['inserted']:.0f}"),
        ("completed trips", f"{g['ended']:.0f}", f"{b['ended']:.0f}"),
    ]
    w = max(len(r[0]) for r in rows)
    print(f"{'':{w}}  {'actuated':>12} {'mis-timed':>12}")
    print("-" * (w + 27))
    for name, a, c in rows:
        print(f"{name:{w}}  {a:>12} {c:>12}")

    # Demand is identical by construction (same route file, same seed); anything
    # not inserted is demand the network was too jammed to even accept.
    print(f"\nunserved demand (loaded but never inserted): "
          f"actuated {g['inserted'] - g['ended']:.0f} still en route, "
          f"mis-timed {b['inserted'] - b['ended']:.0f} still en route")


if __name__ == "__main__":
    main()
