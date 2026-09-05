#!/usr/bin/env python3
"""CURRENT -- the same ITO junctions as they are today: signals badly timed, jams.

Same network, same demand and the *same route file* as pravah.py, driven
through fixed-time programs built by build_jam_scenario.py: a 150 s cycle,
green splits allocated inversely to measured demand, and neighbouring
junctions deliberately anti-coordinated. Signal timing is the only variable
between this run and pravah.py, so every difference in the numbers below is
attributable to it.

One caveat worth knowing when reading the output: the shared route file was
path-optimised against the *good* signals, so vehicles here do not re-route
around the congestion they meet. That is deliberate -- it holds origins,
destinations and paths identical across the two runs -- but it means this is
the effect of bad timing on fixed traffic, not on traffic free to avoid it.

  default     opens sumo-gui so the queues and spillback can be watched.
  --headless  prints the congestion numbers instead.

Run scripts/compare_signal_plans.py to see both side by side.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import pravah  # noqa: E402  -- the sibling script, not the src/ package

JAM_CFG = REPO / "sumo" / "pravah_ito_jam.sumocfg"


def main():
    args = pravah.build_parser(JAM_CFG, __doc__).parse_args()
    pravah.run(args, "jam")


if __name__ == "__main__":
    main()
