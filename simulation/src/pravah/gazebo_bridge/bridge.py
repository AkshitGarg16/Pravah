"""Live TraCI -> Gazebo pose + signal-color bridge.

Gazebo never simulates traffic here -- it only renders positions SUMO has
already computed. Every vehicle in Gazebo is a purely kinematic, static
model whose pose gets overwritten every sync() call via gz-sim's
/world/<name>/set_pose_vector service; nothing about it is driven by
Gazebo's own physics. This is the same pattern verified end-to-end against
the real gz-sim 8.13 install before writing this module (see chat): a
model's pose really does move when set_pose is called, repeatedly, live.

Vehicles are spawned (EntityFactory /create) the first time their id is
seen and removed (/remove) the step after they're no longer in SUMO's
vehicle list -- mirroring TraCI's own vehicle lifecycle instead of
pre-declaring a fixed fleet.
"""

import math

import gz.transport13 as transport
from gz.msgs10 import boolean_pb2, entity_factory_pb2, entity_pb2, material_color_pb2, pose_v_pb2

from .world_builder import tls_marker_visual

VEHICLE_SDF = """<sdf version="1.6">
<include>
  <uri>{uri}</uri>
  <name>{name}</name>
  <static>true</static>
</include>
</sdf>"""

# (Fuel model URI, yaw_correction radians) per SUMO vType id -- real,
# pre-scaled 3D models fetched from Gazebo Fuel (see chat: this machine
# does have internet access, checked directly rather than assumed),
# replacing the earlier flat-colored boxes. yaw_correction accounts for
# each mesh's own default-forward direction not necessarily matching this
# codebase's +X-at-yaw-0 convention (sumo_angle_to_yaw); empirically
# checked this session via a top-down render with world-axis reference
# markers, not assumed: Hatchback and TruckDelivery already face +X at
# yaw=0, no correction needed. Bus's long axis instead sits along Y at
# yaw=0, needing +90 degrees. No real motorcycle/scooter model exists in
# Fuel's catalog (checked) -- athackst/bicycle is the closest available
# stand-in for "twowheeler", visually a bicycle rather than the
# motorized two-wheelers that actually dominate Indian traffic; its
# orientation looked consistent with the uncorrected models in the same
# verification image, but it was too small in that shot to be fully sure.
# Two of these models had genuine upstream asset-packaging bugs (an OBJ's
# .mtl pointing at a texture file one directory away from where it
# actually was, and a texture reference hard-coded to a different model's
# folder entirely) -- fixed by hand in the local Fuel cache; see the note
# in README.txt, since a fresh `gz fuel download` on another machine would
# hit the same two bugs again.
VEHICLE_MODELS = {
    "car": ("https://fuel.gazebosim.org/1.0/openrobotics/models/Hatchback", 0.0),
    "twowheeler": ("https://fuel.gazebosim.org/1.0/athackst/models/bicycle", 0.0),
    "bus": ("https://fuel.gazebosim.org/1.0/openrobotics/models/Bus", math.pi / 2),
    "truck": ("https://fuel.gazebosim.org/1.0/openrobotics/models/TruckDelivery", 0.0),
}
DEFAULT_MODEL = VEHICLE_MODELS["car"]  # DEFAULT_VEHTYPE / unknown types

# Collapsed at-a-glance signal colors -- same red/yellow/green convention
# the (retired) 2D renderer used, so a screenshot from either would read
# the same way to someone looking at it.
STATE_COLOR = {
    "red": (0.90, 0.15, 0.15, 1.0),
    "yellow": (0.95, 0.75, 0.10, 1.0),
    "green": (0.15, 0.85, 0.35, 1.0),
}


def sumo_angle_to_yaw(sumo_angle_deg):
    """SUMO's vehicle angle is a compass bearing: 0 = North (+Y), clockwise,
    degrees. Gazebo/SDF yaw is standard math convention: 0 = +X axis,
    counter-clockwise, radians. yaw = 90 - bearing is the standard
    compass-to-math-angle conversion; not re-derived empirically here, so
    treat vehicle heading as a documented assumption, not a verified one,
    until checked visually against a known SUMO direction of travel.
    """
    return math.radians(90.0 - sumo_angle_deg)


def _collapse_char(ch):
    """One RYG state-string character -> one at-a-glance color name.

    Used per-approach now, not just on the whole junction's state[0]: a
    junction alternates which movement gets green (e.g. "gGgrrrrGg" then
    "rrrGGGGrr"), so an "any green anywhere in the string wins" rule would
    read as green almost the entire cycle everywhere -- nothing like
    watching an actual traffic light. Reading one specific approach's own
    link character gives that approach's real red -> yellow -> green ->
    yellow -> red cycle.
    """
    ch = ch.lower()
    if ch == "y":
        return "yellow"
    if ch == "g":
        return "green"
    return "red"


