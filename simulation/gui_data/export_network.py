#!/usr/bin/env python3
"""Exports the SUMO network as the dashboard's canonical topology.

The Pravah_Traffic_Optimizer middleware needs a `network.json` describing every
road and junction the feed can talk about. It shipped with an OpenStreetMap
extract centred on 28.6289,77.2410 with a 1 km radius -- which is mostly *west*
of the network this simulation actually runs. Three of the four traffic lights
fell inside that disc, junction 309 did not, and most of the 132 SUMO edges had
no counterpart at all, so the dashboard would have drawn roads nobody is
simulating and greyed out the ones that are.

Generating the topology from `pravah.net.xml` instead makes the correspondence
exact: every id the telemetry emits is an id the dashboard already knows, so the
middleware's crosswalk resolves on its first branch and never has to snap
anything by coordinate.

Ids follow the middleware's `<kind>:<source>:<local id>` convention:

    segment    S:sumo:<edge id>
    junction   J:sumo:<junction id>

Each segment also carries `tomtom_ids` -- the original CSV segment ids the edge
was built from -- so a real-world TomTom feed can still be fused onto the same
segment later without going through SUMO at all.

    gui_data/export_network.py                 # writes both copies
    gui_data/export_network.py --dry-run       # print the summary only
"""

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.path.join(os.environ.get("SUMO_HOME", ""), "tools"))

import sumolib  # noqa: E402

sys.path.insert(0, str(REPO / "gui_data"))

from pravah_telemetry import load_segment_map, load_segment_reference  # noqa: E402

# The dashboard is the sibling directory in this repo. Override with
# --gui-repo if the two halves live apart.
GUI_REPO = REPO.parent / "dashboard"

# SUMO edge types are "highway.primary" and the like; the dashboard styles and
# labels roads by the bare OSM class, so strip the prefix and keep the class.
KNOWN_HIGHWAYS = {"motorway", "trunk", "primary", "secondary", "tertiary",
                  "unclassified", "residential", "living_street", "service"}


def highway_class(edge):
    raw = (edge.getType() or "").split(".")[-1].replace("_link", "")
    if raw in KNOWN_HIGHWAYS:
        return raw
    # No usable type tag: fall back to the speed limit, which is what the class
    # would have implied anyway.
    kmh = edge.getSpeed() * 3.6
    return "trunk" if kmh >= 65 else "primary" if kmh >= 55 else \
        "secondary" if kmh >= 45 else "tertiary" if kmh >= 35 else "residential"


