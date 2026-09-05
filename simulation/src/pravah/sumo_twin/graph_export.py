"""Exports the simulation's live state in PRAVAH's own graph vocabulary
(segments are the data-bearing nodes, junctions are edges/connectors), and
logs every real signal-phase change PravahEnv performs.

SUMO itself is never re-modeled to match this vocabulary internally -- the
simulation keeps using SUMO's own edge/junction model throughout. This
module only translates its live TraCI state back into the shape the
project's own architecture describes, for anything downstream (a GUI, a
future RL/GNN layer) that expects that shape. The exact interface such a
consumer needs is not yet decided (see the plan) -- these are the two
concrete, self-contained pieces that are decided: what a segment "node"
looks like, and what format a signal change is "given out in".
"""

import json
from pathlib import Path


def segment_node_state(env, edge_to_segments, speed_limits_kmh):
    """{segment_id: {avg_speed_kmh, congestion_ratio, pressure, timestamp}}
    rolled up from TraCI's live edge-level speed via the edge_id ->
    [segment_id, ...] map (segment_map.py's build_edge_to_segments output,
    or the saved pravah.net.segment_map.json). An edge born from merging
    several original segments (see segment_map.py) reports the same live
    state for all of them -- they share one simulated traffic state now.

    congestion_ratio = 1 - live_speed / speed_limit, clamped to [0, 1]
    (free-flow-or-faster reads as 0, a dead stop as 1). None when a
    segment's speed_limit isn't known. `pressure` is always None here --
    an explicit hook for a future max-pressure-style per-segment metric,
    not a fabricated number.
    """
    import traci  # local: this module doesn't need a live SUMO connection except when actually called

    t = env.t
    out = {}
    for edge_id, seg_ids in edge_to_segments.items():
        if not seg_ids or edge_id.startswith(":"):
            continue
        speed_ms = traci.edge.getLastStepMeanSpeed(edge_id)
        if speed_ms < 0:
            continue
        speed_kmh = speed_ms * 3.6
        for seg_id in seg_ids:
            limit = speed_limits_kmh.get(seg_id)
            ratio = max(0.0, min(1.0, 1 - speed_kmh / limit)) if limit else None
            out[seg_id] = {
                "avg_speed_kmh": round(speed_kmh, 2),
                "congestion_ratio": round(ratio, 3) if ratio is not None else None,
                "pressure": None,
                "timestamp": t,
            }
    return out


class SignalEventLogger:
    """Writes one JSON line per real signal-phase change PravahEnv performs.

    Reads env.step()'s own info["phase_changes"] each call rather than
    independently tracking or guessing what changed -- PravahEnv is the
    only thing that actually knows when a change was applied (including
    ones the 10s end-of-phase lock deferred and then auto-applied later).
    """

    def __init__(self, out_path, triggering_controller="unknown"):
        self.out_path = Path(out_path)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.triggering_controller = triggering_controller
        self._fh = open(self.out_path, "w")

    def log_step(self, env, info):
        for tls_id, change in info.get("phase_changes", {}).items():
            record = {
                "t": env.t,
                "tls_id": tls_id,
                "from_slot": change["from_slot"],
                "to_slot": change["to_slot"],
                "triggering_controller": self.triggering_controller,
            }
            self._fh.write(json.dumps(record) + "\n")
        self._fh.flush()

    def close(self):
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
