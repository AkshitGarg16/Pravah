#!/usr/bin/env python3
"""Feeds the SUMO telemetry stream into the Pravah dashboard middleware.

`pravah_telemetry.py` speaks newline-delimited JSON over a raw TCP socket, one
large snapshot per publish. The dashboard middleware ingests individual records
over HTTP with different field names (`speed` not `mean_speed_ms`, `halting` not
`halting_vehicles`, `free_flow` not `speed_limit_ms`). This translates between
them and POSTs to /api/ingest.

Ids are already canonical on both sides -- `export_network.py` builds the
dashboard's topology from the same `pravah.net.xml` -- so an edge only needs its
`S:sumo:` prefix, and the middleware's crosswalk never has to snap anything by
coordinate.

Two things worth knowing about the translation:

  * The telemetry omits edges with no vehicles, keeping its payload proportional
    to activity. The middleware would read that omission as "no data" and grey
    the road out, so empty edges are filled in here at their free-flow speed --
    which is what an empty road *is*.

  * Signal state is sent as a {segment id -> colour} map rather than SUMO's
    redYellowGreenState string. The string is indexed by link index, and
    recovering which approach each character belongs to needs the controlled-link
    order; the telemetry already did that work per approach, so passing the
    result avoids re-deriving it and getting it wrong.

Nothing here imports SUMO, so it can run next to the simulation or beside the
dashboard, whichever side of the network you want the TCP hop on.

    gui_data/pravah_bridge.py                                   # all local
    gui_data/pravah_bridge.py --feed-host 192.168.0.149 \\
                              --api http://192.168.0.42:8000    # split machines
    gui_data/pravah_bridge.py --replay feed.jsonl               # no SUMO needed
"""

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from telemetry_client import stream_messages  # noqa: E402

# SUMO's redYellowGreenState characters. 'G' is a protected green and 'g' a
# permissive one -- both are green to a driver, so both are green here.
PHASE_OF_CHAR = {"G": "green", "g": "green", "y": "amber", "Y": "amber",
                 "r": "red", "R": "red", "o": "off", "O": "off", "u": "amber"}
PHASE_RANK = {"green": 3, "amber": 2, "red": 1, "off": 0}

HEAVY_TYPES = ("bus", "truck")


def seg_id(edge_id):
    return f"S:sumo:{edge_id}"


def jun_id(junction_id):
    return f"J:sumo:{junction_id}"


def occupancy_fraction(value):
    """TraCI reports lane occupancy as a fraction; be defensive about percent.

    The middleware weights occupancy at 0.65 when scoring pressure and clamps at
    1.0, so a value on a 0-100 scale would peg every road to maximum pressure
    and silently flatten the map.
    """
    if value is None:
        return None
    v = float(value)
    return v / 100.0 if v > 1.0 else v


class Translator:
    """Turns one telemetry snapshot into middleware ingest records."""

    def __init__(self):
        self.edges = {}      # edge_id -> free-flow speed and lane count, from meta
        self.scenario = None

    def load_meta(self, meta):
        self.scenario = meta.get("scenario")
        self.edges = {
            e["edge_id"]: {
                "free_flow_ms": e.get("speed_limit_ms") or 0.0,
                "lanes": e.get("lane_count") or 1,
            }
            for e in meta.get("edges", [])
        }

    def segments(self, snapshot, ts):
        records = []
        seen = set()
        for s in snapshot.get("segments", []):
            eid = s["edge_id"]
            seen.add(eid)
            records.append({
                "type": "segment",
                "id": seg_id(eid),
                "ts": ts,
                "speed": s["mean_speed_ms"],
                "free_flow": s["speed_limit_ms"],
                "occupancy": occupancy_fraction(s.get("occupancy")),
                "halting": s.get("halting_vehicles"),
                "count": s.get("vehicles"),
            })
        # Edges the snapshot left out carry no vehicles, so they are running at
        # the limit. Reporting that keeps the whole map coloured and keeps
        # "no-data" meaning the feed is down rather than the road is quiet.
        for eid, meta in self.edges.items():
            if eid in seen:
                continue
            records.append({
                "type": "segment",
                "id": seg_id(eid),
                "ts": ts,
                "speed": meta["free_flow_ms"],
                "free_flow": meta["free_flow_ms"],
                "occupancy": 0.0,
                "halting": 0,
                "count": 0,
            })
        return records

    def junctions(self, snapshot, ts):
        records = []
        for j in snapshot.get("junctions", []):
            tl = j["traffic_light"]
            totals = j["totals"]

            # Several lanes of one edge can be controlled separately; the edge
            # shows the most permissive of them, which is what a driver
            # approaching it can actually do.
            phases = {}
            for a in j["approaches"]:
                colour = "red"
                for ch in a.get("signal_chars", []):
                    cand = PHASE_OF_CHAR.get(ch, "red")
                    if PHASE_RANK.get(cand, 0) > PHASE_RANK.get(colour, 0):
                        colour = cand
                key = seg_id(a["edge_id"])
                if PHASE_RANK.get(colour, 0) > PHASE_RANK.get(phases.get(key, "off"), 0):
                    phases[key] = colour

            types = totals.get("vehicle_types", {})
            records.append({
                "type": "junction",
                "id": jun_id(j["junction_id"]),
                "ts": ts,
                "phases": phases,
                "time_to_change": tl.get("seconds_to_switch"),
                "program": tl.get("program_id"),
                "hv_count": sum(types.get(t, 0) for t in HEAVY_TYPES),
                "ev": False,
                "metrics": {
                    **totals,
                    "phase_index": tl.get("phase_index"),
                    "state": tl.get("state"),
                    "phase_name": tl.get("phase_name"),
                    "is_green_phase": tl.get("is_green_phase"),
                    "time_in_phase_s": tl.get("time_in_phase_s"),
                    "phase_duration_s": tl.get("phase_duration_s"),
                    "seconds_to_switch": tl.get("seconds_to_switch"),
                },
                "approaches": [{
                    "lane_id": a["lane_id"],
                    "segment": seg_id(a["edge_id"]),
                    "segment_ids": a.get("segment_ids", []),
                    "phase": phases.get(seg_id(a["edge_id"]), "red"),
                    "vehicles": a["vehicles"],
                    "halting_vehicles": a["halting_vehicles"],
                    "queue_length_m": a["queue_length_m"],
                    "queue_ratio": a["queue_ratio"],
                    "mean_speed_ms": a["mean_speed_ms"],
                    "speed_ratio": a["speed_ratio"],
                    "congestion": a["congestion"],
                    "waiting_time_s": a["waiting_time_s"],
                    "vehicle_types": a["vehicle_types"],
                } for a in j["approaches"]],
            })
        return records

    def network(self, snapshot, ts):
        return {
            "type": "network",
            "ts": ts,
            "scenario": self.scenario,
            "seq": snapshot.get("seq"),
            "sim_time_s": snapshot.get("sim_time_s"),
            **snapshot.get("network", {}),
        }

    def envelope(self, snapshot):
        # Wall-clock, not sim time: the middleware ages observations against
        # time.time() to decide what is stale.
        ts = time.time()
        # A list of two envelopes -- iter_messages() fans out the first into its
        # segments and junctions, and passes the second through as one record.
        return [
            {"source": "sumo", "units": "mps", "ts": ts,
             "segments": self.segments(snapshot, ts),
             "junctions": self.junctions(snapshot, ts)},
            {"source": "sumo", **self.network(snapshot, ts)},
        ]


