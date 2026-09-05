#!/usr/bin/env python3
"""CLI: run the digital twin over TraCI and render it to an .mp4 clip.

Headless-safe -- renders via matplotlib + ffmpeg, no sumo-gui / X11 / Fox
toolkit needed (none of that is available on this machine without sudo;
see src/pravah/sumo_twin/render.py for why this approach is used instead,
and why it isn't just a fallback).

Usage:
    python scripts/render_video.py \
        --sumocfg sumo/pravah.sumocfg \
        --net sumo/network/pravah.net.xml \
        --out sumo/video/pravah_run.mp4 \
        --duration 600
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pravah.sumo_twin.render import record_trajectory, render_video, save_trajectory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sumocfg", required=True)
    parser.add_argument("--net", required=True, help="the same .net.xml the sumocfg points at")
    parser.add_argument("--out", required=True, help="output .mp4 path")
    parser.add_argument("--duration", type=int, default=3600, help="simulated seconds to run")
    parser.add_argument("--sample-interval", type=int, default=5, help="sim-seconds between recorded frames")
    parser.add_argument("--fps", type=int, default=20, help="output video frame rate")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--trajectory-out", default=None,
        help="optionally save the recorded trajectory as JSON, so the clip can be re-rendered "
             "with different styling later without re-running the simulation",
    )
    args = parser.parse_args()

    print(f"Recording {args.duration}s of simulation (sampling every {args.sample_interval}s)...")
    frames = record_trajectory(args.sumocfg, duration=args.duration,
                                sample_interval=args.sample_interval, seed=args.seed)
    print(f"Recorded {len(frames)} frames.")

    if args.trajectory_out:
        save_trajectory(frames, args.trajectory_out)
        print(f"Saved trajectory: {args.trajectory_out}")

    print(f"Rendering video ({args.fps} fps)...")
    render_video(args.net, frames, args.out, fps=args.fps)
    print(f"Wrote video: {args.out}")


if __name__ == "__main__":
    main()
