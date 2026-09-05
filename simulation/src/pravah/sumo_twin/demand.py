"""Generates placeholder vehicle demand for the SUMO network via randomTrips.py.

The CSV has no origin-destination trip data -- it only tells us how fast
traffic moved on each segment, not where vehicles were going. So for this
first pass, demand comes from SUMO's bundled `randomTrips.py`: it samples
random start/end edges and routes between them, weighted toward
higher-speed (arterial) roads via `--speed-exponent` so traffic doesn't
spread evenly onto small residential edges. This is a stand-in to be
replaced later by real GNSS-derived origin-destination demand.
"""

import csv
import json
import os
import random
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .tomtom_feed import insertion_rate, interpolate_segment_series

# Rough Indian-urban-arterial composition: two-wheelers and cars dominate,
# with a smaller share of buses/trucks. Not sourced from a specific study --
# a defensible, documented approximation for what the demo should visually
# look like, not a claim of measured mode share. SUMO samples one of these
# per vehicle according to `probability` whenever a <flow> uses type="mix".
VTYPE_MIX = """  <vTypeDistribution id="mix">
    <vType id="car" vClass="passenger" length="4.4" width="1.8" probability="0.45" color="0.90,0.75,0.10"/>
    <vType id="twowheeler" vClass="motorcycle" length="2.0" width="0.7" probability="0.40" color="0.90,0.20,0.20"/>
    <vType id="bus" vClass="bus" length="10.0" width="2.5" probability="0.05" color="0.20,0.40,0.90"/>
    <vType id="truck" vClass="truck" length="7.5" width="2.4" probability="0.10" color="0.30,0.70,0.30"/>
  </vTypeDistribution>
"""


def find_duarouter():
    exe = shutil.which("duarouter")
    if exe:
        return exe
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        candidate = Path(sumo_home) / "bin" / "duarouter"
        if candidate.exists():
            return str(candidate)
    raise SystemExit(
        "duarouter not found on PATH or under $SUMO_HOME/bin.\n"
        "export SUMO_HOME=/home/kreacher/sumo-src && export PATH=$SUMO_HOME/bin:$PATH"
    )


def find_random_trips_script():
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        candidate = Path(sumo_home) / "tools" / "randomTrips.py"
        if candidate.exists():
            return str(candidate)
    raise SystemExit(
        "randomTrips.py not found. Set $SUMO_HOME to your SUMO checkout/install "
        "(it lives at $SUMO_HOME/tools/randomTrips.py)."
    )


def generate_routes(net_path, route_path, duration=3600, period=3.0, speed_exponent=2, seed=42):
    """Runs randomTrips.py -> validated .rou.xml next to a .trips.xml sibling file."""
    script = find_random_trips_script()
    trips_path = str(Path(route_path).with_suffix("")) + ".trips.xml"
    Path(route_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, script,
        "-n", str(net_path),
        "-r", str(route_path),
        "-o", trips_path,
        "-b", "0",
        "-e", str(duration),
        "-p", str(period),
        "--speed-exponent", str(speed_exponent),
        "--seed", str(seed),
        "--validate",       # run duarouter under the hood to reject unreachable OD pairs
        "--fringe-factor", "5",  # bias toward through-traffic entering/leaving at the network edge
        "--threads", "1",   # duarouter's parallel routing needs a Fox-toolkit build; we have neither
    ]
    subprocess.run(cmd, check=True)
    return route_path


def _real_edge_ids(net_root):
    return {e.get("id") for e in net_root.findall("edge") if e.get("function") != "internal"}


def _fringe_edges(net_root):
    """Edges touching a dead-end junction -- network-boundary roads, biased
    toward as plausible through-traffic destinations, same idea as
    randomTrips.py's --fringe-factor (see generate_routes' docstring)."""
    dead_ends = {j.get("id") for j in net_root.findall("junction") if j.get("type") == "dead_end"}
    fringe = [
        e.get("id") for e in net_root.findall("edge")
        if e.get("function") != "internal" and (e.get("from") in dead_ends or e.get("to") in dead_ends)
    ]
    return fringe or sorted(_real_edge_ids(net_root))  # degenerate fallback if a network has no fringe at all


