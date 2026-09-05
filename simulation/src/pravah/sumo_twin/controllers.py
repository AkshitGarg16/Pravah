"""Signal-timing controllers that run against PravahEnv.

Both controllers share one interface -- reset(obs) then repeated
decide_actions(obs) -> {tls_id: green_slot} -- so a comparison harness can
run either one through the exact same env.step() loop with nothing else
different between runs (same network, same demand seed, same metrics
collection). That's what makes a "did the algorithm help" comparison
credible: every difference in the outcome comes from the controller, not
from incidental differences in how the two runs were driven.
"""


class FixedTimeController:
    """Baseline: reproduces the network's own default fixed-time program.

    Calling env.step({}) every control step is NOT the same as "normal"
    signal timing -- PravahEnv only switches a phase when explicitly told
    to (see env.py), so never sending an action would leave every light
    stuck on its first green for the entire run. That's not a fair
    baseline, it's a pathological one. This instead alternates each TLS
    through its green phases on a timer matching each phase's own
    programmed duration (what netconvert generated, e.g. 40s/40s on this
    network) -- the same fixed-time behavior the network would show
    without any controller at all, just running through PravahEnv's own
    action interface so the comparison is apples-to-apples.
    """

    def __init__(self, env):
        self.env = env

    def reset(self, obs):
        self._elapsed = {tls: 0.0 for tls in self.env.tls_ids}
        self._current_slot = {tls: obs[tls]["current_phase"] for tls in self.env.tls_ids}

    def decide_actions(self, obs):
        actions = {}
        for tls in self.env.tls_ids:
            self._elapsed[tls] += self.env.control_interval
            duration = self.env.get_phase_duration(tls, self._current_slot[tls])
            if self._elapsed[tls] >= duration:
                num_phases = obs[tls]["num_phases"]
                self._current_slot[tls] = (self._current_slot[tls] + 1) % num_phases
                self._elapsed[tls] = 0.0
            actions[tls] = self._current_slot[tls]
        return actions


class MaxPressureController:
    """Classic max-pressure: at each decision point, every TLS independently
    switches to whichever green phase currently has the highest "pressure" --
    the total queue length its incoming lanes would drain, minus the total
    queue length building up on the lanes it would send that traffic onto.
    Serving a phase that's backed up on the approach and clear downstream
    relieves the most congestion right now; a big queue feeding into an
    already-jammed downstream lane scores low even if its own approach is
    long, because serving it wouldn't actually help. No historical state,
    no fixed cycle -- just a queue-length snapshot each time it's asked.
    """

    def __init__(self, env):
        self.env = env

    def reset(self, obs):
        pass  # stateless: each decision only depends on the current observation

    def decide_actions(self, obs):
        actions = {}
        for tls in self.env.tls_ids:
            pairs_by_slot = self.env.get_phase_lane_pairs(tls)
            best_slot, best_pressure = 0, float("-inf")
            for slot, pairs in pairs_by_slot.items():
                pressure = sum(
                    self.env.get_lane_queue(in_lane) - self.env.get_lane_queue(out_lane)
                    for in_lane, out_lane in pairs
                )
                if pressure > best_pressure:
                    best_slot, best_pressure = slot, pressure
            actions[tls] = best_slot
        return actions


class QueueBasedController:
    """The literal rule this was asked to demonstrate: whichever approach
    currently has the most vehicles queued gets the green, held there for
    as long as it keeps being the busiest. Simpler and more directly
    explainable than MaxPressureController's queue-in-minus-queue-out
    pressure formula -- this only looks at how many cars are actually
    waiting on each phase's own approach, nothing about where they'd go
    next. Re-evaluated every control step, same as MaxPressureController:
    there's no separate "hold for N seconds" timer, a busy approach simply
    keeps winning this comparison round after round for as long as it
    stays the busiest, which is what "keep it green for most of the time"
    actually looks like in a re-decided-every-step controller -- and the
    same env.py end-of-phase lock (see env.py's lock_window) still applies
    underneath, so a switch away from a long-held green still can't be cut
    short in its last few seconds.
    """

    def __init__(self, env):
        self.env = env

    def reset(self, obs):
        pass  # stateless: each decision only depends on the current observation

    def decide_actions(self, obs):
        actions = {}
        for tls in self.env.tls_ids:
            pairs_by_slot = self.env.get_phase_lane_pairs(tls)
            best_slot, best_queue = 0, -1
            for slot, pairs in pairs_by_slot.items():
                queue = sum(self.env.get_lane_queue(in_lane) for in_lane, _out_lane in pairs)
                if queue > best_queue:
                    best_slot, best_queue = slot, queue
            actions[tls] = best_slot
        return actions
