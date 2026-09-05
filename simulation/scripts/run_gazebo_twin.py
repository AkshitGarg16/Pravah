#!/usr/bin/env python3
"""CLI: drives a running Gazebo world live from a SUMO/TraCI simulation --
watch the digital twin firsthand, not a recording.

Launches `gz sim -r <world.sdf>` itself (GUI visible by default -- this is
meant to be watched, not captured headlessly), waits for it to come up,
then steps the SUMO simulation exactly like run_digital_twin.py does --
but every step it pushes every active vehicle's real position (and type,
for a visually distinct box per vehicle) into Gazebo via
GazeboBridge.sync(), and every real traffic-light phase change into
GazeboBridge.set_tls_colors(). Gazebo is a renderer here, not a second
traffic simulator: nothing about vehicle motion or signal state is
computed by Gazebo, every bit of it comes from SUMO over TraCI.

Usage:
    python scripts/run_gazebo_twin.py \
        --sumocfg sumo/pravah.sumocfg \
        --world gazebo/worlds/pravah_ito.sdf \
        --duration 600
"""

import argparse
import atexit
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, os.path.join(os.environ.get("SUMO_HOME", ""), "tools"))
import traci
import sumolib

from pravah.gazebo_bridge.bridge import GazeboBridge
from pravah.gazebo_bridge.world_builder import tls_approaches
from pravah.sumo_twin.controllers import MaxPressureController, QueueBasedController
from pravah.sumo_twin.env import PravahEnv
import gz.transport13 as transport
from gz.msgs10 import pose_pb2, boolean_pb2

CONTROLLERS = {"maxpressure": MaxPressureController, "queue": QueueBasedController}


def all_tls_approaches(net_path):
    """{tls_id: [approach, ...]} for every real traffic light in --net --
    the exact same per-approach breakdown build_gazebo_world.py used to
    place this world's markers, so set_tls_colors() (bridge.py) can read
    the right character out of each TLS's live RYG state string for each
    one. Harmless if a given tls_id isn't actually in this scoped world:
    GazeboBridge just publishes to a marker name nothing subscribes to.
    """
    sys.path.insert(0, os.path.join(os.environ.get("SUMO_HOME", ""), "tools"))
    import sumolib

    net = sumolib.net.readNet(str(net_path), withInternal=True)
    return {tls.getID(): tls_approaches(net, tls.getID()) for tls in net.getTrafficLights()}


