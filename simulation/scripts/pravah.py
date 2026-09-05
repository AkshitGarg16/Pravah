#!/usr/bin/env python3
"""PRAVAH -- the ITO junctions running well: busy, but free-flowing.

Actuated, gap-based signals on realistic Delhi demand. Two modes:

  default     opens sumo-gui with the scenario's own view settings (vehicles
              coloured by speed, framed on the ITO cluster) so the run can be
              watched or recorded.
  --headless  runs it as fast as the machine allows and prints the same
              busy/congestion numbers tune_busy_freeflow.py scores on, so a
              claim that a run was congestion-free is something you can check
              rather than something you have to take on trust.

Its counterpart is current.py, which runs the same demand and the same routes
through today's mis-timed fixed-time signals and jams. Signal timing is the
only difference between the two.

The signals are the actuated programs from build_busy_freeflow.py; SUMO runs
them itself, so no TraCI loop is needed here. run_digital_twin.py stays the
entry point for TraCI-driven control experiments.
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from tune_busy_freeflow import analyse  # noqa: E402

BUSY_CFG = REPO / "sumo" / "pravah_ito_busy.sumocfg"
GUI_SETTINGS = REPO / "sumo" / "additional" / "pravah_busy_gui.xml"


def build_parser(default_cfg, description):
    ap = argparse.ArgumentParser(description=description,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sumocfg", default=str(default_cfg))
    # GUI by default: the point of these scenarios is watching them. --headless
    # is the opt-out, for when you want the metrics rather than the picture.
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--summary", default=None,
                    help="headless only: where to write the summary xml")
    ap.add_argument("--delay", type=int, default=60,
                    help="GUI only: ms of real time per simulated step (0 = as fast as possible)")
    ap.add_argument("--paused", action="store_true", help="GUI only: open without auto-starting")
    return ap


def run(args, label):
    """Runs one scenario. Shared by pravah.py and current.py."""
    gui = not args.headless
    summary = args.summary or f"/tmp/pravah_{label}_summary.xml"
    cmd = ["sumo-gui" if gui else "sumo", "-c", args.sumocfg,
           "--seed", str(args.seed), "--no-step-log"]
    if gui:
        # No --quit-on-end: the window stays open at the end so the run can be
        # inspected (and rewound with the GUI's own time slider) instead of
        # vanishing the moment it finishes.
        cmd += ["--gui-settings-file", str(GUI_SETTINGS), "--delay", str(args.delay)]
        if not args.paused:
            cmd += ["--start"]
    else:
        cmd += ["--summary-output", summary, "--no-warnings"]

    if gui:
        print(f"opening sumo-gui on {args.sumocfg}"
              f"{' (paused -- press Play)' if args.paused else ''}")
    subprocess.run(cmd, check=True)

    if not gui:
        m = analyse(summary)
        print(f"vehicles in network : {m['mean_running']:.0f} mean, {m['peak_running']:.0f} peak")
        print(f"mean speed          : {m['mean_speed']:.2f} m/s ({m['mean_speed']*3.6:.0f} km/h)")
        print(f"stopped at any time : {m['halting_frac']:.1%} of vehicles")
        print(f"2nd-half growth     : {m['running_slope_2h']:+.0f} vehicles "
              f"({'accumulating' if m['running_slope_2h'] > 40 else 'steady state'})")
        print(f"inserted / completed: {m['inserted']:.0f} / {m['ended']:.0f}")


def main():
    args = build_parser(BUSY_CFG, __doc__).parse_args()
    run(args, "busy")


if __name__ == "__main__":
    main()