class GazeboBridge:
    def __init__(self, world_name="pravah", tls_approaches=None):
        """tls_approaches: {tls_id: [{"link_index": int, ...}, ...]} -- the
        exact same per-approach breakdown (world_builder.tls_approaches(),
        one dict per approach, in the same order) that built this world's
        markers, so set_tls_colors() knows which live RYG state-string
        character belongs to which approach's own marker. Without it for a
        given tls_id, falls back to a single marker (index 0) read from
        state[0] -- the old, single-direction-only behavior; every real TLS
        this session's world_builder.py generates supplies the real map.
        """
        self.world_name = world_name
        self.tls_approaches = tls_approaches or {}
        self.node = transport.Node()
        self._spawned = {}  # vehicle_id -> vtype, so sync() knows each one's height for z-placement
        self._color_pub = self.node.advertise(f"/world/{world_name}/material_color", material_color_pb2.MaterialColor)
        self._last_tls_color = {}  # (tls_id, approach_idx) -> last color name published; not used to skip
                                    # republishing (see set_tls_colors), kept for tests/debugging

    def _request(self, service, req, req_type, resp_type, timeout_ms=2000):
        ok, resp = self.node.request(f"/world/{self.world_name}/{service}", req, req_type, resp_type, timeout_ms)
        return ok and resp.data

    def _spawn(self, vehicle_id, vtype):
        uri, _yaw_correction = VEHICLE_MODELS.get(vtype, DEFAULT_MODEL)
        req = entity_factory_pb2.EntityFactory()
        req.sdf = VEHICLE_SDF.format(uri=uri, name=vehicle_id)
        if self._request("create", req, entity_factory_pb2.EntityFactory, boolean_pb2.Boolean):
            self._spawned[vehicle_id] = vtype

    def _despawn(self, vehicle_id):
        req = entity_pb2.Entity()
        req.name = vehicle_id
        req.type = entity_pb2.Entity.MODEL
        self._request("remove", req, entity_pb2.Entity, boolean_pb2.Boolean)
        self._spawned.pop(vehicle_id, None)

    def sync(self, vehicles):
        """vehicles: iterable of {"id", "x", "y", "angle", "type"} (SUMO's own
        traci.vehicle.getPosition()/getAngle()/getTypeID() -- network x/y
        meters, angle in degrees, type one of VEHICLE_MODELS' keys).
        Spawns new ids, removes ids no longer present, and pushes one
        batched pose update for everything currently active.
        """
        current_ids = set()
        pose_v = pose_v_pb2.Pose_V()

        for v in vehicles:
            vid = v["id"]
            vtype = v.get("type", "car")
            current_ids.add(vid)
            if vid not in self._spawned:
                self._spawn(vid, vtype)

            _uri, yaw_correction = VEHICLE_MODELS.get(vtype, DEFAULT_MODEL)
            pose = pose_v.pose.add()
            pose.name = vid
            pose.position.x = v["x"]
            pose.position.y = v["y"]
            pose.position.z = 0.0  # all four models are authored with their origin at ground level (checked)
            yaw = sumo_angle_to_yaw(v.get("angle", 0.0)) + yaw_correction
            pose.orientation.z = math.sin(yaw / 2)
            pose.orientation.w = math.cos(yaw / 2)

        if pose_v.pose:
            self._request("set_pose_vector", pose_v, pose_v_pb2.Pose_V, boolean_pb2.Boolean)

        for vid in list(self._spawned.keys() - current_ids):
            self._despawn(vid)

    def set_tls_colors(self, tls_states):
        """tls_states: {tls_id: raw RYG state string, e.g. from
        traci.trafficlight.getRedYellowGreenState(tls_id)}. Publishes a
        live color update for every approach marker of every TLS, every
        call -- one publish per approach (see self.tls_approaches), each
        reading its own link_index's character out of that TLS's state
        string, not just state[0] for the whole junction. This is exactly
        the fix for "the traffic lights only show in one direction in
        gazebo": every real approach now gets its own marker showing its
        own actual color, including ones that move opposite to whichever
        approach state[0] happened to belong to.

        Deliberately not deduped to skip unchanged colors: gz-transport
        pub/sub has no delivery acknowledgment, and this publisher's very
        first message can race the SceneBroadcaster subscriber's discovery
        and simply be dropped -- confirmed live (see chat): a run where
        the real signal state changed correctly the whole time still
        showed the marker stuck on its initial color throughout, because
        an early dedup implementation trusted that first (silently lost)
        publish and never tried again until the color changed a second
        time. Republishing every call is cheap (small local message) and
        self-healing -- any dropped message is corrected by the very next
        call regardless of whether the color actually changed.
        """
        for tls_id, state in tls_states.items():
            approaches = self.tls_approaches.get(tls_id) or [{"link_index": 0}]  # fallback: old single-marker form
            for i, approach in enumerate(approaches):
                link_index = approach["link_index"]
                ch = state[link_index] if link_index < len(state) else (state[0] if state else "r")
                color_name = _collapse_char(ch)
                r, g, b, a = STATE_COLOR[color_name]
                msg = material_color_pb2.MaterialColor()
                msg.entity.name = tls_marker_visual(tls_id, i)
                msg.entity.type = entity_pb2.Entity.VISUAL
                msg.entity_match = material_color_pb2.MaterialColor.ALL
                msg.ambient.r, msg.ambient.g, msg.ambient.b, msg.ambient.a = r, g, b, a
                msg.diffuse.r, msg.diffuse.g, msg.diffuse.b, msg.diffuse.a = r, g, b, a
                self._color_pub.publish(msg)
                self._last_tls_color[(tls_id, i)] = color_name
