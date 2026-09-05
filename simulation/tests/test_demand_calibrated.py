import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pravah.sumo_twin.demand import _fringe_edges, _real_edge_ids

REPO_ROOT = Path(__file__).resolve().parent.parent

needs_sumo = pytest.mark.skipif(
    not os.environ.get("SUMO_HOME"),
    reason="SUMO_HOME not set -- export SUMO_HOME=/home/kreacher/sumo-src (see README.txt) to run this test",
)

NET_XML = """<net>
  <junction id="A" type="dead_end"/>
  <junction id="B" type="priority"/>
  <junction id="C" type="dead_end"/>
  <edge id="e1" from="A" to="B" function=""/>
  <edge id="e2" from="B" to="C" function=""/>
  <edge id="internal_1" function="internal"/>
</net>"""


def test_real_edge_ids_excludes_internal():
    root = ET.fromstring(NET_XML)
    assert _real_edge_ids(root) == {"e1", "e2"}


def test_fringe_edges_are_those_touching_a_dead_end():
    root = ET.fromstring(NET_XML)
    # both e1 (A is dead_end) and e2 (C is dead_end) touch a dead end here
    assert set(_fringe_edges(root)) == {"e1", "e2"}


def test_fringe_edges_falls_back_to_all_real_edges_if_none_are_dead_end():
    root = ET.fromstring("""<net>
      <junction id="A" type="priority"/>
      <junction id="B" type="priority"/>
      <edge id="e1" from="A" to="B" function=""/>
    </net>""")
    assert _fringe_edges(root) == ["e1"]


@needs_sumo
def test_generate_calibrated_routes_against_the_real_network(tmp_path):
    from pravah.sumo_twin.demand import generate_calibrated_routes

    out = generate_calibrated_routes(
        net_path=REPO_ROOT / "sumo/network/pravah.net.xml",
        segment_map_path=REPO_ROOT / "sumo/network/pravah.net.segment_map.json",
        csv_path=REPO_ROOT / "data/pravah_900to1000_balanced.csv",
        route_path=tmp_path / "calibrated.rou.xml",
        duration=3600, interval=300, target_network_vehicles_per_hour=1200, seed=1,
    )
    text = Path(out).read_text()
    vehicle_count = text.count("<vehicle ")
    assert vehicle_count > 500  # some routing failures are expected on a one-way network; most should succeed

    # all four vTypes from VTYPE_MIX should actually show up in a run this size
    for vtype in ("car", "twowheeler", "bus", "truck"):
        assert f'type="{vtype}"' in text


@needs_sumo
def test_hotspot_multiplier_boosts_only_the_named_edges(tmp_path):
    """hotspot_edges/hotspot_multiplier must boost exactly the named edges'
    rate by the multiplier, and leave every other edge's rate identical to
    an unboosted run -- the whole point is a same-density-everywhere-else
    comparison (see the plan / demand.py's docstring)."""
    from pravah.sumo_twin.demand import generate_calibrated_routes

    net_path = REPO_ROOT / "sumo/network/pravah.net.xml"
    segment_map_path = REPO_ROOT / "sumo/network/pravah.net.segment_map.json"
    csv_path = REPO_ROOT / "data/pravah_900to1000_balanced.csv"
    hotspot_edge = "1285520201747431424"  # a real cluster_2_72 (ITO) approach edge

    generate_calibrated_routes(
        net_path=net_path, segment_map_path=segment_map_path, csv_path=csv_path,
        route_path=tmp_path / "plain.rou.xml",
        duration=600, interval=300, target_network_vehicles_per_hour=1200, seed=1,
    )
    generate_calibrated_routes(
        net_path=net_path, segment_map_path=segment_map_path, csv_path=csv_path,
        route_path=tmp_path / "dense.rou.xml",
        duration=600, interval=300, target_network_vehicles_per_hour=1200, seed=1,
        hotspot_edges={hotspot_edge}, hotspot_multiplier=5.0,
    )

    def rates_by_edge(trips_path):
        root = ET.parse(trips_path).getroot()
        return {
            flow.get("from"): float(flow.get("vehsPerHour"))
            for flow in root.findall("flow")
        }

    plain_rates = rates_by_edge(tmp_path / "plain.rou.calibrated_trips.xml")
    dense_rates = rates_by_edge(tmp_path / "dense.rou.calibrated_trips.xml")

    # rates are written with .2f rounding, so compare with a tolerance
    # wide enough for that (not a claim of exact floating-point equality)
    assert hotspot_edge in plain_rates and hotspot_edge in dense_rates
    assert dense_rates[hotspot_edge] == pytest.approx(plain_rates[hotspot_edge] * 5.0, abs=0.05)

    # every non-hotspot edge's rate is untouched
    unaffected = [eid for eid in plain_rates if eid != hotspot_edge and eid in dense_rates]
    assert unaffected  # sanity: the network has other calibrated edges
    for eid in unaffected:
        assert dense_rates[eid] == pytest.approx(plain_rates[eid], abs=0.05)


