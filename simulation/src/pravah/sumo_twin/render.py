"""Records a TraCI run's vehicle/signal trajectories and renders them to video.

This machine has no FOX toolkit (checked: no fox.pc, no libFOX anywhere on
disk), which is what sumo-gui needs -- and installing it would need apt
(sudo, unavailable here) or a from-source FOX build of its own. Rather than
chase that, this renders directly to an .mp4 via matplotlib + ffmpeg (both
already present on this machine): no display server, no X session, no
sumo-gui involved at all. It's also strictly more useful for a hackathon
clip than a raw GUI screen recording would be -- exact framing, a live
signal-state overlay (red/yellow/green per junction) and a vehicle/time
counter aren't things sumo-gui gives you by default either.

record_trajectory() and render_video() are deliberately separate: recording
requires a live TraCI connection ($SUMO_HOME set), rendering only needs the
saved trajectory file and the .net.xml, so a clip can be re-styled without
re-running the simulation.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.environ.get("SUMO_HOME", ""), "tools"))
try:
    import traci
    import sumolib
except ImportError:
    # See env.py for why this is deferred rather than a hard import-time
    # failure: it keeps this module importable (e.g. for render_video(),
    # which only needs sumolib) without $SUMO_HOME set for recording.
    traci = None
    sumolib = None

import numpy as np
import matplotlib

matplotlib.use("Agg")  # headless backend -- no display needed, ever
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.collections import LineCollection


def record_trajectory(sumocfg, duration=3600, sample_interval=5, seed=None):
    """Runs the sim over TraCI, sampling every `sample_interval` sim-seconds.

    Returns a list of frames: {"t", "vehicles": [{"id","x","y","angle","type"}],
    "tls": {tls_id: state_string}}. Plain dict/list data, not live traci
    objects, so it can be saved to disk (save_trajectory) and re-rendered
    later without a SUMO connection at all.
    """
    if traci is None:
        raise RuntimeError(
            "traci not importable -- set $SUMO_HOME before recording "
            "(see README.txt section 6)."
        )

    sumo_binary = sumolib.checkBinary("sumo")
    cmd = [sumo_binary, "-c", str(sumocfg), "--end", str(duration)]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    traci.start(cmd)

    frames = []
    step = 0
    while traci.simulation.getMinExpectedNumber() > 0 and step < duration:
        traci.simulationStep()
        if step % sample_interval == 0:
            vehicles = [
                {
                    "id": vid,
                    "x": (pos := traci.vehicle.getPosition(vid))[0],
                    "y": pos[1],
                    "angle": traci.vehicle.getAngle(vid),
                    "type": traci.vehicle.getTypeID(vid),
                }
                for vid in traci.vehicle.getIDList()
            ]
            tls_states = {
                tls: traci.trafficlight.getRedYellowGreenState(tls)
                for tls in traci.trafficlight.getIDList()
            }
            frames.append({"t": step, "vehicles": vehicles, "tls": tls_states})
        step += 1

    traci.close()
    return frames


def save_trajectory(frames, out_path):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(frames, f)


def load_trajectory(path):
    with open(path) as f:
        return json.load(f)


def _tls_stop_line_positions(net):
    """One representative (x, y) per traffic light -- the stop line of its first controlled lane."""
    positions = {}
    for tls in net.getTrafficLights():
        conns = tls.getConnections()
        if conns:
            in_lane = conns[0][0]
            positions[tls.getID()] = in_lane.getShape()[-1]
    return positions


def _state_color(state):
    """Collapses a multi-link RYG state string (e.g. 'GGrr') to one at-a-glance color.

    Red if any controlled link is red and none are green/yellow, yellow if
    any link is transitioning, else green -- good enough for a wide shot
    where each junction is one dot, not a lane-by-lane diagram.
    """
    s = state.lower()
    if "y" in s:
        return "#f1c40f"
    if "g" in s:
        return "#2ecc71"
    return "#e74c3c"


def render_video(net_path, frames, out_path, fps=20, figsize=None, vehicle_size=10):
    """Renders recorded frames (from record_trajectory / load_trajectory) to an .mp4.

    figsize=None (default) sizes the canvas to the network's own aspect
    ratio instead of assuming a square -- this corridor's road network is
    much wider than it is tall, and a fixed square canvas leaves dead black
    bars above and below it, which looks wrong in an actual demo clip.
    """
    if sumolib is None:
        raise RuntimeError("sumolib not importable -- set $SUMO_HOME (see README.txt section 6).")

    # withInternal=True: sumolib skips parsing internal (in-junction connector)
    # edges by default. Without them, only each real edge's shape up to the
    # junction boundary gets drawn -- every junction then shows as a gap,
    # since the actual turn geometry *through* the intersection is exactly
    # what "internal" edges are. Drawing both is what makes the road network
    # look continuous instead of a set of disconnected line segments.
    net = sumolib.net.readNet(str(net_path), withInternal=True)
    edge_segments = [e.getShape() for e in net.getEdges()]
    tls_positions = _tls_stop_line_positions(net)

    xs = [p[0] for seg in edge_segments for p in seg]
    ys = [p[1] for seg in edge_segments for p in seg]
    margin = 50
    x_span = (max(xs) - min(xs)) + 2 * margin
    y_span = (max(ys) - min(ys)) + 2 * margin

    if figsize is None:
        aspect = x_span / y_span
        long_side = 14
        figsize = (long_side, long_side / aspect) if aspect >= 1 else (long_side * aspect, long_side)
        figsize = (max(figsize[0], 4), max(figsize[1], 4))  # keep degenerate/near-1D networks sane

    fig, ax = plt.subplots(figsize=figsize)
    ax.set_xlim(min(xs) - margin, max(xs) + margin)
    ax.set_ylim(min(ys) - margin, max(ys) + margin)
    ax.set_aspect("equal")
    ax.set_facecolor("#202020")
    fig.patch.set_facecolor("#202020")
    ax.axis("off")
    ax.add_collection(LineCollection(edge_segments, colors="#808080", linewidths=1.5, zorder=1))

    vehicle_scat = ax.scatter([], [], s=vehicle_size, c="#ffcc00", zorder=3)
    tls_scat = ax.scatter([], [], s=140, zorder=4, edgecolors="white", linewidths=0.8)
    time_text = ax.text(
        0.02, 0.97, "", transform=ax.transAxes, color="white",
        fontsize=13, va="top", family="monospace",
    )

    empty = np.empty((0, 2))

    def update(frame):
        pts = np.array([(v["x"], v["y"]) for v in frame["vehicles"]]) if frame["vehicles"] else empty
        vehicle_scat.set_offsets(pts)

        tls_pts, tls_colors = [], []
        for tls_id, state in frame["tls"].items():
            pos = tls_positions.get(tls_id)
            if pos is not None:
                tls_pts.append(pos)
                tls_colors.append(_state_color(state))
        tls_scat.set_offsets(np.array(tls_pts) if tls_pts else empty)
        if tls_colors:
            tls_scat.set_color(tls_colors)

        mins, secs = divmod(int(frame["t"]), 60)
        time_text.set_text(f"t = {mins:02d}:{secs:02d}   vehicles = {len(frame['vehicles'])}")
        return vehicle_scat, tls_scat, time_text

    anim = animation.FuncAnimation(fig, update, frames=frames, blit=False)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    anim.save(str(out_path), writer=animation.FFMpegWriter(fps=fps, bitrate=2400))
    plt.close(fig)
