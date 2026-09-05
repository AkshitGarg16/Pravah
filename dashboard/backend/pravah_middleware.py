"""
PRAVAH middleware — the interconnect between the SUMO/TomTom feeds and the dashboard.

It owns three things:

1. THE CANONICAL NETWORK. Built once from OpenStreetMap (Overpass) so every
   component — SUMO, TomTom, the middleware and the frontend — talks about the
   same physical roads. Real geometry only; nothing is invented.

2. UNIVERSAL IDs. The marker everyone must agree on:

       junction   J:osm:<node_id>              e.g. J:osm:1234567890
       segment    S:osm:<way_id>:<from>:<to>   e.g. S:osm:23417811:245:9912
       direction  ...:F  / ...:B               optional suffix, forward/backward

   OSM ids are stable, globally unique, and derivable by anyone holding the same
   extract, so no party has to invent a numbering scheme. Feeds that cannot emit
   them (SUMO edge/tls ids, TomTom ids) are resolved through a crosswalk file and,
   failing that, snapped by coordinates to the nearest canonical feature; every
   learned mapping is written back to the crosswalk so it only happens once.
   Junctions with no OSM node fall back to J:geo:<lat5>_<lng5>.

3. FUSION + FAN-OUT. Normalises whatever the feeds send, fuses SUMO and TomTom
   per segment with freshness rules, and pushes snapshots to the dashboard over
   WebSocket at a fixed rate.

Inbound (teammates -> here). Any of:
    poll     GET http://<their-ip>:<port>/...   ->  --poll <url>[,<url>...]
    push     POST /api/ingest                    (single object or list)
    stream   WS  ws://<their-ip>:<port>/...     ->  --subscribe <url>[,<url>...]

Inbound payloads are tolerant: keys are matched case-insensitively against the
alias tables below. The two shapes that matter:

    {"type": "segment",  "id": "<any id>", "ts": 1725345600.0,
     "speed": 8.4, "free_flow": 13.9, "units": "mps",
     "source": "sumo", "occupancy": 0.42, "halting": 11, "count": 37}

    {"type": "junction", "id": "<any id>", "ts": 1725345600.0,
     "state": "GGrrGG", "time_to_change": 12.0, "program": "0",
     "links": ["<seg id>", ...],          # optional, else from crosswalk
     "hv_count": 3, "ev": false}

    # or, instead of a SUMO state string:
     "phases": {"<seg id>": "green", "<seg id>": "red"}

Units default to m/s for source=sumo and km/h for source=tomtom; a per-message
"units" field ("mps" | "kmh") overrides that.

Outbound (here -> dashboard):
    GET  /api/network     canonical topology + geometry (static, cache it)
    GET  /api/state       latest fused snapshot
    GET  /api/health      per-source liveness and staleness counters
    POST /api/ingest      push endpoint for the feeds
    WS   /ws/state        snapshot on connect, then live updates at --rate Hz

    python backend/pravah_middleware.py fetch-osm --lat 28.6289 --lng 77.2410 --radius 1200
    python backend/pravah_middleware.py serve --poll http://192.168.1.42:8080/state
    python backend/pravah_middleware.py serve --replay backend/data/feed.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

DATA = Path(__file__).parent / "data"
NETWORK_FILE = DATA / "network.json"
CROSSWALK_FILE = DATA / "crosswalk.json"

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OVERPASS_UA = "pravah-middleware/1.0 (traffic dashboard; contact: pravah team)"
HIGHWAY_CLASSES = "motorway|trunk|primary|secondary|tertiary"

DEFAULT_FREE_FLOW = {
    "motorway": 80, "trunk": 60, "primary": 50, "secondary": 45,
    "tertiary": 40, "unclassified": 35, "residential": 30,
}
DEFAULT_LANES = {
    "motorway": 3, "trunk": 3, "primary": 3, "secondary": 2,
    "tertiary": 2, "unclassified": 1, "residential": 1,
}

SEG_ID_KEYS = ["canonical_id", "segment_id", "segmentid", "seg_id", "id", "edge_id",
               "edgeid", "edge", "sumo_edge", "way_id", "wayid", "tomtom_id"]
JUN_ID_KEYS = ["canonical_id", "junction_id", "junctionid", "jn_id", "id", "tls_id",
               "tlsid", "tls", "node_id", "nodeid", "intersection_id"]
SPEED_KEYS = ["speed", "avg_speed", "avgspeed", "mean_speed", "meanspeed",
              "current_speed", "currentspeed", "speed_kmh", "harmonic_speed"]
FREEFLOW_KEYS = ["free_flow", "freeflow", "free_flow_speed", "freeflowspeed", "ff_speed"]
TS_KEYS = ["ts", "timestamp", "time", "epoch", "observed_at"]
LAT_KEYS = ["lat", "latitude", "y"]
LNG_KEYS = ["lng", "lon", "long", "longitude", "x"]
PHASE_WORDS = {"g": "green", "green": "green", "y": "amber", "amber": "amber",
               "yellow": "amber", "r": "red", "red": "red", "o": "off", "u": "off"}


def now() -> float:
    return time.time()


def get_any(d: dict, keys: Iterable[str], default=None):
    low = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if k in low and low[k] is not None:
            return low[k]
    return default


def to_epoch(v) -> float:
    if v is None:
        return now()
    try:
        f = float(v)
    except (TypeError, ValueError):
        return now()
    return f / 1000.0 if f > 1e12 else f


def haversine_m(a, b) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# ------------------------------------------------------------------- network


@dataclass
class Segment:
    id: str
    osm_way: int
    from_node: int
    to_node: int
    name: str
    highway: str
    lanes: int
    oneway: bool
    length_m: float
    free_flow_kmh: float
    coords: list[list[float]]
    # Original TomTom CSV ids behind this segment. netconvert merged several
    # source segments into one edge, so this is one-to-many. Carried so a
    # real-world feed can be fused onto the same segment without going via SUMO.
    tomtom_ids: list[str] = field(default_factory=list)


@dataclass
class Junction:
    id: str
    osm_node: int | None
    name: str
    lat: float
    lng: float
    signalised: bool
    approaches: list[str] = field(default_factory=list)


@dataclass
class Network:
    center: dict
    segments: dict[str, Segment]
    junctions: dict[str, Junction]
    movements: list[dict]

    def to_json(self) -> dict:
        return {
            "center": self.center,
            "segments": [asdict(s) for s in self.segments.values()],
            "junctions": [asdict(j) for j in self.junctions.values()],
            "movements": self.movements,
        }

    @staticmethod
    def load(path: Path = NETWORK_FILE) -> "Network":
        if not path.exists():
            raise SystemExit(
                f"No canonical network at {path}. Build it first:\n"
                f"  python {Path(__file__).name} fetch-osm --lat <lat> --lng <lng> --radius 1200"
            )
        raw = json.loads(path.read_text())
        return Network(
            center=raw["center"],
            segments={s["id"]: Segment(**s) for s in raw["segments"]},
            junctions={j["id"]: Junction(**j) for j in raw["junctions"]},
            movements=raw["movements"],
        )


def junction_id(node_id: int | None, lat: float, lng: float) -> str:
    return f"J:osm:{node_id}" if node_id is not None else f"J:geo:{lat:.5f}_{lng:.5f}"


def segment_id(way: int, a: int, b: int) -> str:
    return f"S:osm:{way}:{a}:{b}"


def base_segment_id(sid: str) -> str:
    return sid[:-2] if sid.endswith((":F", ":B")) else sid


def fetch_osm(lat: float, lng: float, radius: int, classes: str = HIGHWAY_CLASSES) -> Network:
    import httpx

    query = f"""
    [out:json][timeout:120];
    (
      way["highway"~"^({classes})(_link)?$"](around:{radius},{lat},{lng});
      node["highway"="traffic_signals"](around:{radius},{lat},{lng});
    );
    out body;
    >;
    out skel qt;
    """
    print(f"Querying Overpass around {lat},{lng} r={radius}m ...")
    elements = None
    for url in OVERPASS_MIRRORS:
        try:
            r = httpx.post(url, data={"data": query},
                           headers={"User-Agent": OVERPASS_UA}, timeout=180)
            r.raise_for_status()
            elements = r.json()["elements"]
            print(f"  via {url}")
            break
        except Exception as exc:
            print(f"  {url} -> {type(exc).__name__}: {str(exc)[:80]}")
    if elements is None:
        raise SystemExit("All Overpass mirrors failed; retry later or pass a saved extract.")

    nodes: dict[int, tuple[float, float]] = {}
    node_tags: dict[int, dict] = {}
    ways = []
    for el in elements:
        if el["type"] == "node":
            nodes[el["id"]] = (el["lat"], el["lon"])
            if el.get("tags"):
                node_tags[el["id"]] = el["tags"]
        elif el["type"] == "way" and el.get("tags", {}).get("highway"):
            ways.append(el)

    usage: dict[int, int] = defaultdict(int)
    for w in ways:
        for n in w["nodes"]:
            usage[n] += 1

    segments: dict[str, Segment] = {}
    junctions: dict[str, Junction] = {}
    incident: dict[str, list[str]] = defaultdict(list)

    def ensure_junction(node_id: int) -> str:
        lat_, lng_ = nodes[node_id]
        jid = junction_id(node_id, lat_, lng_)
        if jid not in junctions:
            tags = node_tags.get(node_id, {})
            junctions[jid] = Junction(
                id=jid,
                osm_node=node_id,
                name=tags.get("name", ""),
                lat=lat_,
                lng=lng_,
                signalised=tags.get("highway") == "traffic_signals",
            )
        return jid

    for w in ways:
        tags = w.get("tags", {})
        hw = tags["highway"].replace("_link", "")
        node_list = [n for n in w["nodes"] if n in nodes]
        if len(node_list) < 2:
            continue

        cut = [0] + [i for i in range(1, len(node_list) - 1) if usage[node_list[i]] > 1] + [len(node_list) - 1]
        cut = sorted(set(cut))

        try:
            lanes = int(str(tags.get("lanes", "")).split(";")[0])
        except ValueError:
            lanes = DEFAULT_LANES.get(hw, 2)
        try:
            maxspeed = float(str(tags.get("maxspeed", "")).split()[0])
        except (ValueError, IndexError):
            maxspeed = DEFAULT_FREE_FLOW.get(hw, 40)
        oneway = tags.get("oneway") in ("yes", "true", "1", "-1")

        for a, b in zip(cut, cut[1:]):
            chunk = node_list[a : b + 1]
            if len(chunk) < 2:
                continue
            coords = [[nodes[n][0], nodes[n][1]] for n in chunk]
            length = sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))
            if length < 12:
                continue
            sid = segment_id(w["id"], chunk[0], chunk[-1])
            segments[sid] = Segment(
                id=sid,
                osm_way=w["id"],
                from_node=chunk[0],
                to_node=chunk[-1],
                name=tags.get("name") or tags.get("ref") or hw.title(),
                highway=hw,
                lanes=max(1, lanes),
                oneway=oneway,
                length_m=round(length, 1),
                free_flow_kmh=maxspeed,
                coords=coords,
            )
            for endpoint in (chunk[0], chunk[-1]):
                jid = ensure_junction(endpoint)
                incident[jid].append(sid)

    for jid, segs in incident.items():
        junctions[jid].approaches = sorted(set(segs))

    movements = []
    for jid, j in junctions.items():
        for u in j.approaches:
            for v in j.approaches:
                if u == v:
                    continue
                movements.append({"junction": jid, "from": u, "to": v})

    keep = {jid for jid, j in junctions.items() if len(j.approaches) >= 2 or j.signalised}
    junctions = {k: v for k, v in junctions.items() if k in keep}
    movements = [m for m in movements if m["junction"] in junctions]

    net = Network(center={"lat": lat, "lng": lng, "radius_m": radius},
                  segments=segments, junctions=junctions, movements=movements)
    print(f"  {len(segments)} segments, {len(junctions)} junctions "
          f"({sum(j.signalised for j in junctions.values())} signalised), {len(movements)} movements")
    return net


# ----------------------------------------------------------------- crosswalk


class Crosswalk:
    """Foreign id -> canonical id, learned by coordinate snapping and persisted."""

    def __init__(self, net: Network, path: Path = CROSSWALK_FILE, snap_m: float = 30.0):
        self.net = net
        self.path = path
        self.snap_m = snap_m
        raw = json.loads(path.read_text()) if path.exists() else {}
        self.segments: dict[str, str] = raw.get("segments", {})
        self.junctions: dict[str, str] = raw.get("junctions", {})
        self.links: dict[str, list[str]] = raw.get("links", {})
        self.unresolved: dict[str, int] = defaultdict(int)
        self._dirty = False
        self._seg_mid = {
            sid: s.coords[len(s.coords) // 2] for sid, s in net.segments.items()
        }

    def save(self):
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"segments": self.segments, "junctions": self.junctions, "links": self.links},
            indent=2, sort_keys=True))
        self._dirty = False

    def _snap(self, point, table: dict[str, Any]) -> str | None:
        best, best_d = None, self.snap_m
        for cid, c in table.items():
            d = haversine_m(point, c)
            if d < best_d:
                best, best_d = cid, d
        return best

    def resolve_segment(self, raw_id: str, hint: dict | None = None) -> str | None:
        rid = str(raw_id).lstrip("-")
        if rid in self.net.segments:
            return rid
        if base_segment_id(rid) in self.net.segments:
            return base_segment_id(rid)
        if rid in self.segments:
            return self.segments[rid]
        point = None
        if hint:
            lat, lng = get_any(hint, LAT_KEYS), get_any(hint, LNG_KEYS)
            if lat is not None and lng is not None:
                point = (float(lat), float(lng))
            elif hint.get("coords"):
                c = hint["coords"]
                point = tuple(c[len(c) // 2])
        if point:
            match = self._snap(point, self._seg_mid)
            if match:
                self.segments[rid] = match
                self._dirty = True
                return match
        self.unresolved[f"segment:{rid}"] += 1
        return None

    def resolve_junction(self, raw_id: str, hint: dict | None = None) -> str | None:
        rid = str(raw_id)
        if rid in self.net.junctions:
            return rid
        if rid in self.junctions:
            return self.junctions[rid]
        if rid.isdigit() and f"J:osm:{rid}" in self.net.junctions:
            return f"J:osm:{rid}"
        if hint:
            lat, lng = get_any(hint, LAT_KEYS), get_any(hint, LNG_KEYS)
            if lat is not None and lng is not None:
                table = {jid: (j.lat, j.lng) for jid, j in self.net.junctions.items()}
                match = self._snap((float(lat), float(lng)), table)
                if match:
                    self.junctions[rid] = match
                    self._dirty = True
                    return match
        self.unresolved[f"junction:{rid}"] += 1
        return None

    def link_order(self, canonical_jid: str, raw_id: str) -> list[str]:
        """Approach order behind a SUMO redYellowGreenState string."""
        return self.links.get(raw_id) or self.links.get(canonical_jid) or \
            self.net.junctions[canonical_jid].approaches


# ------------------------------------------------------------- fused state


class State:
    """Latest fused view of the network."""

    def __init__(self, net: Network, crosswalk: Crosswalk, ttl: float = 90.0):
        self.net = net
        self.xwalk = crosswalk
        self.ttl = ttl
        self.seg_raw: dict[str, dict[str, dict]] = defaultdict(dict)  # seg -> source -> obs
        self.jun: dict[str, dict] = {}
        self.net_stats: dict | None = None  # whole-network totals from the simulation
        self.sources: dict[str, dict] = defaultdict(lambda: {"messages": 0, "last_seen": 0.0, "errors": 0})
        self.rejected = 0
        self.revision = 0

    def ingest(self, msg: dict) -> bool:
        kind = str(get_any(msg, ["type", "kind", "record"], "")).lower()
        if not kind:
            kind = "junction" if get_any(msg, ["state", "phases", "signal_state"]) else "segment"
        source = str(get_any(msg, ["source", "src", "provider"], "sumo")).lower()
        ts = to_epoch(get_any(msg, TS_KEYS))

        if kind.startswith("net"):
            ok = self._ingest_network(msg, source, ts)
        elif kind.startswith("seg"):
            ok = self._ingest_segment(msg, source, ts)
        else:
            ok = self._ingest_junction(msg, source, ts)
        meta = self.sources[source]
        if ok:
            meta["messages"] += 1
            meta["last_seen"] = ts
            self.revision += 1
        else:
            meta["errors"] += 1
            self.rejected += 1
        return ok

    def _speed_kmh(self, value, msg, source) -> float | None:
        if value is None:
            return None
        units = str(get_any(msg, ["units", "unit", "speed_units"], "")).lower()
        if not units:
            units = "mps" if source == "sumo" else "kmh"
        v = float(value)
        return v * 3.6 if units in ("mps", "m/s", "ms") else v

    def _ingest_segment(self, msg: dict, source: str, ts: float) -> bool:
        raw_id = get_any(msg, SEG_ID_KEYS)
        if raw_id is None:
            return False
        sid = self.xwalk.resolve_segment(raw_id, msg)
        if sid is None:
            return False
        speed = self._speed_kmh(get_any(msg, SPEED_KEYS), msg, source)
        if speed is None:
            return False
        free_flow = self._speed_kmh(get_any(msg, FREEFLOW_KEYS), msg, source)
        self.seg_raw[sid][source] = {
            "ts": ts,
            "speed": speed,
            "free_flow": free_flow,
            "occupancy": get_any(msg, ["occupancy", "occ", "density"]),
            "halting": get_any(msg, ["halting", "halting_number", "queue", "stopped"]),
            "count": get_any(msg, ["count", "vehicle_count", "veh_count", "n"]),
            "confidence": get_any(msg, ["confidence", "conf"]),
        }
        return True

    def _ingest_junction(self, msg: dict, source: str, ts: float) -> bool:
        raw_id = get_any(msg, JUN_ID_KEYS)
        if raw_id is None:
            return False
        jid = self.xwalk.resolve_junction(raw_id, msg)
        if jid is None:
            return False

        phases: dict[str, str] = {}
        explicit = get_any(msg, ["phases", "approach_phases"])
        if isinstance(explicit, dict):
            for k, v in explicit.items():
                seg = self.xwalk.resolve_segment(k) or k
                phases[seg] = PHASE_WORDS.get(str(v).lower()[:1], str(v).lower())
        else:
            state = get_any(msg, ["state", "signal_state", "rygstate", "redyellowgreenstate"])
            if state:
                order = get_any(msg, ["links", "link_order", "approaches"]) or \
                    self.xwalk.link_order(jid, str(raw_id))
                for i, ch in enumerate(str(state)):
                    if i >= len(order):
                        break
                    seg = self.xwalk.resolve_segment(order[i]) or order[i]
                    phases[seg] = PHASE_WORDS.get(ch.lower(), "red")

        single = get_any(msg, ["phase", "current_phase"])
        self.jun[jid] = {
            "id": jid,
            "ts": ts,
            "source": source,
            "phases": phases,
            "phase": PHASE_WORDS.get(str(single).lower()[:1], single) if single else None,
            "time_to_change": get_any(msg, ["time_to_change", "timetochange", "next_switch", "remaining"]),
            "program": get_any(msg, ["program", "program_id", "plan"]),
            "hv_count": get_any(msg, ["hv_count", "heavy_vehicles", "hv"]),
            "ev": bool(get_any(msg, ["ev", "emergency", "ev_active"], False)),
            # Queue length, spillback, waiting time, vehicle mix: analytics the
            # feed already computes. Passed through verbatim rather than
            # flattened, so adding a metric upstream needs no change here.
            "metrics": get_any(msg, ["metrics"]) or {},
            "approaches": get_any(msg, ["approaches", "approach_detail"]) or [],
        }
        return True

    def _ingest_network(self, msg: dict, source: str, ts: float) -> bool:
        """Whole-network totals -- running vehicles, mean speed, teleports."""
        payload = {k: v for k, v in msg.items() if k not in ("type", "kind", "record")}
        self.net_stats = {**payload, "ts": ts, "source": source}
        return True

    def snapshot(self) -> dict:
        t = now()
        segs = []
        stale = 0
        no_data = 0
        for sid, seg in self.net.segments.items():
            obs = self.seg_raw.get(sid, {})
            tomtom = obs.get("tomtom")
            sumo = obs.get("sumo")
            # Speed comes from the preferred fresh source; queue/occupancy detail
            # is merged from whichever source reports it.
            chosen, provider = None, None
            for cand, name in ((tomtom, "tomtom"), (sumo, "sumo")):
                if cand and t - cand["ts"] <= self.ttl:
                    chosen, provider = cand, name
                    break
            if chosen is None and (tomtom or sumo):
                chosen = tomtom or sumo
                provider = "tomtom" if tomtom else "sumo"

            if chosen is None:
                no_data += 1
                segs.append({"id": sid, "status": "no-data"})
                continue

            def merged(field):
                for cand in (chosen, sumo, tomtom):
                    if cand and cand.get(field) is not None:
                        return cand[field]
                return None

            age = t - chosen["ts"]
            free_flow = merged("free_flow") or seg.free_flow_kmh
            speed = max(0.0, float(chosen["speed"]))
            congestion = max(0.0, min(1.0, 1.0 - speed / max(free_flow, 1e-6)))
            occ = merged("occupancy")
            halting = merged("halting")
            if occ is not None:
                pressure = min(1.0, 0.65 * float(occ) + 0.35 * congestion)
            elif halting is not None:
                pressure = min(1.0, 0.5 * congestion + 0.5 * min(1.0, float(halting) / (8 * seg.lanes)))
            else:
                pressure = congestion
            if age > self.ttl:
                stale += 1
            segs.append({
                "id": sid,
                "status": "stale" if age > self.ttl else "live",
                "speed": round(speed, 1),
                "free_flow": round(float(free_flow), 1),
                "congestion": round(congestion, 3),
                "pressure": round(float(pressure), 3),
                "occupancy": occ,
                "halting": halting,
                "source": provider,
                "age_s": round(age, 1),
                "fused_from": sorted(obs.keys()),
            })

        juncs = []
        for jid, j in self.net.junctions.items():
            live = self.jun.get(jid)
            if live is None:
                juncs.append({"id": jid, "status": "no-data",
                              "signalised": j.signalised, "lat": j.lat, "lng": j.lng})
                continue
            age = t - live["ts"]
            juncs.append({**live, "lat": j.lat, "lng": j.lng, "signalised": j.signalised,
                          "age_s": round(age, 1), "status": "stale" if age > self.ttl else "live"})

        return {
            "ts": t,
            "revision": self.revision,
            "segments": segs,
            "junctions": juncs,
            "stats": {
                "segments_total": len(self.net.segments),
                "segments_live": len(self.net.segments) - stale - no_data,
                "segments_stale": stale,
                "segments_no_data": no_data,
                "junctions_reporting": len(self.jun),
                "rejected": self.rejected,
            },
            "network": self.net_stats,
        }

    def health(self) -> dict:
        t = now()
        return {
            "ts": t,
            "sources": {
                name: {**meta, "age_s": round(t - meta["last_seen"], 1) if meta["last_seen"] else None,
                       "status": "ok" if meta["last_seen"] and t - meta["last_seen"] < self.ttl else "down"}
                for name, meta in self.sources.items()
            },
            "unresolved_ids": dict(sorted(self.xwalk.unresolved.items(), key=lambda kv: -kv[1])[:20]),
            "rejected": self.rejected,
            "network": {"segments": len(self.net.segments), "junctions": len(self.net.junctions)},
        }


# ---------------------------------------------------------------- feed tasks


def iter_messages(payload: Any, inherited: dict | None = None) -> Iterable[dict]:
    """Flatten whatever the feeds send into individual records.

    Envelope-level source/ts/units cascade down to each record that omits them,
    so a batch can be tagged once instead of per item.
    """
    inherited = inherited or {}
    if isinstance(payload, list):
        for item in payload:
            yield from iter_messages(item, inherited)
    elif isinstance(payload, dict):
        env = {k: payload[k] for k in ("source", "ts", "units", "timestamp", "provider")
               if k in payload and not isinstance(payload[k], (list, dict))}
        env = {**inherited, **env}
        if "segments" in payload or "junctions" in payload:
            for m in payload.get("segments") or []:
                yield {**env, **m, "type": m.get("type", "segment")}
            for m in payload.get("junctions") or []:
                yield {**env, **m, "type": m.get("type", "junction")}
        elif "data" in payload and isinstance(payload["data"], (list, dict)):
            yield from iter_messages(payload["data"], env)
        else:
            yield {**inherited, **payload}


async def poll_loop(url: str, state: State, interval: float, recorder):
    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        backoff = 1.0
        while True:
            try:
                r = await client.get(url)
                r.raise_for_status()
                for msg in iter_messages(r.json()):
                    state.ingest(msg)
                    if recorder:
                        recorder.write(json.dumps(msg) + "\n")
                backoff = 1.0
            except Exception as exc:  # feed down: keep the dashboard alive, mark stale
                print(f"[poll {url}] {type(exc).__name__}: {exc}")
                backoff = min(backoff * 2, 30.0)
                await asyncio.sleep(backoff)
                continue
            await asyncio.sleep(interval)


async def subscribe_loop(url: str, state: State, recorder):
    import websockets

    backoff = 1.0
    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                print(f"[ws {url}] connected")
                backoff = 1.0
                async for raw in ws:
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    for msg in iter_messages(payload):
                        state.ingest(msg)
                        if recorder:
                            recorder.write(json.dumps(msg) + "\n")
        except Exception as exc:
            print(f"[ws {url}] {type(exc).__name__}: {exc}; retry in {backoff:.0f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


async def replay_loop(path: Path, state: State, speed: float):
    lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if not lines:
        return
    print(f"[replay] {len(lines)} messages from {path}")
    while True:
        t0 = to_epoch(get_any(lines[0], TS_KEYS))
        wall = now()
        for msg in lines:
            ts = to_epoch(get_any(msg, TS_KEYS))
            delay = (ts - t0) / max(speed, 1e-6) - (now() - wall)
            if delay > 0:
                await asyncio.sleep(min(delay, 5.0))
            state.ingest({**msg, "ts": now()})
        await asyncio.sleep(1.0)


# -------------------------------------------------------------------- server


def build_app(net: Network, state: State, args):
    from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware

    # `from __future__ import annotations` postpones every annotation in this
    # file, and FastAPI resolves an endpoint's annotations against the module
    # globals -- where these names, imported inside this function to keep
    # fetch-osm runnable without fastapi, do not exist. Unresolved, `Request`
    # and `WebSocket` are read as query parameters and every call to
    # /api/ingest fails with 422. Publishing them fixes that without hoisting
    # the import out of the lazy path.
    globals().update(Request=Request, WebSocket=WebSocket)

    app = FastAPI(title="PRAVAH middleware", version="1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=args.cors.split(","), allow_methods=["*"], allow_headers=["*"]
    )
    clients: set[WebSocket] = set()
    network_payload = net.to_json()
    recorder = open(args.record, "a", encoding="utf-8") if args.record else None

    @app.get("/api/network")
    def get_network():
        return network_payload

    @app.get("/api/state")
    def get_state():
        return state.snapshot()

    @app.get("/api/health")
    def get_health():
        return state.health()

    @app.post("/api/ingest")
    async def ingest(request: Request):
        payload = await request.json()
        accepted = 0
        for msg in iter_messages(payload):
            if state.ingest(msg):
                accepted += 1
            if recorder:
                recorder.write(json.dumps(msg) + "\n")
        return {"accepted": accepted, "rejected": state.rejected, "revision": state.revision}

    @app.websocket("/ws/state")
    async def ws_state(ws: WebSocket):
        await ws.accept()
        clients.add(ws)
        try:
            await ws.send_json({"type": "network", "payload": network_payload})
            await ws.send_json({"type": "state", "payload": state.snapshot()})
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(ws)

    async def broadcast():
        last = -1
        while True:
            await asyncio.sleep(1.0 / max(args.rate, 0.1))
            if not clients or state.revision == last:
                continue
            last = state.revision
            snap = {"type": "state", "payload": state.snapshot()}
            for ws in list(clients):
                try:
                    await ws.send_json(snap)
                except Exception:
                    clients.discard(ws)

    @app.on_event("startup")
    async def startup():
        asyncio.create_task(broadcast())
        for url in filter(None, (args.poll or "").split(",")):
            asyncio.create_task(poll_loop(url.strip(), state, args.poll_interval, recorder))
        for url in filter(None, (args.subscribe or "").split(",")):
            asyncio.create_task(subscribe_loop(url.strip(), state, recorder))
        if args.replay:
            asyncio.create_task(replay_loop(Path(args.replay), state, args.replay_speed))

        async def persist():
            while True:
                await asyncio.sleep(15)
                state.xwalk.save()

        asyncio.create_task(persist())

    @app.on_event("shutdown")
    async def shutdown():
        state.xwalk.save()
        if recorder:
            recorder.close()

    return app


def cmd_serve(args):
    net = Network.load(Path(args.network))
    xwalk = Crosswalk(net, Path(args.crosswalk), args.snap_radius)
    state = State(net, xwalk, ttl=args.ttl)
    app = build_app(net, state, args)
    try:
        import uvicorn
    except ImportError:
        raise SystemExit("pip install uvicorn fastapi httpx websockets")
    print(f"Network: {len(net.segments)} segments, {len(net.junctions)} junctions")
    print(f"Serving http://{args.host}:{args.port}  (ws /ws/state)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def cmd_fetch_osm(args):
    net = fetch_osm(args.lat, args.lng, args.radius, args.classes)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(net.to_json(), separators=(",", ":")))
    print(f"Wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch-osm", help="build the canonical network from OpenStreetMap")
    f.add_argument("--lat", type=float, default=28.6289)
    f.add_argument("--lng", type=float, default=77.2410)
    f.add_argument("--radius", type=int, default=1200)
    f.add_argument("--classes", default=HIGHWAY_CLASSES,
                   help="OSM highway classes to include (regex alternation)")
    f.add_argument("--out", default=str(NETWORK_FILE))
    f.set_defaults(func=cmd_fetch_osm)

    s = sub.add_parser("serve", help="run the middleware")
    # PORT / PRAVAH_CORS let a PaaS (Render, Fly, Railway) configure the service
    # without changing the start command.
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    s.add_argument("--network", default=str(NETWORK_FILE))
    s.add_argument("--crosswalk", default=str(CROSSWALK_FILE))
    s.add_argument("--poll", default=None, help="comma-separated GET endpoints to poll")
    s.add_argument("--poll-interval", type=float, default=1.0)
    s.add_argument("--subscribe", default=None, help="comma-separated ws:// feeds")
    s.add_argument("--replay", default=None, help="replay a recorded .jsonl feed")
    s.add_argument("--replay-speed", type=float, default=1.0)
    s.add_argument("--record", default=None, help="append every inbound message to this .jsonl")
    s.add_argument("--rate", type=float, default=1.0, help="dashboard push rate in Hz")
    s.add_argument("--ttl", type=float, default=90.0, help="seconds before an observation is stale")
    s.add_argument("--snap-radius", type=float, default=30.0, help="metres for id snapping")
    s.add_argument("--cors", default=os.environ.get(
        "PRAVAH_CORS", "http://localhost:5173,http://127.0.0.1:5173"))
    s.set_defaults(func=cmd_serve)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
