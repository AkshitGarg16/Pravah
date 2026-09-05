"""Tests for segment_map.py.

No SUMO/TraCI dependency at all -- the module only ever parses plain XML/JSON
files already on disk, so these tests run anywhere, always.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pravah.sumo_twin.segment_map import build_edge_to_segments, save_edge_to_segments, load_ways

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_NET = REPO_ROOT / "sumo" / "network" / "pravah.net.xml"
REAL_WAYS = REPO_ROOT / "sumo" / "network" / "pravah.net.ways.json"


def _write_net_xml(path, junction_ids, edges):
    """edges: {edge_id: (from_id, to_id)} -- from/to only matter for junction lookup elsewhere,
    build_edge_to_segments doesn't read them at all, only the edge id and function."""
    root = ET.Element("net")
    for jid in junction_ids:
        ET.SubElement(root, "junction", id=jid, type="priority")
    for eid, (f, t) in edges.items():
        ET.SubElement(root, "edge", id=eid, attrib={"from": f, "to": t})
    ET.ElementTree(root).write(path)


def test_extends_chain_through_a_plain_passthrough_point(tmp_path):
    # Segments A (1->2) and B (2->3) share node 2, which is NOT a real junction
    # (no <junction id="2">) -- the final network kept only edge A, so A should
    # resolve to the full chain [A, B].
    ways_path = tmp_path / "net.ways.json"
    import json
    json.dump({"A": [1, 2], "B": [2, 3]}, open(ways_path, "w"))

    net_path = tmp_path / "net.xml"
    _write_net_xml(net_path, junction_ids=["1", "3"], edges={"A": ("1", "3")})

    mapping = build_edge_to_segments(net_path, ways_path)
    assert mapping == {"A": ["A", "B"]}


def test_stops_at_a_real_junction_even_if_topologically_passthrough(tmp_path):
    # Same shape as above, but node 2 DOES survive as a real junction -- this is
    # exactly the case a pure connectivity/degree check can't see (e.g. netconvert
    # placed a traffic light there, or an attribute mismatch blocked merging).
    # Both A and B must resolve to themselves only, not to each other.
    import json
    ways_path = tmp_path / "net.ways.json"
    json.dump({"A": [1, 2], "B": [2, 3]}, open(ways_path, "w"))

    net_path = tmp_path / "net.xml"
    _write_net_xml(net_path, junction_ids=["1", "2", "3"], edges={"A": ("1", "2"), "B": ("2", "3")})

    mapping = build_edge_to_segments(net_path, ways_path)
    assert mapping == {"A": ["A"], "B": ["B"]}


def test_cluster_junction_id_absorbs_all_its_constituent_nodes(tmp_path):
    # --junctions.join can merge several original nodes into one "cluster_A_B"
    # junction -- every constituent node is a real boundary, not just the first.
    import json
    ways_path = tmp_path / "net.ways.json"
    json.dump({"A": [1, 2], "B": [2, 3], "C": [3, 4]}, open(ways_path, "w"))

    net_path = tmp_path / "net.xml"
    _write_net_xml(
        net_path,
        junction_ids=["1", "cluster_2_3", "4"],
        edges={"A": ("1", "cluster_2_3"), "C": ("cluster_2_3", "4")},
    )
    # B (2->3) got fully absorbed into the cluster junction's internal geometry --
    # it isn't recoverable from this net.xml (see module docstring: this is a
    # real, known limitation, not something this test pretends to solve).
    mapping = build_edge_to_segments(net_path, ways_path)
    assert mapping == {"A": ["A"], "C": ["C"]}


def test_edge_id_with_hash_suffix_resolves_to_its_base_segment(tmp_path):
    # netconvert appends #0, #1... when it has to SPLIT one original way.
    import json
    ways_path = tmp_path / "net.ways.json"
    json.dump({"A": [1, 2, 3]}, open(ways_path, "w"))

    net_path = tmp_path / "net.xml"
    _write_net_xml(net_path, junction_ids=["1", "2", "3"], edges={"A#0": ("1", "2"), "A#1": ("2", "3")})

    mapping = build_edge_to_segments(net_path, ways_path)
    assert mapping == {"A#0": ["A"], "A#1": ["A"]}


def test_save_edge_to_segments_round_trips(tmp_path):
    out_path = tmp_path / "nested" / "map.json"
    save_edge_to_segments({"E1": ["S1", "S2"]}, out_path)
    import json
    assert json.load(open(out_path)) == {"E1": ["S1", "S2"]}


def test_against_the_real_built_network():
    if not (REAL_NET.exists() and REAL_WAYS.exists()):
        import pytest
        pytest.skip("sumo/network/pravah.net.xml or .ways.json not built yet -- see README.txt section 4")

    all_segments = set(load_ways(REAL_WAYS).keys())
    mapping = build_edge_to_segments(REAL_NET, REAL_WAYS)

    assert None not in mapping.values()  # every edge's own id must resolve to a seed segment

    covered = {s for chain in mapping.values() for s in chain}
    coverage = len(covered) / len(all_segments)
    # ~85.6% measured on the current build (43/299 original segments are short
    # connectors fully absorbed into roundabout/junction internal geometry --
    # see the module docstring). A regression well below that means the
    # extension logic broke; a rise is fine and expected as the network build
    # improves. This is a floor, not an exact-match assertion.
    assert coverage >= 0.80, f"segment coverage dropped to {coverage:.1%}, expected >= 80%"

    all_refs = [s for chain in mapping.values() for s in chain]
    dupes = {s for s in set(all_refs) if all_refs.count(s) > 1}
    assert not dupes, f"segment(s) claimed by more than one edge: {dupes}"
