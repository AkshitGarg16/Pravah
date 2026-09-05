"""Builds a Gazebo (gz-sim) SDF world from the real SUMO road network.

This is the render substrate the TraCI->Gazebo pose bridge (bridge.py) puts
moving vehicles onto -- Gazebo never simulates traffic itself, it only
renders positions SUMO already computed, so the world only needs to *look*
like the road network, not behave like one: no lane logic, no collision
geometry on the roads, nothing physics needs to reason about. Every road
segment is a flat box visual with no <collision> at all, which is why a
network with hundreds of shape points renders as one lightweight static
model rather than hundreds of physics bodies.

Verified against the real gz-sim 8.13 install on this machine before
building this (see chat): headless rendering works via
`gz sim -s -r --headless-rendering` with no GL/EGL issues, and the
resulting camera topics carry real (non-blank) pixel data retrievable from
Python via gz.transport13 + gz.msgs10.
"""

import math
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROAD_HEIGHT = 0.04
ROAD_Z = ROAD_HEIGHT / 2 + 0.01  # sits just above the ground plane, no z-fighting
MIN_ROAD_WIDTH = 3.0
DEFAULT_LANE_WIDTH = 3.2

STANDARD_PLUGINS = [
    ("gz-sim-physics-system", "gz::sim::systems::Physics"),
    ("gz-sim-sensors-system", "gz::sim::systems::Sensors"),
    ("gz-sim-scene-broadcaster-system", "gz::sim::systems::SceneBroadcaster"),
    ("gz-sim-user-commands-system", "gz::sim::systems::UserCommands"),
]

