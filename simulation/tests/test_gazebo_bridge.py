"""Tests for the Gazebo bridge's pure-logic pieces.

No gz-sim / running world needed for these -- see the chat transcript for
the live end-to-end verification (headless rendering, real image data,
live pose sync against an actual running world) that isn't practical to
re-run as an automated test on every CI machine, since it depends on a
GPU-backed render context.
"""

import math
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pravah.gazebo_bridge.bridge import STATE_COLOR, sumo_angle_to_yaw
from pravah.gazebo_bridge.world_builder import (
    STREET_LIGHT_POLE_URI,
    _camera_pose,
    _road_segments,
    _street_light_pole,
    tls_approaches,
    tls_marker_visual,
    tls_model_name,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

needs_sumo = pytest.mark.skipif(
    not os.environ.get("SUMO_HOME"),
    reason="SUMO_HOME not set -- export SUMO_HOME=/home/kreacher/sumo-src (see README.txt) to run this test",
)


class FakeShape(list):
    pass


class FakeEdge:
    def __init__(self, lane_number, shape):
        self._lane_number = lane_number
        self._shape = shape

    def getLaneNumber(self):
        return self._lane_number

    def getShape(self):
        return self._shape


class FakeNet:
    def __init__(self, edges):
        self._edges = edges

    def getEdges(self):
        return self._edges


def test_sumo_north_maps_to_positive_y_yaw():
    # SUMO 0 deg = North = +Y. Standard math yaw 90 deg also points along +Y.
    yaw = sumo_angle_to_yaw(0.0)
    assert math.isclose(yaw, math.radians(90), abs_tol=1e-9)


def test_sumo_east_maps_to_positive_x_yaw():
    # SUMO 90 deg = East = +X. Standard math yaw 0 deg points along +X.
    yaw = sumo_angle_to_yaw(90.0)
    assert math.isclose(yaw, 0.0, abs_tol=1e-9)


def test_road_segments_skips_degenerate_zero_length_points():
    # A duplicated point (rounding artifact) must not produce a zero-length box.
    net = FakeNet([FakeEdge(2, [(0, 0), (0, 0), (10, 0)])])
    segments = list(_road_segments(net))
    assert len(segments) == 1
    x, y, length, yaw, width = segments[0]
    assert math.isclose(length, 10.0)
    assert math.isclose(x, 5.0) and math.isclose(y, 0.0)
    assert math.isclose(yaw, 0.0, abs_tol=1e-9)  # pointing along +X


def test_road_segments_width_from_lane_count():
    net = FakeNet([FakeEdge(3, [(0, 0), (10, 0)])])
    _, _, _, _, width = next(_road_segments(net))
    assert math.isclose(width, 3 * 3.2)


def test_road_segments_enforces_minimum_width():
    net = FakeNet([FakeEdge(0, [(0, 0), (10, 0)])])  # degenerate 0-lane edge shouldn't collapse to 0 width
    _, _, _, _, width = next(_road_segments(net))
    assert width >= 3.0


def test_road_segments_scoped_to_a_radius_excludes_far_segments():
    net = FakeNet([
        FakeEdge(1, [(0, 0), (10, 0)]),        # midpoint (5,0) -- near the center
        FakeEdge(1, [(1000, 1000), (1010, 1000)]),  # midpoint far away
    ])
    scoped = list(_road_segments(net, center=(0, 0), radius=50))
    assert len(scoped) == 1
    assert scoped[0][:2] == (5.0, 0.0)


def test_road_segments_unscoped_by_default():
    net = FakeNet([
        FakeEdge(1, [(0, 0), (10, 0)]),
        FakeEdge(1, [(1000, 1000), (1010, 1000)]),
    ])
    assert len(list(_road_segments(net))) == 2


def test_tls_marker_visual_name_is_the_scoped_form_material_color_needs():
    # Confirmed live (see chat): MaterialColor only takes effect against
    # entity.type=VISUAL with this exact model::link::visual scoped name --
    # targeting the model itself is silently ignored. One marker per
    # approach (index), not one per TLS -- see the docstring for why.
    assert tls_model_name("cluster_2_72", 0) == "tls_cluster_2_72_0"
    assert tls_marker_visual("cluster_2_72", 0) == "tls_cluster_2_72_0::link::visual"
    assert tls_marker_visual("cluster_2_72", 1) == "tls_cluster_2_72_1::link::visual"


def test_street_light_pole_includes_the_verified_working_fuel_model():
    import xml.etree.ElementTree as ET

    parent = ET.Element("model", name="tls_cluster_2_72")
    _street_light_pole(parent, 264.68, 805.06)
    include = parent.find("include")
    assert include is not None
    assert include.find("uri").text == STREET_LIGHT_POLE_URI
    assert include.find("pose").text == "264.680 805.060 0 0 0 0"
    assert include.find("static").text == "true"


def test_camera_pose_prefers_the_explicit_center_over_a_tls_average():
    # Real bug caught this session: the real ITO world has a second TLS
    # (node "13") ~226m away from the actual focal junction -- averaging
    # every visible TLS position landed the default camera at neither
    # junction. `center` (what the caller explicitly asked to focus on)
    # must win whenever it's given, regardless of what other TLS also fall
    # within the same radius.
    tls_positions = {"cluster_2_72": (264.68, 805.06), "13": (312.37, 579.22)}
    pose = _camera_pose(tls_positions, 0, 0, 0, 0, center=(264.68, 805.06), radius=300)
    x, y, z, roll, pitch, yaw = (float(v) for v in pose.split())
    # empirically validated framing (see chat): offset -15/-15, height ~25
    assert math.isclose(x, 249.68, abs_tol=0.5)
    assert math.isclose(y, 790.06, abs_tol=0.5)
    assert math.isclose(z, 24.9, abs_tol=0.5)
    assert math.isclose(yaw, math.pi / 4, abs_tol=0.01)  # facing back toward the look-at point


def test_camera_pose_falls_back_to_tls_average_without_an_explicit_center():
    pose = _camera_pose({"a": (100.0, 100.0), "b": (200.0, 200.0)}, 0, 0, 0, 0, center=None, radius=300)
    x, y, z, roll, pitch, yaw = (float(v) for v in pose.split())
    assert math.isclose(x, 150.0 - 15.0, abs_tol=0.5)
    assert math.isclose(y, 150.0 - 15.0, abs_tol=0.5)


def test_camera_pose_falls_back_to_bounding_box_centroid_with_no_tls_at_all():
    pose = _camera_pose({}, 0.0, 0.0, 100.0, 200.0, center=None, radius=None)
    x, y, z, roll, pitch, yaw = (float(v) for v in pose.split())
    diagonal = math.hypot(100.0, 200.0)
    assert math.isclose(x, 50.0 - diagonal * 0.05, abs_tol=0.5)
    assert math.isclose(y, 100.0 - diagonal * 0.05, abs_tol=0.5)


@needs_sumo
def test_build_world_embeds_a_gui_camera_pose_near_the_real_junction():
    import xml.etree.ElementTree as ET

    from pravah.gazebo_bridge.world_builder import build_world

    tree = build_world(
        REPO_ROOT / "sumo/network/pravah.net.xml",
        tls_positions={"cluster_2_72": (264.68, 805.06), "13": (312.37, 579.22)},
        center=(264.68, 805.06), radius=300,
    )
    root = tree.getroot()
    gui = root.find(".//gui")
    assert gui is not None, "world must override gz-sim's default near-origin camera"
    camera_pose = gui.find(".//camera_pose")
    assert camera_pose is not None
    x, y = (float(v) for v in camera_pose.text.split()[:2])
    # near the real ITO junction (264.68, 805.06), not gz-sim's "-6 0 6" default
    assert math.hypot(x - 264.68, y - 805.06) < 50


@needs_sumo
def test_tls_approaches_finds_all_three_real_ito_approaches():
    # Real bug this fixes: cluster_2_72 has 3 distinct incoming approach
    # edges, but the old single shared marker only ever read state[0] --
    # confirmed live this always showed one specific approach (sometimes
    # coincidentally matching a second one), while the third approach
    # (edge ...2709368832, always the *opposite* color) had no marker at
    # all -- "the traffic lights only show in one direction in gazebo".
    import sumolib

    net = sumolib.net.readNet(str(REPO_ROOT / "sumo/network/pravah.net.xml"), withInternal=True)
    approaches = tls_approaches(net, "cluster_2_72")
    edge_ids = {a["edge_id"] for a in approaches}
    assert edge_ids == {"1285520201747431424", "1285520202073505792", "1285520202709368832"}
    # every approach's position is near the real junction (264.68, 805.06), not some default/shared point
    for a in approaches:
        assert math.hypot(a["x"] - 264.68, a["y"] - 805.06) < 30
    # link_index must be a real index into the live RYG state string (9 chars for cluster_2_72)
    for a in approaches:
        assert 0 <= a["link_index"] < 9


def test_set_tls_colors_publishes_one_color_per_approach_from_its_own_link_index():
    # Bridge-level check that set_tls_colors reads each approach's OWN
    # character, not always state[0] -- using a fake pub to avoid needing
    # a live gz-sim connection.
    from pravah.gazebo_bridge.bridge import GazeboBridge

    bridge = GazeboBridge.__new__(GazeboBridge)  # skip __init__'s real transport.Node()/advertise()
    published = []

    class FakePub:
        def publish(self, msg):
            published.append((msg.entity.name, (msg.ambient.r, msg.ambient.g, msg.ambient.b)))

    bridge.world_name = "pravah"
    bridge.tls_approaches = {"cluster_2_72": [{"link_index": 0}, {"link_index": 3}, {"link_index": 7}]}
    bridge._color_pub = FakePub()
    bridge._last_tls_color = {}

    # phase 0 from the real cluster_2_72 program: "gGgrrrrGg" -- approach 0
    # (index 0) and approach 2 (index 7) are green, approach 1 (index 3) is red.
    bridge.set_tls_colors({"cluster_2_72": "gGgrrrrGg"})

    names = [name for name, _rgb in published]
    assert names == [
        "tls_cluster_2_72_0::link::visual",
        "tls_cluster_2_72_1::link::visual",
        "tls_cluster_2_72_2::link::visual",
    ]
    # protobuf's float fields are 32-bit, so compare with a small tolerance
    colors = dict(published)
    assert colors["tls_cluster_2_72_0::link::visual"] == pytest.approx(STATE_COLOR["green"][:3], abs=1e-6)
    assert colors["tls_cluster_2_72_1::link::visual"] == pytest.approx(STATE_COLOR["red"][:3], abs=1e-6)
    assert colors["tls_cluster_2_72_2::link::visual"] == pytest.approx(STATE_COLOR["green"][:3], abs=1e-6)


def test_set_tls_colors_falls_back_to_a_single_marker_without_an_approach_map():
    from pravah.gazebo_bridge.bridge import GazeboBridge

    bridge = GazeboBridge.__new__(GazeboBridge)
    published = []

    class FakePub:
        def publish(self, msg):
            published.append(msg.entity.name)

    bridge.world_name = "pravah"
    bridge.tls_approaches = {}  # no map for this tls_id at all
    bridge._color_pub = FakePub()
    bridge._last_tls_color = {}

    bridge.set_tls_colors({"13": "gr"})
    assert published == ["tls_13_0::link::visual"]
