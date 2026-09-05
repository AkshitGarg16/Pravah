"""Builds a SUMO road network from the real PRAVAH TomTom CSV segment geometry.

Each CSV row is one road segment sampled over a week; five rows (Mon-Fri)
share the same static geometry, so segments are deduped by `segment_id`
before anything else. Segment endpoints that share the same (rounded)
coordinate are the real road junctions -- merging them at that precision is
what turns 299 independently-listed line segments back into the single
connected, 81-junction road network they actually form (checked against the
CSV before writing this).

The network is expressed as a synthetic OSM XML file (nodes + ways) and
handed to SUMO's own `netconvert` OSM importer, rather than building
`.net.xml` directly, because netconvert's OSM path already does
geo-projection, lane-count guessing from the `highway` tag, and one-way
handling correctly -- reimplementing that in plain-xml node/edge XML would
just recreate netconvert's importer with more bugs.
"""

import csv
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

# TomTom's Functional Road Class: 1 = highest-capacity arterial ... 4 = residential.
# Mapped to OSM `highway` tags so netconvert's OSM importer picks sane lane
# counts and default speeds for each segment.
FRC_TO_HIGHWAY = {
    "1": "primary",
    "2": "secondary",
    "3": "tertiary",
    "4": "residential",
}

_COORD_RE = re.compile(r"(-?\d+\.\d+) (-?\d+\.\d+)")

# ~1m precision at this latitude -- close enough to merge real shared
# junction endpoints without accidentally merging distinct nearby segments.
COORD_PRECISION = 5


def _parse_linestring(wkt):
    return [(float(lon), float(lat)) for lon, lat in _COORD_RE.findall(wkt)]


def _node_key(lon, lat):
    return (round(lon, COORD_PRECISION), round(lat, COORD_PRECISION))


def load_segments(csv_path):
    """Dedupe the CSV (5 weekday rows/segment) to one static-geometry record per segment_id."""
    segments = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sid = row["segment_id"]
            if sid in segments:
                continue
            coords = _parse_linestring(row["geometry_wkt"])
            if len(coords) < 2:
                continue
            segments[sid] = {
                "coords": coords,
                "street_name": row["street_name"] or f"segment-{sid}",
                "frc": row["frc"],
                "speed_limit": row["speed_limit"],
            }
    return segments


def build_osm_xml(segments):
    """Turn deduped segments into a synthetic OSM XML tree that netconvert can import.

    All <node> elements are written before any <way> element. netconvert's OSM
    parser resolves `<nd ref=...>` against nodes seen so far in document order
    (like real OSM XML always does) -- a way referencing a node defined later
    in the file fails with "referenced geometry information is not known", so
    node collection has to be a separate pass from way emission, not interleaved.
    """
    node_ids = {}  # rounded (lon, lat) -> synthetic osm node id
    next_node_id = 1

    for seg in segments.values():
        for lon, lat in seg["coords"]:
            key = _node_key(lon, lat)
            if key not in node_ids:
                node_ids[key] = next_node_id
                next_node_id += 1

    osm = ET.Element("osm", version="0.6", generator="pravah-network-builder")

    for (lon, lat), nid in sorted(node_ids.items(), key=lambda item: item[1]):
        ET.SubElement(osm, "node", id=str(nid), lon=f"{lon:.7f}", lat=f"{lat:.7f}")

    ways = {}  # segment_id -> ordered list of node ids, kept for segment_map.py
    for seg_id, seg in segments.items():
        way = ET.SubElement(osm, "way", id=str(seg_id))
        node_seq = [node_ids[_node_key(lon, lat)] for lon, lat in seg["coords"]]
        for nid in node_seq:
            ET.SubElement(way, "nd", ref=str(nid))
        ET.SubElement(way, "tag", k="highway", v=FRC_TO_HIGHWAY.get(seg["frc"], "unclassified"))
        ET.SubElement(way, "tag", k="name", v=seg["street_name"])
        ET.SubElement(way, "tag", k="maxspeed", v=str(seg["speed_limit"]))
        # TomTom segments are directional flow measurements: a real two-way
        # street shows up as two separate opposing segments in the CSV, so
        # each way here genuinely is one-way, not an import artifact.
        ET.SubElement(way, "tag", k="oneway", v="yes")
        ways[seg_id] = node_seq

    return ET.ElementTree(osm), ways


def write_osm_xml(segments, out_path, ways_out_path=None):
    """Writes the OSM XML, and -- unless ways_out_path is False -- a JSON side
    file mapping segment_id -> ordered node-id sequence next to it (same
    stem, .ways.json). netconvert's --geometry.remove later merges multiple
    original segments into one final edge; this file is what lets
    segment_map.py reconstruct which original segment_ids ended up inside
    which final edge, since netconvert's own output doesn't retain a full
    per-edge segment list on its own (see segment_map.py's docstring).
    """
    tree, ways = build_osm_xml(segments)
    ET.indent(tree, space="  ")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_path, encoding="UTF-8", xml_declaration=True)

    if ways_out_path is not False:
        ways_path = Path(ways_out_path) if ways_out_path else out_path.with_suffix("").with_suffix(".ways.json")
        with open(ways_path, "w") as f:
            json.dump(ways, f)
        return ways_path
    return None
