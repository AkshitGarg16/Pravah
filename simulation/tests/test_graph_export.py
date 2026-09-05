import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pravah.sumo_twin.graph_export import SignalEventLogger

REPO_ROOT = Path(__file__).resolve().parent.parent

needs_sumo = pytest.mark.skipif(
    not os.environ.get("SUMO_HOME"),
    reason="SUMO_HOME not set -- export SUMO_HOME=/home/kreacher/sumo-src (see README.txt) to run this test",
)


def test_signal_event_logger_writes_one_line_per_change(tmp_path):
    out_path = tmp_path / "events.jsonl"
    env = SimpleNamespace(t=12.5)

    with SignalEventLogger(out_path, triggering_controller="test") as logger:
        logger.log_step(env, {"phase_changes": {"13": {"from_slot": 0, "to_slot": 1}}})
        env.t = 62.5
        logger.log_step(env, {"phase_changes": {}})  # a no-change step must write nothing
        logger.log_step(env, {
            "phase_changes": {
                "13": {"from_slot": 1, "to_slot": 0},
                "cluster_2_72": {"from_slot": 0, "to_slot": 1},
            }
        })

    lines = [json.loads(line) for line in out_path.read_text().splitlines()]
    assert len(lines) == 3  # 1 + 0 + 2

    assert lines[0] == {"t": 12.5, "tls_id": "13", "from_slot": 0, "to_slot": 1, "triggering_controller": "test"}
    tls_ids_in_third_step = {rec["tls_id"] for rec in lines[1:]}
    assert tls_ids_in_third_step == {"13", "cluster_2_72"}
    for rec in lines[1:]:
        assert rec["t"] == 62.5


def test_signal_event_logger_creates_parent_dirs(tmp_path):
    out_path = tmp_path / "nested" / "dir" / "events.jsonl"
    with SignalEventLogger(out_path) as logger:
        logger.log_step(SimpleNamespace(t=0.0), {"phase_changes": {}})
    assert out_path.exists()


@needs_sumo
def test_segment_node_state_against_the_real_network():
    sys.path.insert(0, os.path.join(os.environ["SUMO_HOME"], "tools"))
    import traci
    import sumolib

    from pravah.sumo_twin.env import PravahEnv
    from pravah.sumo_twin.network_builder import load_segments
    from pravah.sumo_twin.graph_export import segment_node_state

    edge_to_segments = json.load(open(REPO_ROOT / "sumo/network/pravah.net.segment_map.json"))
    speed_limits = {sid: float(seg["speed_limit"]) for sid, seg in
                    load_segments(REPO_ROOT / "data/pravah_900to1000_balanced.csv").items()}

    env = PravahEnv(str(REPO_ROOT / "sumo/pravah.sumocfg"), control_interval=10, sim_end=60)
    try:
        env.reset(seed=1)
        env.step({})
        state = segment_node_state(env, edge_to_segments, speed_limits)

        assert state  # non-empty -- some segments should have live state after a real step
        sample = next(iter(state.values()))
        assert set(sample.keys()) == {"avg_speed_kmh", "congestion_ratio", "pressure", "timestamp"}
        assert sample["pressure"] is None  # explicit not-yet-implemented hook, not fabricated
        assert sample["timestamp"] == env.t

        for rec in state.values():
            assert rec["avg_speed_kmh"] >= 0
            if rec["congestion_ratio"] is not None:
                assert 0.0 <= rec["congestion_ratio"] <= 1.0
    finally:
        env.close()
