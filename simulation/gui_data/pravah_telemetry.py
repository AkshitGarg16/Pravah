#!/usr/bin/env python3
"""Streams live ITO junction analytics from a running SUMO simulation over TCP.

Runs the simulation under TraCI on this machine and publishes everything it
can see -- junction ids, the original TomTom segment ids behind each edge,
signal state, queues, congestion, vehicle counts broken down by type -- as
newline-delimited JSON to any number of connected clients, on any machine
that can reach this one.

WIRE FORMAT
-----------
One JSON object per line, UTF-8, terminated by "\\n". Nothing else: no
length prefix, no framing, no handshake. Any language on the other end can
read it with "read a line, parse it as JSON", which is the point -- the GUI
consuming this does not have to be Python, or know anything about SUMO.

A client receives exactly one `meta` message the moment it connects, then a
`snapshot` message every publish interval for as long as it stays connected:

  {"type": "meta",     ...}   once, on connect -- the things that never change:
                              network geometry in both SUMO x/y and WGS84
                              lon/lat, the junction inventory, every signal's
                              full program, and the edge -> segment_id map.
  {"type": "snapshot", ...}   repeatedly -- everything that does change.
  {"type": "end",      ...}   once, when the simulation finishes.

A client joining late is never left guessing: it gets `meta` first, so it can
draw the network before the first snapshot arrives.

WHY THE SEGMENT IDS MATTER
--------------------------
Every edge carries the original CSV `segment_id`s it was built from (see
src/pravah/sumo_twin/segment_map.py -- netconvert merged 299 source segments
into 132 edges, so this is a one-to-many map, not a rename). That is what
lets the receiving end line simulated numbers up against the real TomTom
measurements for the same stretch of road, or against a camera keyed to a
segment id, rather than against a SUMO edge id that means nothing outside
this simulation.

ROBUSTNESS
----------
Publishing must never be allowed to slow the simulation down, so a client
that stops reading is dropped rather than waited on (see _Broadcaster). All
TraCI calls happen on the main thread -- TraCI connections are not
thread-safe, and the accept loop runs on its own thread precisely so it can
never touch one.
"""

import argparse
import json
import os
import socket
import sys
import threading
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.path.join(os.environ.get("SUMO_HOME", ""), "tools"))

import traci  # noqa: E402
import sumolib  # noqa: E402

SCHEMA = "pravah.telemetry/1"

# Speed as a fraction of the road's own limit, which is the only congestion
# measure that means the same thing on a 30 km/h side street and a 70 km/h
# arterial. Bands are named so the GUI does not have to invent its own.
CONGESTION_BANDS = ((0.75, "free"), (0.50, "light"), (0.30, "heavy"), (0.0, "jammed"))

# A signalised approach is SUPPOSED to be stopped for part of every cycle, so
# speed is the wrong congestion measure at a junction -- by a speed test, every
# red light in the city reads as a jam. What actually distinguishes congestion
# from normal operation is whether the queue clears: a queue occupying most of
# its approach is about to spill back through the junction behind it, and one
# occupying a fraction of it is just a red light doing its job. So junctions are
# scored on how much of the approach the queue fills, and roads on speed.
QUEUE_BANDS = ((0.15, "free"), (0.40, "light"), (0.70, "heavy"))


def congestion_level(ratio):
    """Congestion from speed as a fraction of the limit. For roads, not junctions."""
    for threshold, name in CONGESTION_BANDS:
        if ratio >= threshold:
            return name
    return "jammed"


def queue_congestion_level(queue_ratio):
    """Congestion from how much of an approach its queue fills. For junctions."""
    for threshold, name in QUEUE_BANDS:
        if queue_ratio <= threshold:
            return name
    return "jammed"


