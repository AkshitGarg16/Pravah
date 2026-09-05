#!/usr/bin/env python3
"""CLI: real CSV segment geometry -> synthetic OSM XML -> SUMO .net.xml via netconvert.

Usage:
    python scripts/build_sumo_network.py \
        --csv data/pravah_900to1000_balanced.csv \
        --out sumo/network/pravah.net.xml
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pravah.sumo_twin.network_builder import load_segments, write_osm_xml
from pravah.sumo_twin.segment_map import build_edge_to_segments, save_edge_to_segments


def find_netconvert():
    """Locate the netconvert binary: PATH first, then $SUMO_HOME/bin (source checkout or install)."""
    exe = shutil.which("netconvert")
    if exe:
        return exe
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        candidate = Path(sumo_home) / "bin" / "netconvert"
        if candidate.exists():
            return str(candidate)
        candidate = Path(sumo_home) / "build" / "cmake-build" / "bin" / "netconvert"
        if candidate.exists():
            return str(candidate)
    raise SystemExit(
        "netconvert not found on PATH or under $SUMO_HOME/bin.\n"
        "Build SUMO first (see sumo-src/) or add its bin dir to PATH,\n"
        "e.g. export SUMO_HOME=/home/kreacher/sumo-src && export PATH=$HOME/.local/bin:$PATH"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="path to the PRAVAH TomTom CSV")
    parser.add_argument("--out", required=True, help="output .net.xml path")
    parser.add_argument("--osm-out", default=None, help="where to write the intermediate synthetic OSM XML")
    parser.add_argument(
        "--force-tls", default="cluster_2_72",
        help="comma-separated junction id(s) to force into a controllable traffic light regardless of "
             "--tls.guess's geometry-based placement. Default is the real ITO junction (Mahatma Gandhi "
             "Marg x Indra Prasta Road) -- TomTom carries no signal data, so the simulation is the thing "
             "that decides where a signal exists; empty string disables this.",
    )
    args = parser.parse_args()

    osm_path = args.osm_out or str(Path(args.out).with_suffix("")) + ".osm.xml"

    segments = load_segments(args.csv)
    print(f"Loaded {len(segments)} unique segments from {args.csv}")

    ways_path = write_osm_xml(segments, osm_path)
    print(f"Wrote synthetic OSM XML: {osm_path}")

    netconvert = find_netconvert()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        netconvert,
        "--osm-files", osm_path,
        "-o", args.out,
        "--geometry.remove",
        "--roundabouts.guess",
        "--junctions.join",
        "--tls.guess",  # infer signal placement from junction geometry -- our synthetic
                         # OSM has no real traffic_signal tags, so --tls.guess-signals
                         # (which only reinterprets those tags) would find nothing.
        "--tls.default-type", "static",
    ]
    if args.force_tls:
        cmd += ["--tls.set", args.force_tls]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"Wrote network: {args.out}")

    edge_map = build_edge_to_segments(args.out, ways_path)
    covered = {s for chain in edge_map.values() if chain for s in chain}
    total_segments = len(segments)
    map_out = str(Path(args.out).with_suffix("")) + ".segment_map.json"
    save_edge_to_segments(edge_map, map_out)
    print(f"Wrote edge -> original segment_id map: {map_out} "
          f"({len(covered)}/{total_segments} original segments recovered, "
          f"{100 * len(covered) / total_segments:.1f}%)")


if __name__ == "__main__":
    main()
