#!/usr/bin/env python3
"""Builds the "busy but free-flowing" ITO scenario.

Two things have to be true at once for this scenario, and they pull against
each other: enough vehicles on screen that the junction reads as a real Delhi
arterial (busy), and queues that fully drain every cycle so nothing gridlocks
(no congestion). This script produces both halves of that:

  1. ACTUATED signal programs (sumo/additional/pravah_busy_tls.add.xml).
     The network's netconvert-generated programs are fixed-time 40s greens on
     a 90s cycle -- long enough that an approach keeps arriving traffic for a
     full 85s of red, which is what builds standing queues even at modest
     demand. These are replaced with gap-based actuated programs: a short
     minimum green, extended only while vehicles keep crossing the detector,
     and cut as soon as a gap appears. Green time follows demand instead of
     the clock, so a busy approach is served longer and an empty one is
     skipped in seconds.

  2. Through-traffic DEMAND (sumo/routes/pravah_busy.rou.xml) via SUMO's
     randomTrips.py, inserted at the network fringe and routed across it, so
     vehicles drive through the junctions on camera rather than popping into
     existence mid-block. `--period` sets the insertion rate; that is the one
     knob tune_busy_freeflow.py sweeps to find the busiest level the network
     still absorbs without queue growth.

Vehicles are drawn from demand.py's VTYPE_MIX (cars / two-wheelers / buses /
trucks), so the traffic looks like Indian-urban-arterial traffic rather than
one uniform vehicle.
"""

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from pravah.sumo_twin.demand import VTYPE_MIX, find_duarouter  # noqa: E402

# demand.py's VTYPE_MIX with driving behaviour attached. The mode split
# (45% car / 40% two-wheeler / 10% truck / 5% bus), the dimensions and the
# colours are unchanged -- what's added is how these vehicles drive, which
# VTYPE_MIX leaves at SUMO's European defaults and which is what actually
# decides how much traffic a junction can pass.
#
# Three of these params are what lifts this network off its ~2 500 veh/h
# breakdown point, and all three are the ITO-realistic value rather than the
# default:
#
#   tau / minGap  SUMO defaults to a 1.0 s headway and a 2.5 m standstill gap
#                 -- motorway-grade spacing. Indian urban arterial traffic
#                 runs far tighter, and a two-wheeler tighter still; the
#                 tighter spacing is most of the extra capacity.
#   jmTimegapMinor  the gap a vehicle demands before pulling out of a minor
#                 road across priority traffic. The default 1.0 s is why
#                 every breakdown above showed "Yield" teleports: side-road
#                 vehicles waited for a gap that never came, their queue
#                 spilled back through the junction behind them, and the
#                 gridlock spread from there. Real drivers here accept much
#                 smaller gaps.
#   impatience    lets a vehicle that has been waiting progressively force
#                 its way in, instead of yielding indefinitely -- the direct
#                 cure for the same yield-deadlock.
#
# latAlignment/lcSublane matter with the config's <lateral-resolution>: they
# are what lets two-wheelers filter alongside cars rather than queue single
# file behind them.
BUSY_VTYPE_MIX = """  <vTypeDistribution id="mix">
    <vType id="car" vClass="passenger" length="4.4" width="1.8" probability="0.45"
           color="0.90,0.75,0.10" accel="2.6" decel="4.5" sigma="0.5" tau="0.9"
           minGap="1.5" speedFactor="normc(1.0,0.10,0.7,1.3)"
           jmTimegapMinor="0.5" jmDriveAfterRedTime="-1" impatience="0.6"
           latAlignment="compact" lcSublane="1.0"/>
    <vType id="twowheeler" vClass="motorcycle" length="2.0" width="0.7" probability="0.40"
           color="0.90,0.20,0.20" accel="3.0" decel="5.0" sigma="0.6" tau="0.7"
           minGap="0.8" speedFactor="normc(1.05,0.12,0.7,1.4)"
           jmTimegapMinor="0.3" impatience="0.9"
           latAlignment="arbitrary" lcSublane="1.0" lcPushy="0.7" lcAssertive="1.5"/>
    <vType id="bus" vClass="bus" length="10.0" width="2.5" probability="0.05"
           color="0.20,0.40,0.90" accel="1.2" decel="3.5" sigma="0.5" tau="1.1"
           minGap="2.0" speedFactor="normc(0.90,0.06,0.7,1.1)"
           jmTimegapMinor="0.8" impatience="0.4" latAlignment="center"/>
    <vType id="truck" vClass="truck" length="7.5" width="2.4" probability="0.10"
           color="0.30,0.70,0.30" accel="1.3" decel="3.5" sigma="0.5" tau="1.1"
           minGap="2.0" speedFactor="normc(0.90,0.06,0.7,1.1)"
           jmTimegapMinor="0.8" impatience="0.4" latAlignment="center"/>
  </vTypeDistribution>
"""

