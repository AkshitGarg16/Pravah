#!/usr/bin/env python3
"""Builds the congested counterpart of the busy-but-free-flowing ITO scenario.

Same network, same vehicles, same origins, destinations, departure times and
paths -- the route file is *reused verbatim*, not regenerated. The ONLY
difference from pravah_ito_busy.sumocfg is the signal programs. That is what
makes the pair worth showing: every difference in the outcome is caused by
the signal timing, because nothing else was allowed to vary.

A badly timed signal is not a randomly timed one, so the plan this writes is
the specific, extremely common real-world failure of a plan that was set once
and never retimed after the traffic pattern changed:

  1. FIXED-TIME, not actuated. Green runs for its programmed duration whether
     ten vehicles are waiting or none. An empty approach holds its green while
     the cross street queues.
  2. A LONG cycle. Long cycles mean long reds, and an approach that keeps
     receiving arrivals through a 90-second red builds a queue that its own
     green then has to clear on top of the traffic still arriving.
  3. Splits allocated INVERSELY to measured demand. Each phase's real demand
     is measured from the free-flowing run, and then green time is handed out
     in inverse proportion -- the busiest approach gets the shortest green.
     This is what a decades-old plan looks like once the arterial it was
     timed for has changed direction: not malicious, just never revisited.
  4. Anti-coordinated OFFSETS. Neighbouring lights start their cycles half a
     cycle apart, so a platoon released by one light arrives at the next just
     as it turns red. Coordination is the single cheapest thing a corridor
     can do; this is its exact opposite.

Every one of those is a real failure mode, and each is applied deliberately
rather than by picking numbers until the simulation looked bad.
"""

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from build_busy_freeflow import write_sumocfg  # noqa: E402

SCRATCH = Path("/tmp/claude-1000/-home-akshit/e9f3c31a-c12f-4d23-96d9-e689a8204097/scratchpad")


def measure_phase_demand(net_path, sumocfg, duration, seed, scratch):
    """Per-traffic-light, per-green-phase demand, measured from the good run.

    Reads which movements each green phase actually serves out of the
    network's own <connection> elements (a phase's state string is indexed by
    linkIndex, so a 'G'/'g' at index i means the movement of the connection
    with that linkIndex is being served), then attributes to that phase the
    traffic measured on the edges those movements come from.

    Measuring rather than assuming matters here: the whole point of the
    inverse split below is that it starves the approach that is genuinely
    busiest, and which approach that is, is a property of this demand on this
    network, not something to guess at.
    """
    dump = Path(scratch) / "jam_phase_demand.xml"
    add = Path(scratch) / "jam_ed.add.xml"
    add.write_text(
        f'<additional>\n  <edgeData id="pd" file="{dump}" '
        f'begin="{duration // 4}" end="{duration}" excludeEmpty="true"/>\n</additional>\n',
        encoding="utf-8")
    # -a on the command line REPLACES the config's own <additional-files>, it
    # does not add to them -- passing just the edgeData file would silently drop
    # the vehicle-type definitions and the run would abort on an unknown type.
    # So resolve the config's list (its entries are relative to the config's own
    # directory) and hand back the union.
    cfg_dir = Path(sumocfg).parent
    cfg_adds = ET.parse(sumocfg).getroot().find(".//additional-files").get("value")
    existing = [str((cfg_dir / a.strip()).resolve()) for a in cfg_adds.split(",") if a.strip()]
    subprocess.run(
        ["sumo", "-c", str(sumocfg), "-a", ",".join(existing + [str(add)]),
         "--seed", str(seed), "--no-step-log", "--no-warnings"],
        check=True, stdout=subprocess.DEVNULL, cwd=str(REPO))

    entered = {}
    for iv in ET.parse(dump).getroot().findall("interval"):
        for e in iv.findall("edge"):
            entered[e.get("id")] = float(e.get("entered", 0))

    net = ET.parse(net_path).getroot()
    # tls_id -> linkIndex -> source edge of that movement
    link_edge = defaultdict(dict)
    for c in net.findall("connection"):
        if c.get("tl") is not None:
            link_edge[c.get("tl")][int(c.get("linkIndex"))] = c.get("from")

    demand = {}
    for tl in net.findall("tlLogic"):
        tid = tl.get("id")
        per_phase = []
        for phase in tl.findall("phase"):
            state = phase.get("state")
            if "y" in state.lower():
                continue
            # One edge may feed several links in the same phase; count it once.
            edges = {link_edge[tid].get(i) for i, ch in enumerate(state) if ch in "Gg"}
            per_phase.append(sum(entered.get(e, 0.0) for e in edges if e))
        demand[tid] = per_phase
    return demand


