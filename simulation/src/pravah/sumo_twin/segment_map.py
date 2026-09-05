"""Maps final SUMO edge ids back to the original CSV segment_ids they came from.

netconvert's --geometry.remove merges chains of original one-way segments
(each written as one <way> in the synthetic OSM -- see network_builder.py)
into fewer, longer edges: 299 original CSV segments became 132 edges in the
current build. Reconstructing which original segment_ids ended up inside
which final edge needs to know exactly where netconvert decided to stop
merging -- and that decision isn't purely topological. A first version of
this walked outward from each edge's own seed segment through points with
exactly one incoming and one outgoing connection (computed from
network_builder.py's `ways` side file: segment_id -> ordered node-id
sequence, written next to the .osm.xml as <net-name>.ways.json). That's
right for real branch points (a node touched by more than one segment,
wherever it falls in either segment's sequence -- not just at a segment's
own first/last point), but netconvert can *also* stop merging at a plain
1-in/1-out point for reasons this side file can't see: a placed traffic
light, or an attribute mismatch (lane count, speed) between the two
segments -- and treating those as pass-through points produced two
different final edges that both claimed the same downstream segment.

So the stop condition here isn't "degree computed from our own data" --
it's "does this point survive as a real <junction> in netconvert's own
output". That's authoritative in a way nothing reconstructed from the
input can be, since it's literally netconvert telling us where it put a
boundary, whatever its reason.

A remaining ~13% of original segments (short connectors absorbed into
roundabout/complex-junction internal geometry) don't resolve to any final
edge at all -- there's no junction id for the removed node to recover in
that case either. build_edge_to_segments() reports actual coverage
(len(covered) vs. the full segment set) rather than assuming 100%; see
tests/test_segment_map.py for the currently-verified numbers.

This is the piece a camera-to-edge mapping, or a per-original-segment
sim-vs-real speed comparison against the CSV, gets built on top of.
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path


def load_ways(ways_json_path):
    with open(ways_json_path) as f:
        return json.load(f)


def _build_micro_graph(ways):
    """Every consecutive point-pair, across every segment, as one directed micro-edge."""
    out_edges = {}  # node -> [(next_node, segment_id), ...]
    in_edges = {}   # node -> [(prev_node, segment_id), ...]
    for seg_id, node_seq in ways.items():
        for a, b in zip(node_seq, node_seq[1:]):
            out_edges.setdefault(a, []).append((b, seg_id))
            in_edges.setdefault(b, []).append((a, seg_id))
    return out_edges, in_edges


def _real_junction_nodes(net_xml_root):
    """Original node ids that survive as an actual <junction> in the final network.

    A plain numeric junction id ("148") is an original node kept as-is. A
    "cluster_A_B_C" id is what --junctions.join produces when it merges
    several original nodes into one junction -- every one of A, B, C... was
    a real boundary point netconvert chose to keep, so all of them count.
    Internal helper junctions (":..." ids, used for intersection geometry)
    aren't original nodes at all and are skipped.
    """
    nodes = set()
    for junction in net_xml_root.findall("junction"):
        jid = junction.get("id")
        if jid.startswith(":"):
            continue
        if jid.startswith("cluster_"):
            nodes.update(int(tok) for tok in jid[len("cluster_"):].split("_") if tok.isdigit())
        elif jid.isdigit():
            nodes.add(int(jid))
    return nodes


def _own_segment_id(edge_id, ways):
    if edge_id in ways:
        return edge_id
    base = edge_id.split("#")[0]  # netconvert appends #0, #1... when it splits one original way
    return base if base in ways else None


def _extend_chain(seg_id, ways, out_edges, in_edges, real_junctions):
    """Walks outward from seg_id in both directions, stopping at any real junction node."""
    chain = [seg_id]
    seen = {seg_id}

    node = ways[seg_id][0]
    while node not in real_junctions and len(in_edges.get(node, [])) == 1:
        _prev_node, prev_seg = in_edges[node][0]
        if prev_seg in seen:
            break  # guard against a cycle; shouldn't happen on real road geometry
        chain.insert(0, prev_seg)
        seen.add(prev_seg)
        node = ways[prev_seg][0]

    node = ways[seg_id][-1]
    while node not in real_junctions and len(out_edges.get(node, [])) == 1:
        _next_node, next_seg = out_edges[node][0]
        if next_seg in seen:
            break
        chain.append(next_seg)
        seen.add(next_seg)
        node = ways[next_seg][-1]

    return chain


def build_edge_to_segments(net_xml_path, ways_json_path):
    """Returns {sumo_edge_id: [original_segment_id, ...] | None} for every non-internal edge.

    None means the edge's id (even with a trailing #N stripped) isn't among
    the original segment_ids at all -- normally means net_xml_path and
    ways_json_path came from different builds.
    """
    ways = load_ways(ways_json_path)
    out_edges, in_edges = _build_micro_graph(ways)

    root = ET.parse(net_xml_path).getroot()
    real_junctions = _real_junction_nodes(root)

    result = {}
    for edge in root.findall("edge"):
        if edge.get("function") == "internal":
            continue
        edge_id = edge.get("id")
        seed = _own_segment_id(edge_id, ways)
        result[edge_id] = _extend_chain(seed, ways, out_edges, in_edges, real_junctions) if seed else None
    return result


def save_edge_to_segments(mapping, out_path):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(mapping, f, indent=1)