class _Broadcaster:
    """Accepts TCP clients on a thread and fans out one JSON line per message.

    A GUI on another laptop that stalls -- redrawing, garbage collecting, or
    simply killed with its socket left half-open -- must not be able to stall
    the simulation feeding it. So sends are given a short timeout and a client
    that cannot keep up is disconnected instead of waited on: this is live
    telemetry, where the newest snapshot matters and a backlog of stale ones
    does not.
    """

    def __init__(self, host, port, send_timeout=2.0):
        self._clients = []
        self._lock = threading.Lock()
        self._meta_line = None
        self._send_timeout = send_timeout
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(16)
        self.host, self.port = self._sock.getsockname()
        self._stop = threading.Event()
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def set_meta(self, meta):
        """Stores the message every future client gets on connect."""
        with self._lock:
            self._meta_line = (json.dumps(meta) + "\n").encode("utf-8")

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except OSError:
                return
            conn.settimeout(self._send_timeout)
            # TCP would otherwise coalesce small snapshots to fill a segment,
            # adding latency to exactly the messages that are worth having early.
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                meta = self._meta_line
            try:
                if meta:
                    conn.sendall(meta)
            except OSError:
                conn.close()
                continue
            with self._lock:
                self._clients.append((conn, addr))
            print(f"[telemetry] client connected: {addr[0]}:{addr[1]} "
                  f"({len(self._clients)} connected)", flush=True)

    def broadcast(self, message):
        line = (json.dumps(message) + "\n").encode("utf-8")
        with self._lock:
            clients = list(self._clients)
        dead = []
        for conn, addr in clients:
            try:
                conn.sendall(line)
            except OSError:
                dead.append((conn, addr))
        if dead:
            with self._lock:
                for item in dead:
                    if item in self._clients:
                        self._clients.remove(item)
            for conn, addr in dead:
                conn.close()
                print(f"[telemetry] client dropped: {addr[0]}:{addr[1]}", flush=True)

    @property
    def client_count(self):
        with self._lock:
            return len(self._clients)

    def close(self):
        self._stop.set()
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for conn, _ in clients:
            try:
                conn.close()
            except OSError:
                pass
        self._sock.close()


def load_segment_map(path):
    """edge_id -> [original CSV segment_id, ...]."""
    if not Path(path).exists():
        return {}
    return json.load(open(path))


def load_segment_reference(csv_path):
    """Real-world reference values per segment_id, straight from the source CSV.

    Shipping these alongside the live numbers means the receiving end can show
    "simulated 18 km/h against a measured 21 km/h on a 60 km/h road" without
    needing the CSV itself or knowing how to parse it. The CSV holds one row
    per weekday per segment, so the observed speed is averaged across them and
    the row count is reported next to it rather than hidden.
    """
    import csv
    if not Path(csv_path).exists():
        return {}
    acc = defaultdict(lambda: {"speeds": [], "street_name": None,
                               "speed_limit_kmh": None, "distance_m": None})
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sid = row.get("segment_id")
            if not sid:
                continue
            entry = acc[sid]
            try:
                entry["speeds"].append(float(row["average_speed"]))
            except (KeyError, ValueError):
                pass
            entry["street_name"] = entry["street_name"] or (row.get("street_name") or None)
            for key, col, cast in (("speed_limit_kmh", "speed_limit", float),
                                   ("distance_m", "distance_m", float)):
                if entry[key] is None:
                    try:
                        entry[key] = cast(row[col])
                    except (KeyError, ValueError, TypeError):
                        pass
    return {
        sid: {
            "street_name": e["street_name"],
            "speed_limit_kmh": e["speed_limit_kmh"],
            "distance_m": e["distance_m"],
            "observed_avg_speed_kmh": (sum(e["speeds"]) / len(e["speeds"])) if e["speeds"] else None,
            "observed_samples": len(e["speeds"]),
        }
        for sid, e in acc.items()
    }


def _geo(x, y):
    """SUMO x/y -> (lon, lat), or None if the network carries no projection."""
    try:
        lon, lat = traci.simulation.convertGeo(x, y)
        return round(lon, 7), round(lat, 7)
    except traci.TraCIException:
        return None


def build_lane_index():
    """edge_id -> [lane_id, ...], built once.

    Both the meta and the per-snapshot paths need an edge's lanes, and TraCI
    has no getLanes(edge). Rebuilding it by scanning every lane for every edge
    is ~130 x ~300 comparisons per snapshot for something that cannot change
    during a run, so it is computed here and passed in.
    """
    index = defaultdict(list)
    for lane in traci.lane.getIDList():
        if not lane.startswith(":"):
            index[traci.lane.getEdgeID(lane)].append(lane)
    return dict(index)


