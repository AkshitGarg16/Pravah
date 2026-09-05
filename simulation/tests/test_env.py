"""Tests for the PravahEnv TraCI wrapper (src/pravah/sumo_twin/env.py).

The pure-logic test below needs no SUMO. The integration test drives the
real network/sumocfg built in earlier passes and is skipped automatically
if $SUMO_HOME isn't set -- same convention the rest of this project follows
(see README.txt section 6), since every script here needs
    export SUMO_HOME=/home/kreacher/sumo-src
    export PATH=$SUMO_HOME/bin:$PATH
before it can find the sumo binary/tools at all.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pravah.sumo_twin.env import _green_phase_indices

REPO_ROOT = Path(__file__).resolve().parent.parent
SUMOCFG = REPO_ROOT / "sumo" / "pravah.sumocfg"

needs_sumo = pytest.mark.skipif(
    not os.environ.get("SUMO_HOME"),
    reason="SUMO_HOME not set -- export SUMO_HOME=/home/kreacher/sumo-src (see README.txt) to run this test",
)


def _phase(state, duration=1.0):
    return SimpleNamespace(state=state, duration=duration)


def test_green_phase_indices_skips_yellow_phases():
    # Same 4-phase shape as the real network's TLS programs: green, yellow, green, yellow.
    logic = SimpleNamespace(phases=[_phase("GGrr"), _phase("yyrr"), _phase("rrGG"), _phase("rryy")])
    assert _green_phase_indices(logic) == [0, 2]


def test_green_phase_indices_handles_mixed_case_and_all_red():
    logic = SimpleNamespace(phases=[_phase("GgGg"), _phase("yYrr"), _phase("rrrr")])
    # phase 2 is all-red (no y) -- by this convention it counts as "green" (controllable),
    # which is correct: an all-red clearance phase is still a safe, nameable hold state.
    assert _green_phase_indices(logic) == [0, 2]


@needs_sumo
def test_env_reset_and_step_against_real_network():
    from pravah.sumo_twin.env import PravahEnv

    env = PravahEnv(str(SUMOCFG), control_interval=10, sim_end=60)
    try:
        obs, info = env.reset(seed=1)
        assert set(obs.keys()) == {"13", "57", "309", "cluster_2_72"}  # cluster_2_72 = the real ITO junction
        for tls_obs in obs.values():
            assert tls_obs["num_phases"] == 2
            assert tls_obs["current_phase"] in (0, 1)
            assert tls_obs["lanes"]  # at least one controlled lane per TLS

        # Holding (no actions) must advance by exactly one control interval.
        obs, info, done = env.step({})
        assert info["elapsed_s"] == 10.0

        # Forcing every TLS to its other green must cost interval + yellow.
        actions = {tls: 1 - obs[tls]["current_phase"] for tls in obs}
        obs, info, done = env.step(actions)
        assert info["elapsed_s"] > 10.0
        for tls, requested_slot in actions.items():
            assert obs[tls]["current_phase"] == requested_slot

        # traci.load()-based reset must not raise and must return a fresh observation.
        obs, info = env.reset(seed=2)
        assert set(obs.keys()) == {"13", "57", "309", "cluster_2_72"}  # cluster_2_72 = the real ITO junction

        # inject_vehicle() is the live-demand hook a detection feed will eventually
        # drive -- confirm it actually places a vehicle on the requested edge.
        import traci
        edge_id = [e for e in traci.edge.getIDList() if not e.startswith(":")][0]
        vid = env.inject_vehicle(edge_id)
        traci.simulationStep()
        assert vid in traci.vehicle.getIDList()
        assert traci.vehicle.getRoadID(vid) == edge_id

        # auto-generated ids must not collide across repeated calls.
        vid2 = env.inject_vehicle(edge_id)
        assert vid2 != vid
    finally:
        env.close()


@needs_sumo
def test_end_of_phase_lock_defers_then_applies_the_change():
    from pravah.sumo_twin.env import PravahEnv

    # TLS "13" has a clean 40s green / 10s control_interval, so the lock
    # boundary lands exactly on a step -- no off-by-one guessing needed.
    env = PravahEnv(str(SUMOCFG), control_interval=10, sim_end=200, lock_window=10)
    try:
        obs, info = env.reset(seed=1)
        assert obs["13"]["current_phase"] == 0

        for _ in range(3):  # phase_elapsed: 10, 20, 30 -- all well outside the lock window
            obs, info, done = env.step({})
        assert obs["13"]["current_phase"] == 0

        # Requested with exactly 10s left (40 - 30) -- inside the lock window, must be held.
        obs, info, done = env.step({"13": 1})
        assert obs["13"]["current_phase"] == 0, "a request in the last 10s must not cut the phase short"
        assert info["elapsed_s"] == 10.0, "a deferred request costs no extra time -- it's just a normal hold"
        assert info["phase_changes"] == {}

        # Phase has now run its full 40s -- the deferred request must fire on its own,
        # with no fresh action given this step.
        obs, info, done = env.step({})
        assert obs["13"]["current_phase"] == 1
        assert info["elapsed_s"] > 10.0, "applying the deferred change must still insert the mandatory yellow"
        assert info["phase_changes"]["13"] == {"from_slot": 0, "to_slot": 1}
    finally:
        env.close()


@needs_sumo
def test_request_outside_the_lock_window_applies_immediately():
    from pravah.sumo_twin.env import PravahEnv

    env = PravahEnv(str(SUMOCFG), control_interval=10, sim_end=200, lock_window=10)
    try:
        obs, info = env.reset(seed=1)
        obs, info, done = env.step({})  # phase_elapsed=10, remaining=30 -- well outside the lock window
        obs, info, done = env.step({"13": 1})
        assert obs["13"]["current_phase"] == 1, "plenty of time left -- should switch on this same step"
        assert info["phase_changes"]["13"] == {"from_slot": 0, "to_slot": 1}
    finally:
        env.close()


@needs_sumo
def test_gui_mode_autostarts_and_autoquits_sumo_gui():
    # Without --start/--quit-on-end, sumo-gui launches and sits on an
    # unclicked play button forever -- confirmed this is the exact problem
    # run_gazebo_twin.py's own --sumo-gui handling already had to solve
    # (see chat); PravahEnv needs the same fix for a controller-driven run.
    from pravah.sumo_twin.env import PravahEnv

    env = PravahEnv(str(SUMOCFG), use_gui=True)
    args = env._base_args(seed=1)
    assert "--start" in args
    assert "--quit-on-end" in args


@needs_sumo
def test_non_gui_mode_does_not_add_gui_only_flags():
    from pravah.sumo_twin.env import PravahEnv

    env = PravahEnv(str(SUMOCFG), use_gui=False)
    args = env._base_args(seed=1)
    assert "--start" not in args
    assert "--quit-on-end" not in args


@needs_sumo
def test_default_step_length_is_one_second():
    # SUMO's own default -- confirmed this is what made both sumo-gui and
    # Gazebo look jittery: a live viewer only gets a new vehicle position
    # once per simulated second, nothing interpolating in between.
    from pravah.sumo_twin.env import PravahEnv

    env = PravahEnv(str(SUMOCFG))
    assert env.step_length == 1.0
    assert "--step-length" in env._base_args(seed=1)
    assert env._base_args(seed=1)[env._base_args(seed=1).index("--step-length") + 1] == "1.0"


@needs_sumo
def test_finer_step_length_calls_on_substep_the_right_number_of_times():
    from pravah.sumo_twin.env import PravahEnv

    env = PravahEnv(str(SUMOCFG), control_interval=1, sim_end=60, step_length=0.2)
    try:
        assert "0.2" in env._base_args(seed=1)
        obs, info = env.reset(seed=1)
        calls = []
        obs, info, done = env.step({}, on_substep=lambda: calls.append(1))
        # control_interval=1 second at step_length=0.2 -> 5 real simulationStep() calls
        assert len(calls) == 5
        assert info["elapsed_s"] == 1.0  # elapsed is still tracked in seconds, unaffected by step_length
    finally:
        env.close()
