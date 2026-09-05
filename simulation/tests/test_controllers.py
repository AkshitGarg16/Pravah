"""Tests for the pure decision logic in src/pravah/sumo_twin/controllers.py.

A fake env stands in for PravahEnv here -- these controllers only ever call
env.tls_ids / get_phase_lane_pairs() / get_lane_queue(), so a fake exposing
just those three is enough to test decide_actions() deterministically,
without a real SUMO process.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pravah.sumo_twin.controllers import QueueBasedController


class FakeEnv:
    def __init__(self, phase_lane_pairs, queues):
        self.tls_ids = list(phase_lane_pairs.keys())
        self._phase_lane_pairs = phase_lane_pairs
        self._queues = queues

    def get_phase_lane_pairs(self, tls):
        return self._phase_lane_pairs[tls]

    def get_lane_queue(self, lane_id):
        return self._queues.get(lane_id, 0)


def test_queue_based_controller_picks_the_phase_with_the_most_queued_vehicles():
    env = FakeEnv(
        phase_lane_pairs={"13": {0: [("north_in", "south_out")], 1: [("east_in", "west_out")]}},
        queues={"north_in": 2, "east_in": 9},
    )
    controller = QueueBasedController(env)
    controller.reset(obs=None)
    assert controller.decide_actions(obs=None) == {"13": 1}  # east_in's queue (9) beats north_in's (2)


def test_queue_based_controller_keeps_favoring_the_still_busiest_approach():
    # "keep it green for most of the time" -- as long as the same approach
    # stays the busiest, repeated decide_actions() calls keep choosing it.
    env = FakeEnv(
        phase_lane_pairs={"13": {0: [("north_in", "south_out")], 1: [("east_in", "west_out")]}},
        queues={"north_in": 1, "east_in": 12},
    )
    controller = QueueBasedController(env)
    controller.reset(obs=None)
    for _ in range(5):
        assert controller.decide_actions(obs=None) == {"13": 1}


def test_queue_based_controller_switches_once_the_other_approach_becomes_busier():
    env = FakeEnv(
        phase_lane_pairs={"13": {0: [("north_in", "south_out")], 1: [("east_in", "west_out")]}},
        queues={"north_in": 10, "east_in": 2},
    )
    controller = QueueBasedController(env)
    controller.reset(obs=None)
    assert controller.decide_actions(obs=None) == {"13": 0}
    env._queues = {"north_in": 1, "east_in": 10}  # traffic shifts
    assert controller.decide_actions(obs=None) == {"13": 1}


def test_queue_based_controller_sums_multiple_lanes_per_phase():
    env = FakeEnv(
        phase_lane_pairs={"13": {0: [("a_in", "x_out"), ("b_in", "y_out")], 1: [("c_in", "z_out")]}},
        queues={"a_in": 3, "b_in": 3, "c_in": 5},  # phase 0's total (6) beats phase 1's single lane (5)
    )
    controller = QueueBasedController(env)
    controller.reset(obs=None)
    assert controller.decide_actions(obs=None) == {"13": 0}


def test_queue_based_controller_handles_multiple_tls_independently():
    env = FakeEnv(
        phase_lane_pairs={
            "13": {0: [("a_in", "a_out")], 1: [("b_in", "b_out")]},
            "57": {0: [("c_in", "c_out")], 1: [("d_in", "d_out")]},
        },
        queues={"a_in": 8, "b_in": 1, "c_in": 1, "d_in": 8},
    )
    controller = QueueBasedController(env)
    controller.reset(obs=None)
    assert controller.decide_actions(obs=None) == {"13": 0, "57": 1}