def build_unoptimized_tls(net_path, out_path, demand, cycle=150, yellow=4,
                          min_green=8, program_id="unoptimized"):
    """Writes fixed-time programs with inverse-to-demand splits and bad offsets.

    Phase state strings are copied unchanged from the network's own programs,
    exactly as the actuated builder does: which movements may run together is
    a safety property, and a badly *timed* signal is still a safe one. Only
    the durations and offsets are wrong here -- no conflicting movements are
    ever given green together.
    """
    root = ET.parse(net_path).getroot()
    add = ET.Element("additional")

    for idx, tl in enumerate(root.findall("tlLogic")):
        tid = tl.get("id")
        phases = tl.findall("phase")
        greens = [p for p in phases if "y" not in p.get("state").lower()]
        n_yellow = len(phases) - len(greens)
        # Whatever the cycle does not spend on clearance intervals or on each
        # phase's floor is what the inverse split actually distributes.
        budget = cycle - n_yellow * yellow - len(greens) * min_green
        if budget < 0:
            raise SystemExit(f"cycle {cycle}s too short for {len(greens)} phases at min_green {min_green}s")

        d = demand.get(tid) or [1.0] * len(greens)
        d = (d + [1.0] * len(greens))[:len(greens)]
        # Inverse weights: the busier the approach, the smaller its share.
        inv = [1.0 / max(x, 1.0) for x in d]
        total_inv = sum(inv) or 1.0
        extra = [budget * w / total_inv for w in inv]

        # Anti-coordination: alternate lights start half a cycle apart, so a
        # platoon leaving one arrives at the next on red.
        offset = (idx % 2) * (cycle // 2)
        logic = ET.SubElement(add, "tlLogic", id=tid, type="static",
                              programID=program_id, offset=str(offset))
        gi = 0
        for phase in phases:
            state = phase.get("state")
            if "y" in state.lower():
                ET.SubElement(logic, "phase", duration=str(yellow), state=state)
            else:
                ET.SubElement(logic, "phase",
                              duration=str(round(min_green + extra[gi])), state=state)
                gi += 1

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(add)
    ET.indent(tree, space="  ")
    tree.write(out_path, encoding="UTF-8", xml_declaration=True)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--routes", default="routes/pravah_busy.rou.xml",
                    help="route file to reuse, relative to sumo/ -- must be the "
                         "free-flowing scenario's, so both runs carry identical demand")
    ap.add_argument("--duration", type=int, default=3600)
    ap.add_argument("--cycle", type=int, default=150)
    ap.add_argument("--yellow", type=int, default=4)
    ap.add_argument("--min-green", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--tag", default="jam")
    args = ap.parse_args()

    sumo_dir = REPO / "sumo"
    net = sumo_dir / "network" / "pravah.net.xml"
    good_cfg = sumo_dir / "pravah_ito_busy.sumocfg"
    tls_add = sumo_dir / "additional" / f"pravah_{args.tag}_tls.add.xml"
    vtype_add = sumo_dir / "additional" / "pravah_vtypes.add.xml"
    cfg = sumo_dir / f"pravah_ito_{args.tag}.sumocfg"

    if not (sumo_dir / args.routes).exists():
        raise SystemExit(f"{sumo_dir / args.routes} not found -- build the free-flowing "
                         f"scenario first with scripts/build_busy_freeflow.py")

    SCRATCH.mkdir(parents=True, exist_ok=True)
    demand = measure_phase_demand(net, good_cfg, args.duration, args.seed, SCRATCH)
    build_unoptimized_tls(net, tls_add, demand, cycle=args.cycle,
                          yellow=args.yellow, min_green=args.min_green)
    write_sumocfg(cfg, "network/pravah.net.xml", args.routes,
                  [f"additional/{vtype_add.name}", f"additional/{tls_add.name}"],
                  args.duration)

    print(f"measured phase demand (veh entering each phase's approaches):")
    for tid, d in demand.items():
        print(f"  {tid:>14}: {[round(x) for x in d]}")
    print(f"\nmis-timed programs: {tls_add}")
    print(f"config            : {cfg}")
    print(f"routes (reused)   : {args.routes}")


if __name__ == "__main__":
    main()
