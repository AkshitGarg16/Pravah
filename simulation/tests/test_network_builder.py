import csv
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pravah.sumo_twin.network_builder import build_osm_xml, load_segments

FIELDNAMES = ["segment_id", "day", "street_name", "frc", "speed_limit", "geometry_wkt"]

# Segment A and Segment B share an endpoint (77.24709, 28.62810) -- a real
# junction. Segment C is disconnected, to prove unrelated segments don't
# accidentally get merged.
ROWS = [
    # segment A, two weekday rows -> load_segments must dedupe to one record
    {"segment_id": "A", "day": "Monday", "street_name": "Vikas Marg", "frc": "1",
     "speed_limit": "60", "geometry_wkt": "LINESTRING(77.24600 28.62800,77.24709 28.62810)"},
    {"segment_id": "A", "day": "Tuesday", "street_name": "Vikas Marg", "frc": "1",
     "speed_limit": "60", "geometry_wkt": "LINESTRING(77.24600 28.62800,77.24709 28.62810)"},
    # segment B starts exactly where A ends -> shared junction node
    {"segment_id": "B", "day": "Monday", "street_name": "Geeta Colony Road", "frc": "3",
     "speed_limit": "40", "geometry_wkt": "LINESTRING(77.24709 28.62810,77.24800 28.62900)"},
    # segment C is geographically disconnected from A/B
    {"segment_id": "C", "day": "Monday", "street_name": "", "frc": "4",
     "speed_limit": "30", "geometry_wkt": "LINESTRING(77.30000 28.70000,77.30100 28.70100)"},
]


def _write_csv(path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(ROWS)


def test_load_segments_dedupes_weekday_rows(tmp_path):
    csv_path = tmp_path / "segments.csv"
    _write_csv(csv_path)

    segments = load_segments(csv_path)

    assert set(segments) == {"A", "B", "C"}
    assert segments["A"]["street_name"] == "Vikas Marg"
    assert segments["A"]["speed_limit"] == "60"


def test_load_segments_falls_back_to_id_when_street_name_blank(tmp_path):
    csv_path = tmp_path / "segments.csv"
    _write_csv(csv_path)

    segments = load_segments(csv_path)

    assert segments["C"]["street_name"] == "segment-C"


def test_build_osm_xml_merges_shared_junction_node(tmp_path):
    csv_path = tmp_path / "segments.csv"
    _write_csv(csv_path)
    segments = load_segments(csv_path)

    tree, ways = build_osm_xml(segments)
    root = tree.getroot()

    way_elements = root.findall("way")
    nodes = root.findall("node")
    assert len(way_elements) == 3  # one way per unique segment, not per CSV row

    # A has 2 endpoints, B has 2 endpoints, C has 2 endpoints, but A and B
    # share one -> 6 - 1 = 5 unique nodes, not 6.
    assert len(nodes) == 5

    way_by_id = {w.get("id"): w for w in way_elements}
    a_refs = [nd.get("ref") for nd in way_by_id["A"].findall("nd")]
    b_refs = [nd.get("ref") for nd in way_by_id["B"].findall("nd")]
    assert a_refs[-1] == b_refs[0]  # A's end node is B's start node

    c_refs = [nd.get("ref") for nd in way_by_id["C"].findall("nd")]
    assert not set(c_refs) & set(a_refs)  # C shares nothing with A/B

    # The `ways` side dict (what segment_map.py's graph traversal is built
    # from) must agree exactly with what got written into the XML itself.
    assert ways["A"] == [int(r) for r in a_refs]
    assert ways["B"] == [int(r) for r in b_refs]
    assert ways["A"][-1] == ways["B"][0]


def test_build_osm_xml_maps_frc_to_highway_tag(tmp_path):
    csv_path = tmp_path / "segments.csv"
    _write_csv(csv_path)
    segments = load_segments(csv_path)

    tree, ways = build_osm_xml(segments)
    way_by_id = {w.get("id"): w for w in tree.getroot().findall("way")}

    def tag(way, key):
        return way.find(f"tag[@k='{key}']").get("v")

    assert tag(way_by_id["A"], "highway") == "primary"    # frc 1
    assert tag(way_by_id["B"], "highway") == "tertiary"   # frc 3
    assert tag(way_by_id["C"], "highway") == "residential"  # frc 4
    assert tag(way_by_id["A"], "oneway") == "yes"
    assert tag(way_by_id["A"], "maxspeed") == "60"