SUMO_TOOLS = Path(os.environ.get("SUMO_HOME") or Path(sys.prefix)) / "tools"
if not (SUMO_TOOLS / "randomTrips.py").exists():
    import sumolib
    SUMO_TOOLS = Path(sumolib.__file__).resolve().parent.parent / "sumo" / "tools"


def build_actuated_tls(net_path, out_path, min_dur=7, max_dur=32, yellow=4,
                       max_gap=2.1, detector_gap=2.0, program_id="busy"):
    """Rewrites every tlLogic in the net as a gap-based actuated program.

    The phase STATE strings are copied verbatim from the network's own
    programs -- those encode which movements may run together without
    conflicting, and inventing new ones would mean inventing new signal
    safety. Only the timing changes: each green gets a short minDur and a
    bounded maxDur it can grow into while traffic keeps flowing, and the
    yellow phases keep a fixed, non-negotiable clearance duration (a yellow
    is a clearance interval, not something demand may shorten).

    SUMO builds the induction loops an actuated program needs automatically
    when a tlLogic of type "actuated" carries no explicit detector params.
    """
    root = ET.parse(net_path).getroot()
    add = ET.Element("additional")
    count = 0
    for tl in root.findall("tlLogic"):
        logic = ET.SubElement(add, "tlLogic", id=tl.get("id"), type="actuated",
                              programID=program_id, offset="0")
        # max-gap: how long a green may go with no vehicle crossing the detector
        # before it is cut. ~2s is the standard "the platoon has passed" value;
        # smaller cuts sooner and keeps cycles short, which is what stops the
        # cross street from accumulating a queue while an empty green runs out.
        ET.SubElement(logic, "param", key="max-gap", value=str(max_gap))
        ET.SubElement(logic, "param", key="detector-gap", value=str(detector_gap))
        ET.SubElement(logic, "param", key="show-detectors", value="false")
        for phase in tl.findall("phase"):
            state = phase.get("state")
            if "y" in state.lower():
                ET.SubElement(logic, "phase", duration=str(yellow), state=state)
            else:
                ET.SubElement(logic, "phase", duration=str(max_dur), minDur=str(min_dur),
                              maxDur=str(max_dur), state=state)
        count += 1

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(add)
    ET.indent(tree, space="  ")
    tree.write(out_path, encoding="UTF-8", xml_declaration=True)
    return count


