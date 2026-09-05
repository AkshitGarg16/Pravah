"""TraCI environment wrapper for the PRAVAH digital twin.

This is the interface Max-Pressure, and later the GNN-RL controller, run
against. It does not implement any control policy or reward function --
that's the controller's job. It only owns the three things every controller
needs from the simulation, and that today's run_digital_twin.py script
doesn't provide:

  1. A stable per-control-step OBSERVATION: queue length, occupancy,
     waiting time and current signal phase per traffic light, not just the
     network-wide mean speed run_digital_twin.py samples.
  2. A SAFE action interface for switching signal phases. A caller picks
     among a junction's GREEN phases only (index 0, 1, ...) -- it can never
     name a yellow/transition phase directly. If the requested green phase
     differs from the current one, this wrapper inserts the mandatory
     yellow transition itself and holds it for its real programmed
     duration, exactly like a real signal controller would, before
     switching. Skipping straight from one green phase to another is not a
     simulation shortcut, it's an invalid (unsafe) signal plan, so the
     wrapper doesn't expose a way to do it.
  3. Fast EPISODIC RESET via traci.load(), not a fresh traci.start() /
     process restart per episode. RL training means thousands of resets;
     reloading state in an already-running SUMO instance is far cheaper
     than tearing down and relaunching the process each time.

No reward is computed here on purpose. Raw, reward-relevant metrics (queue
lengths, waiting time, throughput, teleports) come back in the `info` dict
from step() so a controller/RL trainer can define its own reward -- that
choice belongs to whoever builds the controller, not to the simulation.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.environ.get("SUMO_HOME", ""), "tools"))
try:
    import traci
    import sumolib
except ImportError:
    # Deferred on purpose: only PravahEnv.__init__ actually needs these.
    # Keeping the module itself importable without $SUMO_HOME set means
    # _green_phase_indices (pure logic, no SUMO dependency at all) stays
    # unit-testable on its own, instead of an unrelated missing-traci error
    # blocking pytest from even collecting tests that don't need it.
    traci = None
    sumolib = None


def _green_phase_indices(logic):
    """Indices of a program's real (controllable) phases.

    SUMO's convention -- also relied on by the sumo-rl library, so this
    isn't a house rule invented here -- is that a phase whose state string
    contains no 'y' is a genuine green phase a controller may hold; any
    phase containing 'y' is a yellow/transition-only phase that must never
    be a controller's target, only something passed through automatically
    on the way between two green phases.
    """
    return [i for i, p in enumerate(logic.phases) if "y" not in p.state.lower()]


class PravahEnv:
    """Thin, controller-agnostic wrapper around one TraCI connection.

    Usage:
        env = PravahEnv("sumo/pravah.sumocfg", control_interval=10)
        obs, info = env.reset(seed=1)
        for _ in range(360):
            obs, info, done = env.step({"13": 1, "309": 0})  # or {} to hold
            if done:
                obs, info = env.reset(seed=2)  # traci.load(), not a restart
        env.close()
    """

    def __init__(self, sumocfg, control_interval=10, sim_end=3600, use_gui=False, lock_window=10, step_length=1.0):
        if traci is None or sumolib is None:
            raise RuntimeError(
                "traci/sumolib not importable -- set $SUMO_HOME to your SUMO "
                "checkout/install before constructing PravahEnv, e.g.\n"
                "  export SUMO_HOME=/home/kreacher/sumo-src\n"
                "  export PATH=$SUMO_HOME/bin:$PATH\n"
                "(see README.txt section 6)."
            )
        self.sumocfg = str(sumocfg)
        self.control_interval = control_interval
        self.sim_end = sim_end
        self.lock_window = lock_window  # seconds before a green's programmed end during which it can't be cut short
        self._use_gui = use_gui
        self._binary = sumolib.checkBinary("sumo-gui" if use_gui else "sumo")
        # SUMO's own default step-length is 1.0 simulated second per
        # traci.simulationStep() call -- fine for training/metrics, but a
        # live viewer (Gazebo bridge or sumo-gui itself) only gets a new
        # vehicle position once per that same interval, with nothing
        # interpolating in between, so motion reads as discrete hops
        # rather than continuous movement (confirmed: this is exactly what
        # made both sumo-gui and Gazebo look jittery at the default,
        # unset step-length -- see chat). step_length<1.0 makes SUMO
        # itself advance in finer increments, which is what every
        # simulationStep()-counting loop below needs to stay aware of --
        # "N seconds" is no longer "N simulationStep() calls" once this
        # isn't 1.0. Pick a step_length whose reciprocal is a whole number
        # (0.1, 0.2, 0.25, 0.5) so _steps_per_second is exact.
        self.step_length = step_length
        self._steps_per_second = round(1.0 / step_length)
        self._started = False
        self._tls_ids = []
        self._green_phases = {}   # tls_id -> [phase indices that are real greens]
        self._yellow_after = {}   # tls_id -> {green_phase_index: yellow_phase_index}
        self._controlled_lanes = {}  # tls_id -> [unique controlled lane ids]
        self._phase_lane_pairs = {}  # tls_id -> {green_slot: [(in_lane, out_lane), ...]}
        self._phase_elapsed = {}  # tls_id -> seconds the current green has been running
        self._deferred = {}       # tls_id -> a requested green_slot held back by the lock window
        self._injected_count = 0    # for auto-generated inject_vehicle() ids

    def _base_args(self, seed):
        args = ["-c", self.sumocfg, "--end", str(self.sim_end), "--step-length", str(self.step_length)]
        if seed is not None:
            args += ["--seed", str(seed)]
        if self._use_gui:
            # Without these, sumo-gui launches and just sits on an unclicked
            # play button forever -- run_gazebo_twin.py's own raw-TraCI
            # passthrough path already had to solve this exact problem (see
            # its --sumo-gui handling); PravahEnv needs the same fix so
            # use_gui=True works for a controller-driven run too. --start:
            # begin immediately once TraCI steps arrive. --quit-on-end:
            # close the window itself when TraCI disconnects, rather than
            # leaving a dead simulation open.
            args += ["--start", "--quit-on-end"]
        return args

    def reset(self, seed=None):
        if not self._started:
            traci.start([self._binary] + self._base_args(seed))
            self._started = True
        else:
            traci.load(self._base_args(seed))

        # (Re)discover TLS structure every reset -- cheap, and avoids ever
        # trusting stale phase-index assumptions if the network changes.
        self._tls_ids = list(traci.trafficlight.getIDList())
        self._green_phases = {}
        self._yellow_after = {}
        self._controlled_lanes = {}
        self._phase_lane_pairs = {}
        for tls in self._tls_ids:
            logic = traci.trafficlight.getAllProgramLogics(tls)[0]
            greens = _green_phase_indices(logic)
            self._green_phases[tls] = greens
            n = len(logic.phases)
            # The phase right after a green, in program order, is that
            # green's mandatory transition -- true for netconvert's default
            # simple (green, yellow, green, yellow, ...) ring, which is what
            # every TLS in this network currently has.
            self._yellow_after[tls] = {g: (g + 1) % n for g in greens}
            self._controlled_lanes[tls] = sorted(set(traci.trafficlight.getControlledLanes(tls)))

            links = traci.trafficlight.getControlledLinks(tls)
            self._phase_lane_pairs[tls] = {
                slot: [
                    (in_lane, out_lane)
                    for link_idx, ch in enumerate(logic.phases[raw_idx].state)
                    if ch in "Gg"
                    for (in_lane, out_lane, _via_lane) in links[link_idx]
                ]
                for slot, raw_idx in enumerate(greens)
            }

        self._t = 0
        self._injected_count = 0
        self._phase_elapsed = {tls: 0.0 for tls in self._tls_ids}
        self._deferred = {}
        return self._get_observation(), self._empty_info()

    def _current_green_slot(self, tls):
        """Which green (0, 1, ...) the TLS is showing right now, not the raw SUMO phase index."""
        raw = traci.trafficlight.getPhase(tls)
        return self._green_phases[tls].index(raw)

    def _resolve_requested_slot(self, tls, requested_slot):
        """Applies the end-of-phase lock: a request arriving with
        lock_window seconds or less left in the current green is held
        until that phase finishes its full programmed run, then applied --
        it never gets cut short in its final stretch. A request with more
        time left, or one arriving after the phase has already run its
        full course, is applied right away. Matches the confirmed reading
        of "the last 10 seconds are non-variable": locked at the end, not
        a minimum-green-time-from-the-start rule.
        """
        if requested_slot is not None:
            self._deferred[tls] = requested_slot

        pending = self._deferred.get(tls)
        current_slot = self._current_green_slot(tls)
        if pending is None or pending == current_slot:
            self._deferred.pop(tls, None)
            return None

        duration = self.get_phase_duration(tls, current_slot)
        remaining_in_phase = duration - self._phase_elapsed.get(tls, 0.0)
        if remaining_in_phase > self.lock_window or remaining_in_phase <= 0:
            self._deferred.pop(tls, None)
            return pending
        return None  # inside the lock window -- keep holding, request stays deferred

    def step(self, actions=None, on_substep=None):
        """actions: {tls_id: green_slot_index} for TLS to (re)set; omit a TLS to hold its current green.

        A request that arrives inside a phase's last `lock_window` seconds
        is deferred and applied automatically once that phase completes,
        even on a later step() call where no fresh action is given for
        that TLS -- see _resolve_requested_slot.

        on_substep: optional no-arg callback invoked after every individual
        traci.simulationStep() this call makes (there can be several --
        the mandatory yellow hold, then the rest of control_interval --
        especially once step_length<1.0 means "control_interval seconds"
        is itself several sub-steps). This is the hook a live viewer needs
        to sync Gazebo/pace playback at the real sub-second granularity
        step_length provides, instead of only once per whole step() call
        (which would silently put back the exact 1Hz choppiness step_length
        was meant to fix, just one level up).
        """
        actions = actions or {}
        elapsed = 0.0
        arrived = 0
        teleported = 0

        resolved_actions = {}
        for tls in set(actions) | set(self._deferred):
            target_slot = self._resolve_requested_slot(tls, actions.get(tls))
            if target_slot is not None:
                resolved_actions[tls] = target_slot

        # -- apply any requested phase changes, inserting yellow first --
        transitioning = {}  # tls -> (target_raw_phase, yellow_duration, from_slot, to_slot)
        for tls, slot in resolved_actions.items():
            greens = self._green_phases[tls]
            target_raw = greens[slot]
            from_slot = self._current_green_slot(tls)
            current_raw = greens[from_slot]
            if target_raw != current_raw:
                yellow_raw = self._yellow_after[tls][current_raw]
                yellow_duration = traci.trafficlight.getAllProgramLogics(tls)[0].phases[yellow_raw].duration
                traci.trafficlight.setPhase(tls, yellow_raw)
                transitioning[tls] = (target_raw, yellow_duration, from_slot, slot)

        if transitioning:
            # Hold every transitioning TLS on yellow for the longest of their
            # programmed yellow durations -- if two lights need different
            # yellow lengths, the shorter one just gets a couple of extra
            # safe seconds of yellow rather than switching on a mismatched
            # clock; never less than its programmed duration.
            yellow_span = max(duration for _, duration, _from, _to in transitioning.values())
            for _ in range(round(yellow_span * self._steps_per_second)):
                traci.simulationStep()
                arrived += traci.simulation.getArrivedNumber()
                teleported += traci.simulation.getStartingTeleportNumber()
                if on_substep:
                    on_substep()
            elapsed += yellow_span
            for tls, (target_raw, _duration, _from, _to) in transitioning.items():
                traci.trafficlight.setPhase(tls, target_raw)

        # -- hold whatever is now current for the rest of the control interval --
        # Duration is set *longer* than what we actually step, not equal to
        # it: setting it to exactly `remaining` risked SUMO's own phase
        # timer expiring right at the last simulationStep() of this loop
        # (an off-by-one on when the countdown is evaluated), auto-advancing
        # into a yellow phase before control ever returned here -- harmless
        # with FixedTimeController (which rarely switches, so this rarely
        # mattered) but broke immediately under MaxPressureController, which
        # can request a different phase on nearly every control step. Every
        # step re-arms this before the margin could ever actually be used.
        remaining = self.control_interval
        for tls in self._tls_ids:
            traci.trafficlight.setPhaseDuration(tls, remaining + 5)
        for _ in range(round(remaining * self._steps_per_second)):
            traci.simulationStep()
            arrived += traci.simulation.getArrivedNumber()
            teleported += traci.simulation.getStartingTeleportNumber()
            if on_substep:
                on_substep()
        elapsed += remaining

        # A transitioned TLS's new green has only been running for the
        # hold portion of this step (not the yellow_span before it); every
        # other TLS's current green ran for the whole step uninterrupted.
        for tls in self._tls_ids:
            if tls in transitioning:
                self._phase_elapsed[tls] = remaining
            else:
                self._phase_elapsed[tls] = self._phase_elapsed.get(tls, 0.0) + elapsed

        self._t += elapsed
        done = self._t >= self.sim_end or traci.simulation.getMinExpectedNumber() <= 0

        # What the SignalEventLogger (graph_export.py) actually changed this
        # step -- built from the same from_slot/to_slot captured when the
        # transition was decided above, not reconstructed after the fact.
        phase_changes = {
            tls: {"from_slot": from_slot, "to_slot": to_slot}
            for tls, (_target_raw, _duration, from_slot, to_slot) in transitioning.items()
        }
        info = {
            "elapsed_s": elapsed,
            "arrived": arrived,
            "teleported": teleported,
            "total_waiting_time": self._total_waiting_time(),
            "mean_edge_speed": self._mean_edge_speed(),
            "phase_changes": phase_changes,
        }
        return self._get_observation(), info, done

    def _get_observation(self):
        obs = {}
        for tls in self._tls_ids:
            lanes = {}
            for lane in self._controlled_lanes[tls]:
                lanes[lane] = {
                    "queue": traci.lane.getLastStepHaltingNumber(lane),
                    "occupancy": traci.lane.getLastStepOccupancy(lane),
                    "waiting_time": traci.lane.getWaitingTime(lane),
                    "mean_speed": traci.lane.getLastStepMeanSpeed(lane),
                }
            obs[tls] = {
                "lanes": lanes,
                "current_phase": self._current_green_slot(tls),
                "num_phases": len(self._green_phases[tls]),
            }
        return obs

    def _total_waiting_time(self):
        return sum(
            traci.lane.getWaitingTime(lane)
            for tls in self._tls_ids
            for lane in self._controlled_lanes[tls]
        )

    def _mean_edge_speed(self):
        edge_ids = [e for e in traci.edge.getIDList() if not e.startswith(":")]
        speeds = [traci.edge.getLastStepMeanSpeed(e) for e in edge_ids]
        speeds = [s for s in speeds if s >= 0]
        return sum(speeds) / len(speeds) if speeds else 0.0

    @staticmethod
    def _empty_info():
        return {
            "elapsed_s": 0.0, "arrived": 0, "teleported": 0,
            "total_waiting_time": 0.0, "mean_edge_speed": 0.0, "phase_changes": {},
        }

    @property
    def tls_ids(self):
        return list(self._tls_ids)

    @property
    def t(self):
        """Cumulative simulated seconds elapsed since the last reset()."""
        return self._t

    def get_phase_lane_pairs(self, tls):
        """{green_slot: [(incoming_lane, outgoing_lane), ...]} for one TLS -- static structure,
        (re)computed at reset(). This is what a pressure-based controller
        (Max-Pressure) needs to know which lanes each green phase actually
        serves -- PravahEnv's observation only carries each TLS's own
        incoming lanes, not the outgoing lanes a pressure calculation also
        needs, since those usually belong to a different, unrelated part of
        the network.
        """
        return self._phase_lane_pairs[tls]

    def get_lane_queue(self, lane_id):
        """Current queue length (halted vehicle count) on ANY lane, not just
        a TLS's own controlled (incoming) lanes -- see get_phase_lane_pairs.
        """
        return traci.lane.getLastStepHaltingNumber(lane_id)

    def get_phase_duration(self, tls, slot):
        """The programmed duration (seconds) of one of a TLS's green phases,
        as netconvert generated it -- what a fixed-time baseline controller
        replicates, and what Max-Pressure is being compared against.
        """
        raw_idx = self._green_phases[tls][slot]
        return traci.trafficlight.getAllProgramLogics(tls)[0].phases[raw_idx].duration

    def inject_vehicle(self, edge_id, vehicle_id=None, vtype="DEFAULT_VEHTYPE", depart_speed="max"):
        """Spawns one vehicle starting on edge_id right now, bypassing the route file entirely.

        This is the hook a live (or synthetic-camera) detection feed calls
        to make simulated demand track something other than
        randomTrips.py's fixed, pre-generated .rou.xml -- e.g. "the camera
        at this edge saw a vehicle this interval" becomes one call here
        instead of something baked into a route file before the run ever
        started. No detection model exists yet to drive this, so it's
        exercised today by whatever stand-in caller needs it (e.g. a test,
        or a synthetic-count generator).

        Route is deliberately just [edge_id] -- the vehicle travels to the
        end of that edge and is removed there. That's the right minimal
        behavior for "represent one detected vehicle passing this point"
        without inventing a destination that doesn't exist yet; extend this
        to a multi-edge route (traci.simulation.findRoute) once real usage
        needs vehicles that continue past their injection point.
        """
        if vehicle_id is None:
            self._injected_count += 1
            vehicle_id = f"injected_{self._injected_count}"
        route_id = f"route_{vehicle_id}"
        traci.route.add(route_id, [edge_id])
        traci.vehicle.add(vehicle_id, route_id, typeID=vtype, departSpeed=depart_speed)
        return vehicle_id

    def close(self):
        if self._started:
            traci.close()
            self._started = False