def _connection_adjacency(net_root):
    """edge_id -> set of edge_ids reachable via one real <connection> hop.

    net.xml's <connection from=.. to=..> elements are the exact edge-to-edge
    movements duarouter itself considers valid -- junctions sharing an edge
    isn't enough on a mostly one-way network like this one (confirmed: an
    earlier version of this picked destinations by node-adjacency alone and
    duarouter rejected well over half the generated flows with "No
    connection between edge X and edge Y found").
    """
    adjacency = {}
    for conn in net_root.findall("connection"):
        frm, to = conn.get("from"), conn.get("to")
        if frm and to and not frm.startswith(":"):
            adjacency.setdefault(frm, set()).add(to)
    return adjacency


def _reachable_fringe(origin_edge, adjacency, fringe_set):
    """BFS over the connection graph from origin_edge; returns fringe edges actually reachable from it."""
    from collections import deque

    visited = {origin_edge}
    queue = deque([origin_edge])
    while queue:
        node = queue.popleft()
        for nxt in adjacency.get(node, ()):
            if nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)
    return [e for e in visited if e in fringe_set and e != origin_edge]


def _rows_by_segment(csv_path):
    rows = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.setdefault(row["segment_id"], []).append(row)
    return rows


def generate_calibrated_routes(net_path, segment_map_path, csv_path, route_path,
                                duration=3600, interval=300,
                                target_network_vehicles_per_hour=1200, seed=42,
                                hotspot_edges=None, hotspot_multiplier=1.0,
                                hotspot_multipliers=None):
    """Builds a .rou.xml whose per-edge insertion rate tracks the real CSV's
    (linearly interpolated -- see tomtom_feed.py) segment speed/volume data,
    replacing generate_routes()'s uniform random sampling. Vehicles are
    drawn from VTYPE_MIX, so runs include cars/two-wheelers/buses/trucks
    rather than one uniform vehicle type.

    Piecewise-constant approximation: the [0, duration] window is cut into
    `interval`-second chunks, each getting one <flow> per edge using the
    interpolated rate/speed at that chunk's midpoint -- SUMO's <flow> only
    supports a constant rate over its own begin/end window, so this is the
    standard way to approximate a continuously-varying rate with it.

    hotspot_edges/hotspot_multiplier: a deliberate synthetic stress option
    for the "before/after PRAVAH" demo -- edges in `hotspot_edges` get their
    interpolated sample_size scaled by `hotspot_multiplier` (default 1.0,
    a no-op) before their own insertion rate is computed, WITHOUT that
    scaled-up weight affecting the shared `weight_total` denominator every
    other edge's share is computed from. So non-hotspot edges get exactly
    the same rate they'd get without this option at all -- only the
    hotspot edges get artificially busier, on top of (not redistributed
    away from) the rest of the network's honest calibrated levels. This is
    not a claim that real ITO traffic runs this heavy; it's a deliberately
    exaggerated stress scenario built for a live "chaos vs PRAVAH" judge
    comparison, run at the exact same density in both cases so only the
    signal-control logic differs.

    hotspot_multipliers: {edge_id: multiplier}, for giving several
    approaches of the same junction distinctly different volumes instead
    of one shared multiplier -- e.g. one real approach at 10x, another at
    4x, a third left at its honest calibrated 1x, so a queue-responsive
    controller has an obvious "busiest side" to visibly favor. Resolved
    per edge as hotspot_multipliers.get(edge_id, hotspot_multiplier if
    edge_id in hotspot_edges else 1.0) -- an edge named in both takes its
    hotspot_multipliers value; the plain hotspot_edges/hotspot_multiplier
    pair above still works unchanged for edges not in this dict.

    An edge's hotspot_multipliers value can also be a list/tuple instead
    of one flat number -- one multiplier per time chunk (index k in the
    loop below), e.g. [1, 1, 8, 8, 8, 1, 1, 1, 1, 1] with interval=30 over
    a 300s run: quiet for the first minute, a sharp surge for the next 90s,
    then quiet again for the rest -- a demand *pulse*, not a constant
    stress level, for demonstrating a controller actually clearing a
    backlog it didn't create and isn't sustaining, rather than just
    running permanently oversaturated. A chunk index past the end of the
    list holds at the list's last value.
    """
    hotspot_edges = set(hotspot_edges or ())
    hotspot_multipliers = hotspot_multipliers or {}

    def _multiplier_for_chunk(edge_id, k):
        if edge_id in hotspot_multipliers:
            m = hotspot_multipliers[edge_id]
            if isinstance(m, (list, tuple)):
                return m[k] if k < len(m) else m[-1]
            return m
        if edge_id in hotspot_edges:
            return hotspot_multiplier
        return 1.0
    net_root = ET.parse(net_path).getroot()
    real_edges = _real_edge_ids(net_root)
    fringe_set = set(_fringe_edges(net_root))
    adjacency = _connection_adjacency(net_root)

    rows_by_segment = _rows_by_segment(csv_path)
    edge_to_segments = json.load(open(segment_map_path))

    series_by_edge = {}
    for edge_id, seg_ids in edge_to_segments.items():
        if edge_id not in real_edges or not seg_ids:
            continue
        rows = [r for sid in seg_ids for r in rows_by_segment.get(sid, [])]
        if rows:
            series_by_edge[edge_id] = interpolate_segment_series(rows, duration=duration)

    rng = random.Random(seed)
    destination = {}
    for edge_id in series_by_edge:
        reachable = _reachable_fringe(edge_id, adjacency, fringe_set)
        destination[edge_id] = rng.choice(reachable) if reachable else rng.choice(list(fringe_set))

    n_intervals = max(1, duration // interval)
    root = ET.Element("routes")
    root.append(ET.fromstring(VTYPE_MIX))

    flow_idx = 0
    for k in range(n_intervals):
        begin, end = k * interval, min((k + 1) * interval, duration)
        mid_t = (begin + end) / 2
        samples = {eid: series(mid_t) for eid, series in series_by_edge.items()}
        weight_total = sum(s["sample_size"] for s in samples.values())  # unscaled -- the honest, shared pot

        for edge_id, sample in samples.items():
            weight = sample["sample_size"] * _multiplier_for_chunk(edge_id, k)
            rate = insertion_rate(weight, weight_total, target_network_vehicles_per_hour)
            if rate < 1.0:
                continue  # not enough calibrated demand to bother with this interval/edge
            flow = ET.SubElement(
                root, "flow", id=f"cal_{flow_idx}", type="mix",
                begin=str(begin), end=str(end), vehsPerHour=f"{rate:.2f}",
                **{"from": edge_id, "to": destination[edge_id]},
            )
            # departSpeed="max" (SUMO's own keyword), not the literal
            # interpolated depart_speed_ms() value: a real run hit SUMO
            # rejecting a computed departure speed as "too high for the
            # departure edge" -- a FATAL error here (unlike duarouter's
            # --ignore-errors routing failures), since interpolation can
            # legitimately overshoot what a specific edge actually allows.
            # "max" always resolves to whatever's currently safe/legal, and
            # arguably is more realistic anyway: it reflects what's
            # actually feasible on that edge right now, not a fixed number
            # blind to current simulated congestion. The interpolated
            # speed still does real work -- it's what shapes vehsPerHour
            # via insertion_rate above; this only affects the one-time
            # departure instant, not the calibration itself.
            flow.set("departSpeed", "max")
            flow_idx += 1

    trips_path = str(Path(route_path).with_suffix("")) + ".calibrated_trips.xml"
    Path(route_path).parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(trips_path, encoding="UTF-8", xml_declaration=True)

    duarouter = find_duarouter()
    cmd = [duarouter, "-n", str(net_path), "-r", trips_path, "-o", str(route_path),
           "--ignore-errors", "--seed", str(seed)]
    subprocess.run(cmd, check=True)
    return route_path