# Without a <gui> block, gz-sim falls back to its own default GUI config
# (confirmed by reading /usr/share/gz/gz-sim8/gui/gui.config and the user's
# own ~/.gz/sim/8/gui.config directly), whose MinimalScene camera_pose is
# hardcoded to "-6 0 6 0 0.5 0" -- a view meant for content built near world
# origin. This network's real SUMO coordinates sit hundreds of meters away
# from the origin (confirmed: the ITO world's own ground plane doesn't even
# extend down to y=0), so every launch opened looking at empty space,
# nowhere near the junction -- confirmed as the actual cause of "unable to
# see visually appealing difference [between two runs]": the user had to
# freshly fly the camera to a different, not-necessarily-consistent framing
# on every single launch, or may not have found the junction at all.
#
# GUI_TEMPLATE is gz-sim's own default plugin set, copied verbatim from
# that same gui.config (also matches the <gui> block structure of a real
# shipped world, /usr/share/gz/gz-sim8/worlds/boundingbox_camera.sdf) --
# not a stripped-down reimplementation, so every existing default tool
# (World control, World stats, Entity tree, Screenshot, Spawn, Shapes,
# etc.) stays exactly as before. The only change from gz-sim's own default
# is {camera_pose}, computed in _camera_pose() below from where the scene
# actually is instead of hardcoded "-6 0 6 0 0.5 0".
GUI_TEMPLATE = """<gui fullscreen="0">
<plugin filename="MinimalScene" name="3D View">
  <gz-gui>
    <title>3D View</title>
    <property type="bool" key="showTitleBar">false</property>
    <property type="string" key="state">docked</property>
  </gz-gui>
  <engine>ogre2</engine>
  <scene>scene</scene>
  <ambient_light>0.4 0.4 0.4</ambient_light>
  <background_color>0.8 0.8 0.8</background_color>
  <camera_pose>{camera_pose}</camera_pose>
</plugin>
<plugin filename="EntityContextMenuPlugin" name="Entity context menu">
  <gz-gui>
    <property key="state" type="string">floating</property>
    <property key="width" type="double">5</property>
    <property key="height" type="double">5</property>
    <property key="showTitleBar" type="bool">false</property>
  </gz-gui>
</plugin>
<plugin filename="GzSceneManager" name="Scene Manager">
  <gz-gui>
    <property key="resizable" type="bool">false</property>
    <property key="width" type="double">5</property>
    <property key="height" type="double">5</property>
    <property key="state" type="string">floating</property>
    <property key="showTitleBar" type="bool">false</property>
  </gz-gui>
</plugin>
<plugin filename="InteractiveViewControl" name="Interactive view control">
  <gz-gui>
    <property key="resizable" type="bool">false</property>
    <property key="width" type="double">5</property>
    <property key="height" type="double">5</property>
    <property key="state" type="string">floating</property>
    <property key="showTitleBar" type="bool">false</property>
  </gz-gui>
</plugin>
<plugin filename="CameraTracking" name="Camera Tracking">
  <gz-gui>
    <property key="resizable" type="bool">false</property>
    <property key="width" type="double">5</property>
    <property key="height" type="double">5</property>
    <property key="state" type="string">floating</property>
    <property key="showTitleBar" type="bool">false</property>
  </gz-gui>
</plugin>
<plugin filename="MarkerManager" name="Marker manager">
  <gz-gui>
    <property key="resizable" type="bool">false</property>
    <property key="width" type="double">5</property>
    <property key="height" type="double">5</property>
    <property key="state" type="string">floating</property>
    <property key="showTitleBar" type="bool">false</property>
  </gz-gui>
</plugin>
<plugin filename="SelectEntities" name="Select Entities">
  <gz-gui>
    <property key="resizable" type="bool">false</property>
    <property key="width" type="double">5</property>
    <property key="height" type="double">5</property>
    <property key="state" type="string">floating</property>
    <property key="showTitleBar" type="bool">false</property>
  </gz-gui>
</plugin>
<plugin filename="Spawn" name="Spawn Entities">
  <gz-gui>
    <property key="resizable" type="bool">false</property>
    <property key="width" type="double">5</property>
    <property key="height" type="double">5</property>
    <property key="state" type="string">floating</property>
    <property key="showTitleBar" type="bool">false</property>
  </gz-gui>
</plugin>
<plugin filename="VisualizationCapabilities" name="Visualization Capabilities">
  <gz-gui>
    <property key="resizable" type="bool">false</property>
    <property key="width" type="double">5</property>
    <property key="height" type="double">5</property>
    <property key="state" type="string">floating</property>
    <property key="showTitleBar" type="bool">false</property>
  </gz-gui>
</plugin>
<plugin filename="WorldControl" name="World control">
  <gz-gui>
    <title>World control</title>
    <property type="bool" key="showTitleBar">false</property>
    <property type="bool" key="resizable">false</property>
    <property type="double" key="height">72</property>
    <property type="double" key="z">1</property>
    <property type="string" key="state">floating</property>
    <anchors target="3D View">
      <line own="left" target="left"/>
      <line own="bottom" target="bottom"/>
    </anchors>
  </gz-gui>
  <play_pause>true</play_pause>
  <step>true</step>
  <start_paused>true</start_paused>
  <use_event>true</use_event>
</plugin>
<plugin filename="WorldStats" name="World stats">
  <gz-gui>
    <title>World stats</title>
    <property type="bool" key="showTitleBar">false</property>
    <property type="bool" key="resizable">false</property>
    <property type="double" key="height">110</property>
    <property type="double" key="width">290</property>
    <property type="double" key="z">1</property>
    <property type="string" key="state">floating</property>
    <anchors target="3D View">
      <line own="right" target="right"/>
      <line own="bottom" target="bottom"/>
    </anchors>
  </gz-gui>
  <sim_time>true</sim_time>
  <real_time>true</real_time>
  <real_time_factor>true</real_time_factor>
  <iterations>true</iterations>
</plugin>
<plugin filename="Shapes" name="Shapes">
  <gz-gui>
    <property key="resizable" type="bool">false</property>
    <property key="x" type="double">0</property>
    <property key="y" type="double">0</property>
    <property key="width" type="double">300</property>
    <property key="height" type="double">50</property>
    <property key="state" type="string">floating</property>
    <property key="showTitleBar" type="bool">false</property>
    <property key="cardBackground" type="string">#666666</property>
  </gz-gui>
</plugin>
<plugin filename="Lights" name="Lights">
  <gz-gui>
    <property key="resizable" type="bool">false</property>
    <property key="x" type="double">300</property>
    <property key="y" type="double">0</property>
    <property key="width" type="double">150</property>
    <property key="height" type="double">50</property>
    <property key="state" type="string">floating</property>
    <property key="showTitleBar" type="bool">false</property>
    <property key="cardBackground" type="string">#666666</property>
  </gz-gui>
</plugin>
<plugin filename="TransformControl" name="Transform control">
  <gz-gui>
    <property key="resizable" type="bool">false</property>
    <property key="x" type="double">0</property>
    <property key="y" type="double">50</property>
    <property key="width" type="double">250</property>
    <property key="height" type="double">50</property>
    <property key="state" type="string">floating</property>
    <property key="showTitleBar" type="bool">false</property>
    <property key="cardBackground" type="string">#777777</property>
  </gz-gui>
</plugin>
<plugin filename="Screenshot" name="Screenshot">
  <gz-gui>
    <property key="resizable" type="bool">false</property>
    <property key="x" type="double">250</property>
    <property key="y" type="double">50</property>
    <property key="width" type="double">50</property>
    <property key="height" type="double">50</property>
    <property key="state" type="string">floating</property>
    <property key="showTitleBar" type="bool">false</property>
    <property key="cardBackground" type="string">#777777</property>
  </gz-gui>
</plugin>
<plugin filename="CopyPaste" name="CopyPaste">
  <gz-gui>
    <property key="resizable" type="bool">false</property>
    <property key="x" type="double">300</property>
    <property key="y" type="double">50</property>
    <property key="width" type="double">100</property>
    <property key="height" type="double">50</property>
    <property key="state" type="string">floating</property>
    <property key="showTitleBar" type="bool">false</property>
    <property key="cardBackground" type="string">#777777</property>
  </gz-gui>
</plugin>
<plugin filename="ComponentInspector" name="Component inspector">
  <gz-gui>
    <property type="bool" key="showTitleBar">false</property>
    <property type="string" key="state">docked</property>
  </gz-gui>
</plugin>
<plugin filename="EntityTree" name="Entity tree">
  <gz-gui>
    <property type="bool" key="showTitleBar">false</property>
    <property type="string" key="state">docked</property>
  </gz-gui>
</plugin>
</gui>"""