def build_meta(scenario, segment_map, seg_ref, publish_interval, lane_index):
    """The static half of the feed: everything a GUI needs to draw the map once.

    Sent on connect rather than repeated in every snapshot -- geometry for 130+
    edges is far larger than a snapshot, and none of it changes during a run.
    """
    edges = [e for e in traci.edge.getIDList() if not e.startswith(":")]
    tls_ids = list(traci.trafficlight.getIDList())

    edge_meta = []
    for eid in edges:
        lanes = lane_index.get(eid, [])
        shape = traci.lane.getShape(lanes[0]) if lanes else []
        sids = segment_map.get(eid, [])
        edge_meta.append({
            "edge_id": eid,
            "segment_ids": sids,
            "street_name": traci.edge.getStreetName(eid) or None,
            "from_junction": traci.edge.getFromJunction(eid),
            "to_junction": traci.edge.getToJunction(eid),
            "lane_ids": lanes,
            "lane_count": len(lanes),
            "length_m": round(traci.lane.getLength(lanes[0]), 2) if lanes else None,
            "speed_limit_ms": round(traci.lane.getMaxSpeed(lanes[0]), 2) if lanes else None,
            "shape_xy": [[round(x, 2), round(y, 2)] for x, y in shape],
            "shape_lonlat": [list(g) for g in (_geo(x, y) for x, y in shape) if g],
            "reference": [seg_ref[s] | {"segment_id": s} for s in sids if s in seg_ref],
        })

    junctions = []
    for jid in traci.junction.getIDList():
        if jid.startswith(":"):
            continue
        x, y = traci.junction.getPosition(jid)
        junctions.append({
            "junction_id": jid,
            "is_traffic_light": jid in tls_ids,
            "position_xy": [round(x, 2), round(y, 2)],
            "position_lonlat": _geo(x, y),
            "incoming_edges": [e for e in traci.junction.getIncomingEdges(jid) if not e.startswith(":")],
            "outgoing_edges": [e for e in traci.junction.getOutgoingEdges(jid) if not e.startswith(":")],
            "shape_xy": [[round(px, 2), round(py, 2)] for px, py in traci.junction.getShape(jid)],
        })

    signals = []
    for tid in tls_ids:
        logic = traci.trafficlight.getAllProgramLogics(tid)[0]
        # getControlledLinks is indexed by link index, which is exactly the
        # index into each phase's state string -- that correspondence is what
        # lets a GUI light up the right approach for the right character.
        links = traci.trafficlight.getControlledLinks(tid)
        signals.append({
            "junction_id": tid,
            "program_id": traci.trafficlight.getProgram(tid),
            "phases": [{
                "index": i,
                "state": p.state,
                "duration_s": p.duration,
                "min_dur_s": p.minDur,
                "max_dur_s": p.maxDur,
                "is_green_phase": "y" not in p.state.lower(),
            } for i, p in enumerate(logic.phases)],
            "controlled_lanes": sorted(set(traci.trafficlight.getControlledLanes(tid))),
            "links": [
                [{"from_lane": a, "to_lane": b, "via_lane": v} for a, b, v in group]
                for group in links
            ],
        })

    net_x1, net_y1, net_x2, net_y2 = (*traci.simulation.getNetBoundary()[0],
                                      *traci.simulation.getNetBoundary()[1])
    return {
        "schema": SCHEMA,
        "type": "meta",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "scenario": str(scenario),
        "publish_interval_s": publish_interval,
        "step_length_s": traci.simulation.getDeltaT(),
        "sim_end_s": traci.simulation.getEndTime(),
        "congestion_bands": {
            "roads_by_speed_ratio": [{"min_speed_ratio": t, "level": n} for t, n in CONGESTION_BANDS],
            "junctions_by_queue_ratio": [{"max_queue_ratio": t, "level": n} for t, n in QUEUE_BANDS],
        },
        "network_boundary_xy": [round(net_x1, 2), round(net_y1, 2),
                                round(net_x2, 2), round(net_y2, 2)],
        "network_boundary_lonlat": [_geo(net_x1, net_y1), _geo(net_x2, net_y2)],
        "junctions": junctions,
        "edges": edge_meta,
        "signals": signals,
        "vehicle_types": sorted(traci.vehicletype.getIDList()),
    }


def _types_on(vehicle_ids):
    return dict(Counter(traci.vehicle.getTypeID(v) for v in vehicle_ids))