def wait_for_gazebo(world_name, timeout=30):
    """Polls the set_pose service until gz-sim has finished starting up."""
    node = transport.Node()
    req = pose_pb2.Pose()
    req.name = "__probe__"  # harmless: a request for a nonexistent entity still gets a response once the service is up
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok, _resp = node.request(f"/world/{world_name}/set_pose", req, pose_pb2.Pose, boolean_pb2.Boolean, 500)
        if ok:
            return True
        time.sleep(0.5)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sumocfg", required=True)
    parser.add_argument("--world", required=True)
    parser.add_argument("--net", default=None,
                         help="the .net.xml the --world was built from, e.g. sumo/network/pravah.net.xml -- "
                              "needed to know which live RYG state-string character belongs to which "
                              "approach's own marker (see bridge.py's set_tls_colors). Without it, every TLS "
                              "falls back to a single shared marker showing only state[0], the old "
                              "one-direction-only behavior.")
    parser.add_argument("--world-name", default="pravah", help="must match the <world name=...> in the SDF file")
    parser.add_argument("--duration", type=int, default=600)
    parser.add_argument("--headless", action="store_true",
                         help="run gz-sim server-only, no visible window (default is GUI-visible -- "
                              "this script exists specifically to watch the run live)")
    parser.add_argument("--speed", type=float, default=1.0,
                         help="playback speed multiplier (1.0 = real-time: 1 simulated second per real second). "
                              "Without this, TraCI steps as fast as the CPU allows -- confirmed live (see chat) "
                              "that this races far ahead of gz-sim's own rendering and message processing, so "
                              "vehicles and signal colors visibly lag or appear stuck rather than tracking the "
                              "live state, defeating the entire point of watching it run.")
    parser.add_argument("--step-length", type=float, default=0.2,
                         help="SUMO simulated seconds per traci.simulationStep() call (default 0.2 = 5 "
                              "updates/sec). SUMO's own default is 1.0 -- confirmed live (see chat) that this "
                              "makes both sumo-gui and Gazebo look jittery, since a viewer only gets a new "
                              "vehicle position once per simulated second with nothing interpolating in "
                              "between. Pick a value whose reciprocal is a whole number (0.1, 0.2, 0.25, 0.5).")
    parser.add_argument("--sumo-gui", action="store_true",
                         help="also open sumo-gui, driven by the exact same TraCI connection as Gazebo -- not a "
                              "second, separately-seeded run. Needs a GUI-enabled SUMO build (this one was "
                              "rebuilt from source with the FOX toolkit specifically for this, see README.txt).")
    parser.add_argument("--controller", choices=["none", "maxpressure", "queue"], default="none",
                         help="none (default): raw TraCI passthrough, no PravahEnv -- SUMO's own "
                              "netconvert-generated fixed-time program drives every light untouched. This is "
                              "the 'before PRAVAH' case. maxpressure: drives every TLS through PravahEnv + "
                              "MaxPressureController instead -- the 'with PRAVAH' case. queue: PravahEnv + "
                              "QueueBasedController -- literally 'whichever approach has the most cars queued "
                              "gets the green', for the graduated-traffic demo. See README.txt's demo "
                              "sections for the matched run commands.")
    args = parser.parse_args()

    gz_cmd = ["gz", "sim", "-r", args.world]
    if args.headless:
        gz_cmd[2:2] = ["-s", "--headless-rendering"]
    gz_proc = subprocess.Popen(gz_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    atexit.register(gz_proc.terminate)

    print("Waiting for Gazebo to start...")
    if not wait_for_gazebo(args.world_name):
        print("Gazebo did not come up in time.", file=sys.stderr)
        sys.exit(1)
    print("Gazebo is up.")

    tls_approach_map = all_tls_approaches(args.net) if args.net else {}
    bridge = GazeboBridge(world_name=args.world_name, tls_approaches=tls_approach_map)
    steps_per_second = round(1.0 / args.step_length)
    sub_budget = args.step_length / args.speed  # real seconds to sleep between each individual SUMO sub-step

    def gazebo_tick(tls_ids):
        """Pulls live vehicle/signal state off whichever TraCI connection is
        currently open (module-level -- shared by both branches below,
        whether opened directly or via PravahEnv) and pushes it into Gazebo.
        Returns the vehicle count, for the periodic progress print."""
        vehicles = [
            {"id": vid, "x": (pos := traci.vehicle.getPosition(vid))[0], "y": pos[1],
             "angle": traci.vehicle.getAngle(vid), "type": traci.vehicle.getTypeID(vid)}
            for vid in traci.vehicle.getIDList()
        ]
        bridge.sync(vehicles)
        bridge.set_tls_colors({tls: traci.trafficlight.getRedYellowGreenState(tls) for tls in tls_ids})
        return len(vehicles)

    def pace(tick_start):
        remaining = sub_budget - (time.monotonic() - tick_start)
        if remaining > 0:
            time.sleep(remaining)

    if args.controller == "none":
        sumo_binary = sumolib.checkBinary("sumo-gui" if args.sumo_gui else "sumo")
        sumo_cmd = [sumo_binary, "-c", str(args.sumocfg), "--end", str(args.duration),
                    "--step-length", str(args.step_length)]
        if args.sumo_gui:
            # --start: begin immediately once this script's TraCI steps arrive,
            # instead of sitting on an unclicked "play" button. --quit-on-end:
            # close the window itself when TraCI disconnects at the end, rather
            # than leaving a dead simulation open.
            sumo_cmd += ["--start", "--quit-on-end"]
        traci.start(sumo_cmd)
        tls_ids = list(traci.trafficlight.getIDList())

        n_substeps = round(args.duration * steps_per_second)
        substep = 0
        try:
            while traci.simulation.getMinExpectedNumber() > 0 and substep < n_substeps:
                tick_start = time.monotonic()
                traci.simulationStep()
                n_vehicles = gazebo_tick(tls_ids)
                if substep % (30 * steps_per_second) == 0:
                    print(f"t={substep // steps_per_second}s  vehicles={n_vehicles}")
                substep += 1
                pace(tick_start)
        finally:
            traci.close()
            gz_proc.terminate()

    else:  # maxpressure or queue -- both "with PRAVAH" cases, just different decision logic
        # control_interval=1: the controller re-decides once per simulated
        # second, same cadence as before. What changed is step_length --
        # PravahEnv.step() now makes several real traci.simulationStep()
        # calls per second (steps_per_second of them) instead of one, and
        # the on_substep callback below runs gazebo_tick()/pace() after
        # every one of those, not just once when step() finally returns --
        # otherwise Gazebo would only see the state *after* all of a
        # second's sub-steps had already happened, putting back the exact
        # 1Hz choppiness step_length was meant to fix, just one level up.
        env = PravahEnv(args.sumocfg, control_interval=1, sim_end=args.duration,
                         lock_window=10, use_gui=args.sumo_gui, step_length=args.step_length)
        controller = CONTROLLERS[args.controller](env)
        substep = 0

        def on_substep():
            nonlocal substep
            tick_start = time.monotonic()
            gazebo_tick(env.tls_ids)
            substep += 1
            pace(tick_start)

        try:
            obs, info = env.reset(seed=1)
            controller.reset(obs)
            step = 0
            done = False
            while not done and step < args.duration:
                actions = controller.decide_actions(obs)
                obs, info, done = env.step(actions, on_substep=on_substep)
                step += int(info["elapsed_s"])
                if step % 30 < info["elapsed_s"]:
                    print(f"t={step}s  vehicles={traci.vehicle.getIDCount()}  "
                          f"total_waiting_time={info['total_waiting_time']:.0f}  "
                          f"mean_edge_speed={info['mean_edge_speed']:.2f}")
        finally:
            env.close()
            gz_proc.terminate()

    print("Done.")


if __name__ == "__main__":
    main()