def _camera_pose(tls_positions, xmin, ymin, xmax, ymax, center, radius):
    """Where the GUI's default camera should start: an oblique view looking
    at the actual junction instead of gz-sim's hardcoded "-6 0 6" (near
    world origin, nowhere near this network's real SUMO coordinates).

    Looks at `center` when the world is center/radius-scoped -- that's the
    junction the caller explicitly asked to focus on (e.g. the real ITO
    junction, cluster_2_72), and the right target even when other,
    unrelated signals also happen to fall within the same radius. A first
    version of this averaged all visible TLS positions instead, which
    silently broke for the real ITO world: its second TLS (node "13") sits
    ~226m away, at a real y of 579 vs ITO's own 805, so the average landed
    at neither junction -- caught by checking the actual generated
    camera_pose against the real marker coordinates, not assumed correct.
    Only falls back to the TLS average (or, failing that, the scene's own
    bounding-box centroid) when there's no explicit center at all -- a
    whole-network build has no single obvious focal point.

    Offset/height scale off `radius` when scoped; empirically validated at
    radius=300 (see chat: a probe camera at this exact offset/height from
    the real ITO junction produced a clear, well-framed shot of the
    junction and its street light) -- proportional scaling for other radii
    is a reasonable, not separately re-validated, extrapolation. Without a
    radius (whole-network build), falls back to a fraction of the scene's
    own diagonal instead.
    """
    if center is not None:
        look_x, look_y = center
    elif tls_positions:
        look_x = sum(x for x, y in tls_positions.values()) / len(tls_positions)
        look_y = sum(y for x, y in tls_positions.values()) / len(tls_positions)
    else:
        look_x, look_y = (xmin + xmax) / 2, (ymin + ymax) / 2

    if radius is not None:
        offset, height = radius * 0.05, radius * 0.083  # -> 15, ~25 at radius=300 (the validated case)
    else:
        diagonal = math.hypot(xmax - xmin, ymax - ymin)
        offset, height = diagonal * 0.05, diagonal * 0.04

    cam_x, cam_y = look_x - offset, look_y - offset
    yaw = math.atan2(look_y - cam_y, look_x - cam_x)  # face back toward the look-at point
    return f"{cam_x:.2f} {cam_y:.2f} {height:.2f} 0 0.5 {yaw:.4f}"


def _load_sumolib():
    sumo_home = os.environ.get("SUMO_HOME")
    if not sumo_home:
        raise RuntimeError("SUMO_HOME not set -- needed to read the .net.xml (see README.txt section 6).")
    sys.path.insert(0, os.path.join(sumo_home, "tools"))
    import sumolib
    return sumolib


def _within_radius(x, y, center, radius):
    if center is None or radius is None:
        return True
    cx, cy = center
    return math.hypot(x - cx, y - cy) <= radius