def _lane_block(lane_id, segment_map):
    """The per-approach analytics block, shared by junctions and segments."""
    vids = traci.lane.getLastStepVehicleIDs(lane_id)
    limit = traci.lane.getMaxSpeed(lane_id)
    speed = traci.lane.getLastStepMeanSpeed(lane_id)
    ratio = (speed / limit) if limit > 0 else 0.0
    edge_id = traci.lane.getEdgeID(lane_id)
    halting = traci.lane.getLastStepHaltingNumber(lane_id)
    lane_length = traci.lane.getLength(lane_id)
    queue_m = halting * (traci.lane.getLastStepLength(lane_id) or 5.0)
    return {
        "lane_id": lane_id,
        "edge_id": edge_id,
        "segment_ids": segment_map.get(edge_id, []),
        "vehicles": traci.lane.getLastStepVehicleNumber(lane_id),
        "halting_vehicles": halting,
        # Queue length in metres, not just a vehicle count: a GUI drawing a
        # queue bar needs a distance, and 8 buses are not 8 two-wheelers.
        "queue_length_m": round(queue_m, 1),
        "lane_length_m": round(lane_length, 1),
        # How much of the approach the queue fills. At 1.0 the queue has reached
        # the upstream junction and is blocking it -- the point at which one
        # junction's problem becomes its neighbour's.
        "queue_ratio": round(min(queue_m / lane_length, 1.0), 3) if lane_length else 0.0,
        "mean_speed_ms": round(speed, 2),
        "speed_limit_ms": round(limit, 2),
        "speed_ratio": round(ratio, 3),
        "congestion": congestion_level(ratio),
        "occupancy": round(traci.lane.getLastStepOccupancy(lane_id), 4),
        "waiting_time_s": round(traci.lane.getWaitingTime(lane_id), 1),
        "travel_time_s": round(traci.lane.getTraveltime(lane_id), 2),
        "vehicle_types": _types_on(vids),
        "co2_mg_s": round(traci.lane.getCO2Emission(lane_id), 1),
        "fuel_ml_s": round(traci.lane.getFuelConsumption(lane_id), 4),
        "noise_db": round(traci.lane.getNoiseEmission(lane_id), 1),
    }