@needs_sumo
def test_hotspot_multipliers_gives_each_edge_its_own_distinct_multiplier(tmp_path):
    """hotspot_multipliers must apply a DIFFERENT multiplier per edge in
    one run -- the graduated-traffic demo needs cluster_2_72's 3 real
    approaches at visibly different volumes, not all boosted the same."""
    from pravah.sumo_twin.demand import generate_calibrated_routes

    net_path = REPO_ROOT / "sumo/network/pravah.net.xml"
    segment_map_path = REPO_ROOT / "sumo/network/pravah.net.segment_map.json"
    csv_path = REPO_ROOT / "data/pravah_900to1000_balanced.csv"
    # cluster_2_72's 3 real incoming approach edges
    heavy, medium, light = "1285520201747431424", "1285520202073505792", "1285520202709368832"

    generate_calibrated_routes(
        net_path=net_path, segment_map_path=segment_map_path, csv_path=csv_path,
        route_path=tmp_path / "plain.rou.xml",
        duration=600, interval=300, target_network_vehicles_per_hour=1200, seed=1,
    )
    generate_calibrated_routes(
        net_path=net_path, segment_map_path=segment_map_path, csv_path=csv_path,
        route_path=tmp_path / "graduated.rou.xml",
        duration=600, interval=300, target_network_vehicles_per_hour=1200, seed=1,
        hotspot_multipliers={heavy: 10.0, medium: 4.0, light: 1.0},
    )

    def rates_by_edge(trips_path):
        root = ET.parse(trips_path).getroot()
        return {flow.get("from"): float(flow.get("vehsPerHour")) for flow in root.findall("flow")}

    plain_rates = rates_by_edge(tmp_path / "plain.rou.calibrated_trips.xml")
    graduated_rates = rates_by_edge(tmp_path / "graduated.rou.calibrated_trips.xml")

    assert graduated_rates[heavy] == pytest.approx(plain_rates[heavy] * 10.0, abs=0.05)
    assert graduated_rates[medium] == pytest.approx(plain_rates[medium] * 4.0, abs=0.05)
    assert graduated_rates[light] == pytest.approx(plain_rates[light] * 1.0, abs=0.05)
    # the whole point: three visibly different volumes, not one shared level
    assert graduated_rates[heavy] > graduated_rates[medium] > graduated_rates[light]


@needs_sumo
def test_hotspot_multipliers_accepts_a_per_chunk_schedule_for_a_demand_pulse(tmp_path):
    """A hotspot_multipliers value can be a list -- one multiplier per
    time chunk -- for a quiet/surge/quiet demand pulse, not just a flat
    constant boost for the whole run."""
    from pravah.sumo_twin.demand import generate_calibrated_routes

    net_path = REPO_ROOT / "sumo/network/pravah.net.xml"
    segment_map_path = REPO_ROOT / "sumo/network/pravah.net.segment_map.json"
    csv_path = REPO_ROOT / "data/pravah_900to1000_balanced.csv"
    pulse_edge = "1285520201747431424"

    generate_calibrated_routes(
        net_path=net_path, segment_map_path=segment_map_path, csv_path=csv_path,
        route_path=tmp_path / "plain.rou.xml",
        duration=150, interval=30, target_network_vehicles_per_hour=1200, seed=1,
    )
    generate_calibrated_routes(
        net_path=net_path, segment_map_path=segment_map_path, csv_path=csv_path,
        route_path=tmp_path / "pulse.rou.xml",
        duration=150, interval=30, target_network_vehicles_per_hour=1200, seed=1,
        hotspot_multipliers={pulse_edge: [1, 8, 8, 1, 1]},  # 5 chunks of 30s: quiet, surge, surge, quiet, quiet
    )

    def rates_by_edge_and_begin(trips_path):
        root = ET.parse(trips_path).getroot()
        return {
            (flow.get("from"), flow.get("begin")): float(flow.get("vehsPerHour"))
            for flow in root.findall("flow")
        }

    plain = rates_by_edge_and_begin(tmp_path / "plain.rou.calibrated_trips.xml")
    pulse = rates_by_edge_and_begin(tmp_path / "pulse.rou.calibrated_trips.xml")

    for k, begin in enumerate(["0", "30", "60", "90", "120"]):
        expected_mult = [1, 8, 8, 1, 1][k]
        assert pulse[(pulse_edge, begin)] == pytest.approx(plain[(pulse_edge, begin)] * expected_mult, abs=0.05)