def _road_segments(net, center=None, radius=None):
    """Yields (mid_x, mid_y, length, yaw, width) for every consecutive point
    pair in every edge's shape whose midpoint falls within `radius` of
    `center` (both edges of the whole network when center/radius are
    None) -- one flat box per polyline segment. withInternal=True (matches
    render.py's fix) so junction interiors are covered too, not just the
    approach edges up to each junction boundary.
    """
    for edge in net.getEdges():
        width = max(MIN_ROAD_WIDTH, edge.getLaneNumber() * DEFAULT_LANE_WIDTH)
        shape = edge.getShape()
        for (x1, y1), (x2, y2) in zip(shape, shape[1:]):
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            if length < 0.01:
                continue  # degenerate zero-length segment, skip
            mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
            if _within_radius(mid_x, mid_y, center, radius):
                yield (mid_x, mid_y, length, math.atan2(dy, dx), width)


# A real street-light pole for every TLS marker, so it reads as "a signal
# mounted on a pole at this junction" instead of a box floating in open
# air. Two candidates were tried and empirically checked (spawn under a
# camera, read the actual rendered frame -- see chat) before picking this
# one, the same discipline used for the vehicle-model calibration:
#   - OpenRobotics/Stop light post -- thematically ideal (an actual
#     signal-head fixture, not a generic lamp), but its SDF nests two
#     <include>s of OpenRobotics/Stop light via a bare `model://stop_light`
#     URI, which this gz-sim build's resource resolver can't find (logged
#     "Unable to find uri[model://stop_light]"), so the whole model never
#     spawns at all. Its sub-model, Stop light, was also checked completely
#     standalone: even after fixing a confirmed real packaging bug (its
#     .mtl's `map_Kd stop_light.png` pointing at a file one directory away
#     from where it actually ships, same class of bug already hit and
#     fixed for the bicycle/bus vehicle models this session), it still
#     rendered nothing at all -- ruled out as a deeper, unresolved issue in
#     that specific mesh, not chased further (bounded effort, same as the
#     asphalt-texture investigation).
#   - OpenRobotics/Lamp Post -- single self-contained mesh, no nested
#     includes, the same include mechanism already proven to work for all
#     four vehicle models. Empirically confirmed rendering correctly: a
#     real ~8m pole with a curved arm and lamp head, genuinely resembling a
#     street light. This is the one actually used.
# Our own small colored box stays the thing that's actually recolored live
# (see tls_marker_visual) -- not gambling the already-verified-correct
# color-cycling behavior on live-recoloring a visual buried inside a Fuel
# model we don't control the internal naming of.
STREET_LIGHT_POLE_URI = "https://fuel.gazebosim.org/1.0/OpenRobotics/models/Lamp Post"
SIGNAL_MARKER_SIZE = 0.4       # a small signal head, not a second pole -- the pole mesh now provides the height
SIGNAL_MARKER_Z = 3.0          # roughly mast-arm signal height, well below the pole's own decorative lamp head
SIGNAL_MARKER_OFFSET = 0.4     # meters off the pole's own center, so the marker box doesn't clip through the pole mesh


def _street_light_pole(parent_model, x, y):
    """Adds a decorative Lamp Post <include> to `parent_model` at world (x, y),
    ground level. Purely visual -- static, no collision, never recolored."""
    include = ET.SubElement(parent_model, "include")
    ET.SubElement(include, "name").text = "pole"
    ET.SubElement(include, "uri").text = STREET_LIGHT_POLE_URI
    ET.SubElement(include, "pose").text = f"{x:.3f} {y:.3f} 0 0 0 0"
    ET.SubElement(include, "static").text = "true"


def tls_model_name(tls_id, approach_idx):
    return f"tls_{tls_id}_{approach_idx}"


def tls_marker_visual(tls_id, approach_idx):
    """The fully-scoped visual name a live /world/<name>/material_color
    update must target to recolor this one approach's marker -- confirmed
    live against the real gz-sim install (see chat) that MaterialColor
    only takes effect against entity.type=VISUAL with this scoped
    model::link::visual form; targeting the MODEL itself is silently
    ignored.

    One marker per approach, not one per TLS: a real junction shows a
    different color per direction at the same time (e.g. north-south
    green while east-west red), so a single shared marker can only ever
    honestly represent one of them -- confirmed live this was exactly why
    "the traffic lights only show in one direction in gazebo" (see chat):
    cluster_2_72 has 3 real approach edges, but the old single marker only
    ever read state[0], silently always showing one specific approach's
    color (sometimes matching a second approach by coincidence) while a
    third approach -- always the opposite color -- had no visual at all.
    """
    return f"{tls_model_name(tls_id, approach_idx)}::link::visual"