def post(api, payload, timeout=5.0):
    req = urllib.request.Request(
        f"{api.rstrip('/')}/api/ingest",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def messages(args):
    """Snapshots, from either the live socket or a recorded file."""
    if args.replay:
        for line in Path(args.replay).read_text().splitlines():
            if line.strip():
                yield json.loads(line)
        return
    yield from stream_messages(args.feed_host, args.feed_port)


def run(args):
    translator = Translator()
    recorder = open(args.record, "w", encoding="utf-8") if args.record else None
    sent = 0
    accepted_total = 0
    api_down_since = None

    try:
        for msg in messages(args):
            if recorder:
                recorder.write(json.dumps(msg) + "\n")
                recorder.flush()

            kind = msg.get("type")
            if kind == "meta":
                translator.load_meta(msg)
                print(f"[bridge] meta: {len(translator.edges)} edges, "
                      f"scenario {str(translator.scenario).split('/')[-1]}", flush=True)
                continue
            if kind == "end":
                print(f"[bridge] simulation ended at t={msg.get('sim_time_s')}s", flush=True)
                return
            if kind != "snapshot":
                continue

            try:
                result = post(args.api, translator.envelope(msg))
            except (urllib.error.URLError, socket.timeout, OSError) as exc:
                # The dashboard being down must not take the bridge with it:
                # the simulation keeps running and reconnecting costs nothing.
                if api_down_since is None:
                    api_down_since = time.time()
                    print(f"[bridge] {args.api} unreachable ({exc}); still trying", flush=True)
                continue
            if api_down_since is not None:
                print(f"[bridge] {args.api} back after "
                      f"{time.time() - api_down_since:.0f}s", flush=True)
                api_down_since = None

            sent += 1
            accepted_total += result.get("accepted", 0)
            if sent == 1 or sent % 30 == 0:
                print(f"[bridge] t={msg.get('sim_time_s')}s  snapshots={sent}  "
                      f"accepted={result.get('accepted')}  "
                      f"rejected={result.get('rejected')}", flush=True)
            if args.replay and args.replay_interval:
                time.sleep(args.replay_interval)
    finally:
        if recorder:
            recorder.close()
        print(f"\n[bridge] forwarded {sent} snapshots, {accepted_total} records accepted",
              flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feed-host", default="127.0.0.1", help="host running pravah_telemetry.py")
    ap.add_argument("--feed-port", type=int, default=5555)
    ap.add_argument("--api", default="http://127.0.0.1:8000", help="middleware base URL")
    ap.add_argument("--retry", action="store_true",
                    help="keep reconnecting to the telemetry feed")
    ap.add_argument("--record", default=None, help="save the raw telemetry to this .jsonl")
    ap.add_argument("--replay", default=None,
                    help="forward a recorded .jsonl instead of connecting to SUMO")
    ap.add_argument("--replay-interval", type=float, default=1.0,
                    help="seconds between replayed snapshots")
    args = ap.parse_args()

    while True:
        try:
            run(args)
            return 0
        except KeyboardInterrupt:
            print("\n[bridge] interrupted", flush=True)
            return 0
        except (ConnectionRefusedError, ConnectionResetError, socket.timeout, OSError) as exc:
            if not args.retry:
                print(f"[bridge] telemetry feed unavailable: {exc}", file=sys.stderr)
                return 1
            print(f"[bridge] telemetry feed unavailable ({exc}); retrying in 2s",
                  file=sys.stderr, flush=True)
            time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