def build(net, segment_map, seg_ref):
    lonlat = net.convertXY2LonLat

    segments = []
    named = 0
    for edge in net.getEdges():
        eid = edge.getID()
        tomtom = segment_map.get(eid, [])
        # The CSV is the only source of street names -- netconvert kept none.
        name = next((seg_ref[s]["street_name"] for s in tomtom
                     if seg_ref.get(s, {}).get("street_name")), None)
        named += bool(name)
        hw = highway_class(edge)
        # coords are [lat, lng] pairs: the schema the middleware and MapView
        # both already use (MapView flips them for GeoJSON itself).
        coords = [[round(lat, 7), round(lon, 7)]
                  for lon, lat in (lonlat(x, y) for x, y in edge.getShape())]
        segments.append({
            "id": f"S:sumo:{eid}",
            "osm_way": eid,
            "from_node": edge.getFromNode().getID(),
            "to_node": edge.getToNode().getID(),
            "name": name or hw.title(),
            "highway": hw,
            "lanes": edge.getLaneNumber(),
            # Every SUMO edge carries one direction; the opposite direction is a
            # separate edge, so each segment here is genuinely one-way.
            "oneway": True,
            "length_m": round(edge.getLength(), 1),
            "free_flow_kmh": round(edge.getSpeed() * 3.6, 1),
            "coords": coords,
            "tomtom_ids": tomtom,
        })

    junctions = []
    for node in net.getNodes():
        nid = node.getID()
        if nid.startswith(":"):
            continue
        lon, lat = lonlat(*node.getCoord())
        incoming = [e for e in node.getIncoming() if not e.getID().startswith(":")]
        outgoing = [e for e in node.getOutgoing() if not e.getID().startswith(":")]
        if not incoming and not outgoing:
            continue
        junctions.append({
            "id": f"J:sumo:{nid}",
            "osm_node": nid,
            "name": next((seg_ref[s]["street_name"] for e in incoming
                          for s in segment_map.get(e.getID(), [])
                          if seg_ref.get(s, {}).get("street_name")), ""),
            "lat": round(lat, 7),
            "lng": round(lon, 7),
            "signalised": node.getType() == "traffic_light",
            "approaches": sorted(f"S:sumo:{e.getID()}" for e in incoming),
        })

    # A movement is a turn the network actually permits, taken from the
    # connections netconvert built -- not the cartesian product of approaches.
    movements = []
    known = {j["id"] for j in junctions}
    for node in net.getNodes():
        jid = f"J:sumo:{node.getID()}"
        if jid not in known:
            continue
        for u in node.getIncoming():
            if u.getID().startswith(":"):
                continue
            for v in u.getOutgoing():
                if v.getID().startswith(":"):
                    continue
                movements.append({"junction": jid,
                                  "from": f"S:sumo:{u.getID()}",
                                  "to": f"S:sumo:{v.getID()}"})

    lats = [c[0] for s in segments for c in s["coords"]]
    lngs = [c[1] for s in segments for c in s["coords"]]
    centre = {"lat": round((min(lats) + max(lats)) / 2, 7),
              "lng": round((min(lngs) + max(lngs)) / 2, 7),
              # Half the diagonal, so the whole network sits inside the radius.
              "radius_m": round(sumolib.geomhelper.distance(
                  net.convertLonLat2XY(min(lngs), min(lats)),
                  net.convertLonLat2XY(max(lngs), max(lats))) / 2)}

    return {"center": centre, "segments": segments,
            "junctions": junctions, "movements": movements}, named


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--net", default=str(REPO / "sumo" / "network" / "pravah.net.xml"))
    ap.add_argument("--segment-map",
                    default=str(REPO / "sumo" / "network" / "pravah.net.segment_map.json"))
    ap.add_argument("--reference-csv",
                    default=str(REPO / "data" / "pravah_900to1000_balanced.csv"))
    ap.add_argument("--gui-repo", default=str(GUI_REPO),
                    help="the Pravah_Traffic_Optimizer checkout to write into")
    ap.add_argument("--dry-run", action="store_true", help="print the summary, write nothing")
    args = ap.parse_args()

    net = sumolib.net.readNet(args.net)
    payload, named = build(net, load_segment_map(args.segment_map),
                           load_segment_reference(args.reference_csv))

    signals = sum(j["signalised"] for j in payload["junctions"])
    print(f"{len(payload['segments'])} segments ({named} with a street name from the CSV), "
          f"{len(payload['junctions'])} junctions ({signals} signalised), "
          f"{len(payload['movements'])} movements")
    print(f"centre {payload['center']['lat']}, {payload['center']['lng']} "
          f"r={payload['center']['radius_m']} m")
    for j in payload["junctions"]:
        if j["signalised"]:
            print(f"  signal {j['id']:<24} {j['lat']}, {j['lng']}  "
                  f"{len(j['approaches'])} approaches")

    if args.dry_run:
        return

    gui = Path(args.gui_repo)
    blob = json.dumps(payload, separators=(",", ":"))
    targets = [gui / "backend" / "data" / "network.json",  # what the middleware serves
               gui / "public" / "network.json"]            # the frontend's offline fallback
    for target in targets:
        # The OSM extract is kept, not overwritten: it is the only copy of that
        # topology and re-fetching it needs Overpass to be up.
        if target.exists() and "S:osm:" in target.read_text()[:4000]:
            backup = target.with_suffix(".osm.json")
            if not backup.exists():
                backup.write_text(target.read_text())
                print(f"kept the OSM extract as {backup}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(blob)
        print(f"wrote {target} ({len(blob) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
