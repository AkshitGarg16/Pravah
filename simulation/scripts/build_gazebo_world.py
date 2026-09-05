#!/usr/bin/env python3
"""CLI: SUMO .net.xml -> Gazebo (gz-sim) SDF world with the real road geometry.

Usage (whole network):
    python scripts/build_gazebo_world.py \
        --net sumo/network/pravah.net.xml --out gazebo/worlds/pravah.sdf

Usage (scoped to one junction, e.g. the real ITO junction):
    python scripts/build_gazebo_world.py \
        --net sumo/network/pravah.net.xml --out gazebo/worlds/pravah_ito.sdf \
        --center-junction cluster_2_72 --radius 300

Traffic-light marker positions are always read fresh from --net itself
(every traffic_light-type junction), not from a separately generated file.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pravah.gazebo_bridge.world_builder import write_world


def _load_net(net_path):
    sumo_home = os.environ.get("SUMO_HOME")
    if not sumo_home:
        raise SystemExit("SUMO_HOME not set -- needed to read --net.")
    sys.path.insert(0, os.path.join(sumo_home, "tools"))
    import sumolib

    return sumolib.net.readNet(str(net_path))


def _live_tls_positions(net):
    """Every TLS junction's own coordinate, read fresh from --net itself --
    not a separately-generated/cacheable file. A JSON side file here would
    silently go stale the moment the network is rebuilt with a different
    (or new, e.g. the forced ITO) set of traffic lights, exactly what
    happened once already this session: an old network_geometry.json from
    before ITO had a signal silently dropped it from a scoped world.
    """
    return {
        j.getID(): j.getCoord()
        for j in net.getNodes()
        if j.getType() == "traffic_light"
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--net", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--center-junction", default=None,
                         help="scope the world to just this junction's surroundings, e.g. cluster_2_72 (the "
                              "real ITO junction) -- looked up from --net itself, not hand-entered coordinates")
    parser.add_argument("--radius", type=float, default=300.0,
                         help="meters around --center-junction to include (ignored without --center-junction)")
    args = parser.parse_args()

    net = _load_net(args.net)
    tls_positions = _live_tls_positions(net)

    center = net.getNode(args.center_junction).getCoord() if args.center_junction else None
    radius = args.radius if center else None

    out = write_world(args.net, args.out, tls_positions=tls_positions, center=center, radius=radius)
    scope = f"scoped to {args.center_junction} +/-{radius:.0f}m" if center else "whole network"
    print(f"Wrote Gazebo world ({scope}): {out}")


if __name__ == "__main__":
    main()