def write_vtypes(out_path, busy=True):
    """The vehicle mix on its own, as an additional-file randomTrips can reference."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("<additional>\n" + (BUSY_VTYPE_MIX if busy else VTYPE_MIX) + "</additional>\n")
    return out_path


def strip_inlined_vtypes(route_path):
    """Removes vType/vTypeDistribution elements from a generated route file.

    duarouter copies whatever vehicle types it was given straight into its
    output. Every route file here passes through duarouter at least once (and
    once more per rebalance_routes iteration), so without this the types end
    up defined in two places at once -- the additional file AND the route file
    -- and SUMO rejects the run outright with "Another vehicle type (or
    distribution) with the id 'bus' exists".

    The invariant this keeps: vehicle types are defined in exactly one place,
    additional/pravah_vtypes.add.xml, and every consumer (sumo, duarouter,
    randomTrips) is handed that file explicitly.
    """
    tree = ET.parse(route_path)
    root = tree.getroot()
    for tag in ("vTypeDistribution", "vType"):
        for el in root.findall(tag):
            root.remove(el)
    ET.indent(tree, space="    ")
    tree.write(route_path, encoding="UTF-8", xml_declaration=True)
    return route_path


def build_demand(net_path, route_path, vtype_path, period, duration, seed,
                 fringe_factor=12, min_distance=600):
    """randomTrips.py -> a validated .rou.xml of fringe-to-fringe trips."""
    trips_path = str(Path(route_path).with_suffix("")) + ".trips.xml"
    Path(route_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(SUMO_TOOLS / "randomTrips.py"),
        "-n", str(net_path), "-o", trips_path, "-r", str(route_path),
        "-b", "0", "-e", str(duration), "-p", f"{period:.4f}",
        "--seed", str(seed),
        # Bias insertion hard toward the network boundary: these are trips
        # crossing ITO, not trips that materialise inside it.
        "--fringe-factor", str(fringe_factor),
        "--min-distance", str(min_distance),
        # Favour the arterials over residential side streets, same reasoning
        # as demand.py's speed-exponent.
        "--speed-exponent", "2",
        "--additional-file", str(vtype_path),
        "--trip-attributes", 'type="mix" departLane="best" departSpeed="max"',
        "--validate", "--threads", "1",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    return strip_inlined_vtypes(route_path)


def rebalance_routes(net_path, trips_path, route_path, tls_add, vtype_add, iterations,
                     duration, seed, scratch):
    """Spreads the demand over the network by iterated equilibrium assignment.

    randomTrips + a single duarouter pass routes every vehicle down the
    free-flow shortest path, which is a path chosen as if the road were empty.
    On this network that funnels the load onto one corridor: measured at
    4000 veh/h, only 12 of 130 edges carried any real load, and the jam traced
    back through four priority junctions to a single-lane approach into the
    ITO signal -- one saturated chain capping a network with most of its
    capacity idle.

    So each iteration here re-routes the SAME trips against the travel times
    the PREVIOUS iteration actually measured, instead of against free-flow
    times: a corridor that ran slow last time is expensive this time, and
    traffic that has a viable alternative takes it. That is one step of
    dynamic user equilibrium -- the same principle as SUMO's own
    duaIterate.py, run inline here because the scenario needs the assignment
    and the signal timing to be built together.

    The trips (who travels, from where to where, when) never change -- only
    the paths they take. This does not quietly reduce the demand to make the
    numbers look better; it stops the demand from being routed as if the
    other 118 edges did not exist.
    """
    edge_dump = Path(scratch) / "assign_edgedata.xml"
    ed_add = Path(scratch) / "assign_ed.add.xml"
    ed_add.write_text(
        f'<additional>\n  <edgeData id="assign" file="{edge_dump}" '
        # Skip the fill-up: travel times measured while the network is still
        # emptying out are not the times a vehicle will actually meet.
        f'begin="{duration // 4}" end="{duration}" excludeEmpty="true"/>\n</additional>\n',
        encoding="utf-8")

    duarouter = find_duarouter()
    for _ in range(iterations):
        subprocess.run(
            ["sumo", "-n", str(net_path), "-r", str(route_path),
             # vtype_add explicitly: each duarouter pass below rewrites the route
             # file, and a pass only carries the vTypeDistribution through if it
             # was given it -- without this the re-routed file references a
             # type="mix" that no longer exists anywhere.
             "-a", f"{vtype_add},{tls_add},{ed_add}", "--end", str(duration),
             "--seed", str(seed), "--no-step-log", "--no-warnings",
             "--ignore-junction-blocker", "20", "--lateral-resolution", "0.8",
             "--time-to-teleport", "120"],
            check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            [duarouter, "-n", str(net_path), "-r", str(trips_path), "-o", str(route_path),
             "--additional-files", str(vtype_add),
             "--weight-files", str(edge_dump), "--weight-attribute", "traveltime",
             "--ignore-errors", "--seed", str(seed),
             # Without this, every vehicle in a congested corridor is handed the
             # identical "best" detour and simply moves the queue somewhere else.
             # A random factor spreads them across comparable alternatives, which
             # is also closer to how drivers actually differ.
             "--weights.random-factor", "1.5"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        strip_inlined_vtypes(route_path)
    return route_path


def write_sumocfg(out_path, net_rel, route_rel, add_rels, duration):
    add = ",".join(add_rels)
    Path(out_path).write_text(f"""<configuration>
    <input>
        <net-file value="{net_rel}"/>
        <route-files value="{route_rel}"/>
        <additional-files value="{add}"/>
    </input>
    <time>
        <begin value="0"/>
        <end value="{duration}"/>
        <step-length value="0.2"/>
    </time>
    <processing>
        <!-- Sublane model: two-wheelers filtering alongside cars is how an
             Indian arterial actually packs vehicles in, and it is also what
             keeps a busy approach from behaving like a single-file queue. -->
        <lateral-resolution value="0.8"/>
        <time-to-teleport value="120"/>
        <collision.action value="warn"/>
        <!-- A vehicle that has blocked a junction this long is let through
             rather than left to hold a deadlock forever. Without it a single
             junction-blocking vehicle can lock a whole ring of streets, which
             is a simulation artefact, not traffic. -->
        <ignore-junction-blocker value="20"/>
    </processing>
    <report>
        <no-step-log value="true"/>
        <duration-log.statistics value="true"/>
    </report>