def tls_approaches(net, tls_id):
    """One entry per distinct incoming approach edge this TLS controls:
    {"edge_id", "link_index", "x", "y"}. link_index is a representative
    controlled-link index for that edge (its first one) -- the character
    at that position in a live traci.trafficlight.getRedYellowGreenState()
    string is that whole approach's current color, since every link on
    the same edge/direction changes color together in netconvert's
    generated programs. (x, y) is that lane's own end point, i.e. where it
    actually meets the junction -- used to place that approach's own
    marker+pole distinctly from every other approach's, instead of one
    shared point at the junction center.

    Built from sumolib.net.TLS.getConnections() (in_lane, out_lane,
    link_index) on the static .net.xml -- the same (in, out, index) triple
    TraCI's own getControlledLinks() exposes live, so a world built once
    here stays correctly indexed against any later live run's RYG state
    string without needing a running simulation at build time.
    """
    tls = net.getTLS(tls_id)
    approaches = {}
    for in_lane, _out_lane, link_index in tls.getConnections():
        edge_id = in_lane.getEdge().getID()
        if edge_id not in approaches:
            x, y = in_lane.getShape()[-1]
            approaches[edge_id] = {"edge_id": edge_id, "link_index": link_index, "x": x, "y": y}
    return list(approaches.values())


def _box_visual(parent, name, x, y, z, yaw, length, width, height, rgba):
    visual = ET.SubElement(parent, "visual", name=name)
    ET.SubElement(visual, "pose").text = f"{x:.3f} {y:.3f} {z:.3f} 0 0 {yaw:.4f}"
    geometry = ET.SubElement(visual, "geometry")
    box = ET.SubElement(geometry, "box")
    ET.SubElement(box, "size").text = f"{length:.3f} {width:.3f} {height:.3f}"
    material = ET.SubElement(visual, "material")
    ET.SubElement(material, "ambient").text = rgba
    ET.SubElement(material, "diffuse").text = rgba


# A real photographic asphalt texture (Fuel: OpenRobotics/Asphalt Plane)
# was tried here and reverted. Both the legacy Ogre material-script form
# (<material><script>, "vrc/asphalt") and Ogre2's own native PBR form
# (<material><pbr><metal><albedo_map>) rendered the road as solid black in
# this build -- checked with scene ambient light and a sky added, and with
# the texture referenced both by its Fuel https:// URI and by a verified-
# correct local file path, all four combinations came back solid black
# with no error logged anywhere. The texture file itself is fine (opened
# and inspected it directly: an ordinary photographic gray-asphalt PNG).
# This is a real, unresolved rendering-pipeline issue in this gz-sim
# build/config, not a configuration mistake caught and fixed -- worth
# revisiting with more time or a different Gazebo build, not chased
# further here. ROAD_COLOR is a deliberately dark, desaturated asphalt
# gray standing in for it, using the same solid-color mechanism every
# other visual in this file already uses successfully.
ROAD_COLOR = "0.12 0.12 0.14 1"


def _road_visual(parent, name, x, y, z, yaw, length, width, height):
    """Road segment box, colored like asphalt -- see the note above for why
    this isn't a real texture."""
    _box_visual(parent, name, x, y, z, yaw, length, width, height, ROAD_COLOR)