def build_snapshot(seq, segment_map, teleports, include_vehicles, lane_index):
    now = traci.simulation.getTime()
    tls_ids = traci.trafficlight.getIDList()

    junctions = []
    for tid in tls_ids:
        state = traci.trafficlight.getRedYellowGreenState(tid)
        controlled = traci.trafficlight.getControlledLanes(tid)
        # One lane can feed several link indices at the same junction; keep the
        # lane once and record every signal character that applies to it, so a
        # protected-left-plus-through approach is not silently reduced to one.
        per_lane = {}
        for idx, lane in enumerate(controlled):
            per_lane.setdefault(lane, []).append(idx)

        approaches = []
        for lane, indices in per_lane.items():
            block = _lane_block(lane, segment_map)
            block["link_indices"] = indices
            block["signal_chars"] = [state[i] for i in indices]
            # Green if any movement from this lane has green: an approach with a
            # protected left on green and a through on red is not "stopped".
            block["has_green"] = any(state[i] in "Gg" for i in indices)
            approaches.append(block)

        total_veh = sum(a["vehicles"] for a in approaches)
        total_queue = sum(a["halting_vehicles"] for a in approaches)
        worst_queue = max((a["queue_ratio"] for a in approaches), default=0.0)
        # Speed is only meaningful on approaches currently being served; a red
        # approach is stopped by design, not by congestion.
        green_ratios = [a["speed_ratio"] for a in approaches if a["has_green"]]
        next_switch = traci.trafficlight.getNextSwitch(tid)
        phase = traci.trafficlight.getPhase(tid)
        junctions.append({
            "junction_id": tid,
            "traffic_light": {
                "program_id": traci.trafficlight.getProgram(tid),
                "phase_index": phase,
                "state": state,
                "phase_name": traci.trafficlight.getPhaseName(tid) or None,
                "is_green_phase": "y" not in state.lower(),
                "time_in_phase_s": round(traci.trafficlight.getSpentDuration(tid), 1),
                "phase_duration_s": round(traci.trafficlight.getPhaseDuration(tid), 1),
                "next_switch_sim_s": round(next_switch, 1),
                "seconds_to_switch": round(next_switch - now, 1),
            },
            "approaches": approaches,
            "totals": {
                "vehicles": total_veh,
                "queued_vehicles": total_queue,
                "queue_length_m": round(sum(a["queue_length_m"] for a in approaches), 1),
                "max_waiting_time_s": round(max((a["waiting_time_s"] for a in approaches), default=0.0), 1),
                "vehicle_types": dict(sum((Counter(a["vehicle_types"]) for a in approaches), Counter())),
                "worst_queue_ratio": round(worst_queue, 3),
                "green_speed_ratio": round(min(green_ratios), 3) if green_ratios else None,
                "congestion": queue_congestion_level(worst_queue),
                "spillback": worst_queue >= 0.95,
            },
        })

    segments = []
    for eid in traci.edge.getIDList():
        if eid.startswith(":"):
            continue
        vids = traci.edge.getLastStepVehicleIDs(eid)
        if not vids:
            continue  # nothing to report; keeps the payload proportional to activity
        speed = traci.edge.getLastStepMeanSpeed(eid)
        lanes = lane_index.get(eid, [])
        limit = traci.lane.getMaxSpeed(lanes[0]) if lanes else 0.0
        length = traci.lane.getLength(lanes[0]) if lanes else 0.0
        ratio = (speed / limit) if limit > 0 else 0.0
        lane_km = (length * len(lanes)) / 1000.0
        segments.append({
            "edge_id": eid,
            "segment_ids": segment_map.get(eid, []),
            "street_name": traci.edge.getStreetName(eid) or None,
            "vehicles": len(vids),
            "halting_vehicles": traci.edge.getLastStepHaltingNumber(eid),
            "mean_speed_ms": round(speed, 2),
            "speed_limit_ms": round(limit, 2),
            "speed_ratio": round(ratio, 3),
            "congestion": congestion_level(ratio),
            "density_veh_per_km_lane": round(len(vids) / lane_km, 2) if lane_km else None,
            "occupancy": round(traci.edge.getLastStepOccupancy(eid), 4),
            "waiting_time_s": round(traci.edge.getWaitingTime(eid), 1),
            "travel_time_s": round(traci.edge.getTraveltime(eid), 2),
            "vehicle_types": _types_on(vids),
            "co2_mg_s": round(traci.edge.getCO2Emission(eid), 1),
            "fuel_ml_s": round(traci.edge.getFuelConsumption(eid), 4),
        })

    all_ids = traci.vehicle.getIDList()
    speeds = [traci.vehicle.getSpeed(v) for v in all_ids]
    allowed = [traci.vehicle.getAllowedSpeed(v) for v in all_ids]
    ratios = [s / a for s, a in zip(speeds, allowed) if a > 0]
    mean_ratio = (sum(ratios) / len(ratios)) if ratios else 1.0

    snapshot = {
        "schema": SCHEMA,
        "type": "snapshot",
        "seq": seq,
        "sim_time_s": round(now, 1),
        "wall_time": datetime.now(timezone.utc).isoformat(),
        "network": {
            "running_vehicles": len(all_ids),
            "halting_vehicles": sum(1 for s in speeds if s < 0.1),
            "mean_speed_ms": round(sum(speeds) / len(speeds), 2) if speeds else 0.0,
            "mean_speed_ratio": round(mean_ratio, 3),
            "congestion": congestion_level(mean_ratio),
            "vehicle_types": _types_on(all_ids),
            "departed_this_step": traci.simulation.getDepartedNumber(),
            "arrived_this_step": traci.simulation.getArrivedNumber(),
            "pending_insertions": len(traci.simulation.getPendingVehicles()),
            "expected_remaining": traci.simulation.getMinExpectedNumber(),
            "teleports_total": teleports,
            "collisions_this_step": traci.simulation.getCollidingVehiclesNumber(),
        },
        "junctions": junctions,
        "segments": segments,
    }

    if include_vehicles:
        vehicles = []
        for v in all_ids:
            x, y = traci.vehicle.getPosition(v)
            edge = traci.vehicle.getRoadID(v)
            vehicles.append({
                "id": v,
                "type": traci.vehicle.getTypeID(v),
                "vehicle_class": traci.vehicle.getVehicleClass(v),
                "edge_id": edge,
                "segment_ids": segment_map.get(edge, []),
                "lane_id": traci.vehicle.getLaneID(v),
                "position_xy": [round(x, 2), round(y, 2)],
                "position_lonlat": _geo(x, y),
                "angle_deg": round(traci.vehicle.getAngle(v), 1),
                "speed_ms": round(traci.vehicle.getSpeed(v), 2),
                "allowed_speed_ms": round(traci.vehicle.getAllowedSpeed(v), 2),
                "acceleration_ms2": round(traci.vehicle.getAcceleration(v), 2),
                "waiting_time_s": round(traci.vehicle.getWaitingTime(v), 1),
                "accumulated_waiting_s": round(traci.vehicle.getAccumulatedWaitingTime(v), 1),
                "time_loss_s": round(traci.vehicle.getTimeLoss(v), 1),
                "distance_m": round(traci.vehicle.getDistance(v), 1),
                "co2_mg_s": round(traci.vehicle.getCO2Emission(v), 1),
            })
        snapshot["vehicles"] = vehicles
    return snapshot


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sumocfg", default=str(REPO / "sumo" / "pravah_ito_busy.sumocfg"))
    ap.add_argument("--host", default="0.0.0.0",
                    help="0.0.0.0 (default) accepts connections from other machines; "
                         "127.0.0.1 restricts to this one")
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--rate", type=float, default=1.0,
                    help="publish one snapshot every N simulated seconds")
    ap.add_argument("--gui", action="store_true", help="also show sumo-gui locally")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--include-vehicles", action="store_true",
                    help="add a per-vehicle array to every snapshot (position, speed, "
                         "waiting time, emissions) -- much larger payloads")
    ap.add_argument("--wait-for-client", action="store_true",
                    help="hold the simulation at t=0 until at least one client connects")
    ap.add_argument("--realtime", action="store_true",
                    help="pace the run at wall-clock speed instead of as fast as possible")
    ap.add_argument("--segment-map",
                    default=str(REPO / "sumo" / "network" / "pravah.net.segment_map.json"))
    ap.add_argument("--reference-csv",
                    default=str(REPO / "data" / "pravah_900to1000_balanced.csv"))
    args = ap.parse_args()

    segment_map = load_segment_map(args.segment_map)
    seg_ref = load_segment_reference(args.reference_csv)
    print(f"[telemetry] segment map: {len(segment_map)} edges, "
          f"reference data: {len(seg_ref)} segments", flush=True)

    bus = _Broadcaster(args.host, args.port)
    print(f"[telemetry] listening on {args.host}:{args.port}", flush=True)
    for ip in _local_ips():
        print(f"[telemetry]   reachable at  {ip}:{args.port}", flush=True)

    binary = sumolib.checkBinary("sumo-gui" if args.gui else "sumo")
    cmd = [binary, "-c", args.sumocfg, "--seed", str(args.seed), "--no-step-log",
           "--no-warnings"]
    if args.gui:
        cmd += ["--start", "--gui-settings-file",
                str(REPO / "sumo" / "additional" / "pravah_busy_gui.xml")]
    traci.start(cmd)

    try:
        lane_index = build_lane_index()
        meta = build_meta(args.sumocfg, segment_map, seg_ref, args.rate, lane_index)
        bus.set_meta(meta)
        print(f"[telemetry] meta ready: {len(meta['junctions'])} junctions, "
              f"{len(meta['edges'])} edges, {len(meta['signals'])} signals", flush=True)

        if args.wait_for_client:
            print("[telemetry] waiting for a client...", flush=True)
            while bus.client_count == 0:
                time.sleep(0.2)

        step_length = traci.simulation.getDeltaT()
        steps_per_publish = max(1, round(args.rate / step_length))
        seq = 0
        step = 0
        teleports = 0
        started = time.time()

        while traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            teleports += traci.simulation.getStartingTeleportNumber()
            step += 1
            if step % steps_per_publish == 0:
                bus.broadcast(build_snapshot(seq, segment_map, teleports,
                                             args.include_vehicles, lane_index))
                seq += 1
                if seq % 60 == 0:
                    print(f"[telemetry] t={traci.simulation.getTime():.0f}s  "
                          f"snapshots={seq}  clients={bus.client_count}", flush=True)
            if args.realtime:
                target = started + traci.simulation.getTime()
                drift = target - time.time()
                if drift > 0:
                    time.sleep(drift)

        bus.broadcast({"schema": SCHEMA, "type": "end",
                       "sim_time_s": round(traci.simulation.getTime(), 1),
                       "snapshots_sent": seq,
                       "teleports_total": teleports})
        print(f"[telemetry] simulation finished, {seq} snapshots sent", flush=True)
    except KeyboardInterrupt:
        print("\n[telemetry] interrupted", flush=True)
    except Exception:
        traceback.print_exc()
    finally:
        try:
            traci.close()
        except Exception:
            pass
        bus.close()


def _local_ips():
    """Addresses another laptop could actually dial, for the console banner."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # No packet is sent by connect() on a UDP socket; this just asks the
        # routing table which local address would be used to reach the outside,
        # which is the one a machine on the LAN can reach back on.
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


if __name__ == "__main__":
    main()