</configuration>
""", encoding="utf-8")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", type=float, default=0.5,
                    help="seconds between vehicle insertions (smaller = busier)")
    ap.add_argument("--duration", type=int, default=3600)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-dur", type=int, default=7)
    ap.add_argument("--max-dur", type=int, default=32)
    ap.add_argument("--max-gap", type=float, default=2.1)
    ap.add_argument("--tag", default="busy")
    ap.add_argument("--skip-demand", action="store_true")
    ap.add_argument("--assign-iterations", type=int, default=0,
                    help="equilibrium re-routing passes (see rebalance_routes); 0 = free-flow shortest path only")
    ap.add_argument("--scratch", default="/tmp/claude-1000/-home-akshit/"
                                         "e9f3c31a-c12f-4d23-96d9-e689a8204097/scratchpad")
    args = ap.parse_args()

    sumo_dir = REPO / "sumo"
    net = sumo_dir / "network" / "pravah.net.xml"
    tls_add = sumo_dir / "additional" / f"pravah_{args.tag}_tls.add.xml"
    vtype_add = sumo_dir / "additional" / "pravah_vtypes.add.xml"
    routes = sumo_dir / "routes" / f"pravah_{args.tag}.rou.xml"
    cfg = sumo_dir / f"pravah_ito_{args.tag}.sumocfg"

    n = build_actuated_tls(net, tls_add, min_dur=args.min_dur, max_dur=args.max_dur,
                           max_gap=args.max_gap)
    write_vtypes(vtype_add)
    if not args.skip_demand:
        build_demand(net, routes, vtype_add, args.period, args.duration, args.seed)
        if args.assign_iterations:
            trips = str(Path(routes).with_suffix("")) + ".trips.xml"
            rebalance_routes(net, trips, routes, tls_add, vtype_add,
                             args.assign_iterations, args.duration, args.seed,
                             args.scratch)
    write_sumocfg(cfg, "network/pravah.net.xml",
                  f"routes/{routes.name}",
                  [f"additional/{vtype_add.name}", f"additional/{tls_add.name}"],
                  args.duration)
    print(f"actuated programs: {n} -> {tls_add}")
    print(f"routes            : {routes}")
    print(f"config            : {cfg}")


if __name__ == "__main__":
    main()