def build_world(net_xml_path, tls_positions=None, world_name="pravah", center=None, radius=None):
    """Returns an ElementTree for a complete SDF world: ground plane, sun,
    the road network (one static model, one visual per polyline segment,
    no collision geometry), and one small marker post per traffic light
    position, each its own model (see tls_marker_visual) so it can be
    recolored live to match the real signal state.

    center=(x, y), radius=meters scopes the world to just that area of the
    network instead of the whole thing -- e.g. the real ITO junction and
    its immediate approaches, not the full 132-edge corridor. Both None
    (the default) builds the whole network, as before.
    """
    sumolib = _load_sumolib()
    net = sumolib.net.readNet(str(net_xml_path), withInternal=True)
    if center is not None and radius is not None:
        cx, cy = center
        xmin, xmax = cx - radius, cx + radius
        ymin, ymax = cy - radius, cy + radius
    else:
        xmin, ymin, xmax, ymax = net.getBoundary()

    sdf = ET.Element("sdf", version="1.6")
    world = ET.SubElement(sdf, "world", name=world_name)

    for filename, name in STANDARD_PLUGINS:
        plugin = ET.SubElement(world, "plugin", filename=filename, name=name)
        if name.endswith("Sensors"):
            ET.SubElement(plugin, "render_engine").text = "ogre2"

    light = ET.SubElement(world, "light", type="directional", name="sun")
    ET.SubElement(light, "pose").text = f"{(xmin + xmax) / 2:.1f} {(ymin + ymax) / 2:.1f} 200 0 0 0"
    ET.SubElement(light, "diffuse").text = "0.9 0.9 0.9 1"
    ET.SubElement(light, "specular").text = "0.3 0.3 0.3 1"
    ET.SubElement(light, "direction").text = "-0.3 0.2 -0.9"
    ET.SubElement(light, "cast_shadows").text = "false"  # cheap: no shadow maps on hundreds of road boxes

    ground = ET.SubElement(world, "model", name="ground")
    ET.SubElement(ground, "static").text = "true"
    g_link = ET.SubElement(ground, "link", name="link")
    g_visual = ET.SubElement(g_link, "visual", name="visual")
    ET.SubElement(g_visual, "pose").text = f"{(xmin + xmax) / 2:.1f} {(ymin + ymax) / 2:.1f} 0 0 0 0"
    g_geom = ET.SubElement(g_visual, "geometry")
    g_plane = ET.SubElement(g_geom, "plane")
    ET.SubElement(g_plane, "size").text = f"{(xmax - xmin) + 40:.1f} {(ymax - ymin) + 40:.1f}"
    g_mat = ET.SubElement(g_visual, "material")
    # A warm, earthy gray rather than neutral gray -- reads as ground/verge
    # next to the (now genuinely textured, see _road_visual) asphalt roads,
    # instead of both surfaces looking like the same flat material.
    ET.SubElement(g_mat, "ambient").text = "0.32 0.29 0.24 1"
    ET.SubElement(g_mat, "diffuse").text = "0.32 0.29 0.24 1"
    g_collision = ET.SubElement(g_link, "collision", name="collision")
    ET.SubElement(g_collision, "geometry").append(
        ET.fromstring(f"<plane><size>{(xmax - xmin) + 40:.1f} {(ymax - ymin) + 40:.1f}</size></plane>")
    )

    roads = ET.SubElement(world, "model", name="road_network")
    ET.SubElement(roads, "static").text = "true"
    r_link = ET.SubElement(roads, "link", name="link")
    for i, (x, y, length, yaw, width) in enumerate(_road_segments(net, center=center, radius=radius)):
        _road_visual(r_link, f"seg_{i}", x, y, ROAD_Z, yaw, length, width, ROAD_HEIGHT)

    visible_tls_positions = {}
    for tls_id, (x, y) in (tls_positions or {}).items():
        if not _within_radius(x, y, center, radius):
            continue
        visible_tls_positions[tls_id] = (x, y)
        # One marker+pole per real approach direction, not one shared marker
        # for the whole junction -- see tls_marker_visual's docstring for
        # why (a real junction shows a different color per direction at
        # the same time; one shared marker can only ever honestly show
        # one of them).
        for i, approach in enumerate(tls_approaches(net, tls_id)):
            ax, ay = approach["x"], approach["y"]
            marker = ET.SubElement(world, "model", name=tls_model_name(tls_id, i))
            ET.SubElement(marker, "static").text = "true"
            t_link = ET.SubElement(marker, "link", name="link")
            _box_visual(  # our own live-recolorable signal head -- starts red
                t_link, "visual", ax + SIGNAL_MARKER_OFFSET, ay, SIGNAL_MARKER_Z, 0,
                SIGNAL_MARKER_SIZE, SIGNAL_MARKER_SIZE, SIGNAL_MARKER_SIZE, "0.9 0.15 0.15 1",
            )
            _street_light_pole(marker, ax, ay)  # decorative pole, never recolored

    # Only visible_tls_positions (post-radius-filter), not the raw
    # tls_positions argument, so the default camera looks at what's
    # actually in this scoped world, not a signal that got filtered out.
    camera_pose = _camera_pose(visible_tls_positions, xmin, ymin, xmax, ymax, center, radius)
    world.append(ET.fromstring(GUI_TEMPLATE.format(camera_pose=camera_pose)))

    return ET.ElementTree(sdf)


def write_world(net_xml_path, out_path, tls_positions=None, world_name="pravah", center=None, radius=None):
    tree = build_world(net_xml_path, tls_positions=tls_positions, world_name=world_name, center=center, radius=radius)
    ET.indent(tree, space="  ")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_path, encoding="UTF-8", xml_declaration=True)
    return out_path
