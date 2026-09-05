# gui_data — live ITO telemetry over TCP

Streams everything the running SUMO simulation knows about the ITO junctions —
junction ids, the original TomTom segment ids, signal state, queues, congestion,
vehicle counts by type, emissions — to a GUI on another laptop.

```
laptop A (simulation)                        laptop B (GUI)
┌──────────────────────────┐                ┌──────────────────────┐
│ pravah_telemetry.py      │  TCP :5555     │ telemetry_client.py  │
│  SUMO ── TraCI ── server │ ─────────────▶ │  or your own GUI     │
└──────────────────────────┘   JSON lines   └──────────────────────┘
```

## Driving the Pravah dashboard

`run_pipeline.sh` starts everything the `Pravah_Traffic_Optimizer` dashboard
needs and stops it all together:

```
simulation            bridge              middleware           browser
pravah_telemetry.py ─ pravah_bridge.py ─ pravah_middleware.py ─ vite
     TraCI      TCP :5555      HTTP /api/ingest      WS /ws/state
```

```bash
gui_data/run_pipeline.sh                     # free-flowing signals
gui_data/run_pipeline.sh --scenario current  # the mis-timed ones
```

Then open http://localhost:5173.

| flag | effect |
|---|---|
| `--scenario current` | the mis-timed signals instead of the actuated ones |
| `--sumo-gui` | open SUMO's own window too — the simulation runs headless otherwise, so this is what to pass if you expected to see vehicles and got only a dashboard |
| `--no-dev` | leave the Vite dev server to you |
| `--fast` | drop the wall-clock pacing: an hour of traffic in about two minutes, useful for reaching a jam quickly and useless for watching one |

`--sumo-gui` needs a real terminal. Without a controlling tty sumo-gui exits
immediately with status 0 and prints nothing, which looks exactly like it never
started, so the script refuses the flag rather than letting you chase that.

| piece | what it does |
|---|---|
| `export_network.py` | writes the dashboard's canonical topology from `pravah.net.xml`, so every id the feed emits is one the dashboard already knows |
| `pravah_bridge.py` | reads the TCP feed below, renames fields, POSTs to the middleware's `/api/ingest` |
| `run_pipeline.sh` | starts the middleware, the simulation and the bridge, and cleans up on Ctrl-C |

The two halves are separable: `pravah_bridge.py --feed-host <ip> --api
http://<ip>:8000` puts the network hop wherever you want it, and `--replay
feed.jsonl` drives the dashboard with no SUMO running at all.

### Why the dashboard's network is generated, not fetched

The middleware shipped with an OpenStreetMap extract centred on 28.6289,77.2410
with a 1 km radius. This simulation covers 77.2440–77.2786 — mostly *east* of
that disc. Three of the four traffic lights fell inside it, junction 309 did
not, and most of the 132 edges had no counterpart, so the dashboard would have
drawn roads nobody is simulating and greyed out the ones that are.

`export_network.py` builds the topology from the SUMO network instead:
`S:sumo:<edge id>` and `J:sumo:<junction id>`, with geometry converted to
lat/lng. Coverage is exact and the middleware's crosswalk never has to snap
anything by coordinate — `/api/health` reports zero unresolved ids. Each segment
still carries its `tomtom_ids`, so a real TomTom feed can be fused onto the same
road later without going through SUMO.

## Running the raw feed on its own

**On the simulating laptop:**

```bash
cd ~/Pravah
export SUMO_HOME=/home/akshit/.local/lib/python3.10/site-packages/sumo
gui_data/pravah_telemetry.py
```

It prints the addresses the other laptop can dial:

```
[telemetry] listening on 0.0.0.0:5555
[telemetry]   reachable at  192.168.0.149:5555
```

**On the receiving laptop:**

```bash
python3 telemetry_client.py --host 192.168.0.149
```

`telemetry_client.py` is the only file the receiving laptop needs — copy it
across on its own. It imports nothing from this repo and needs no SUMO install.

### Server options

| flag | effect |
|---|---|
| `--sumocfg <path>` | which scenario to stream (default `sumo/pravah_ito_busy.sumocfg`; use `sumo/pravah_ito_jam.sumocfg` for the congested one) |
| `--port 5555` | listening port |
| `--host 127.0.0.1` | restrict to this machine (default `0.0.0.0` accepts other machines) |
| `--rate 1.0` | publish one snapshot every N *simulated* seconds |
| `--realtime` | pace the run at wall-clock speed instead of as fast as possible |
| `--gui` | also open sumo-gui locally while streaming |
| `--include-vehicles` | add a per-vehicle array to every snapshot |
| `--wait-for-client` | hold at t=0 until a client connects, so nothing is missed |

Use `--realtime` when a person is watching the GUI. Without it the simulation
runs as fast as the machine allows and an hour of traffic arrives in a couple of
minutes.

### Client modes

| mode | what it does |
|---|---|
| `--mode summary` | refreshing console dashboard (default) |
| `--mode raw` | pretty-prints every message |
| `--mode record --out feed.jsonl` | saves the feed for later replay/analysis |

`--retry` keeps reconnecting, so the client can be started before the server.

### If it will not connect

1. Both laptops on the same network, and `ping 192.168.0.149` works.
2. Firewall on the simulating laptop allows the port:
   `sudo ufw allow 5555/tcp`
3. The server is bound to `0.0.0.0`, not `127.0.0.1` (the default is correct).

## Wire format

One JSON object per line, UTF-8, `\n`-terminated. No length prefix, no framing,
no handshake. Any language can consume it with "read a line, parse it as JSON".

Three message types:

| `type` | when | contents |
|---|---|---|
| `meta` | once, on connect | everything static: network geometry, junction inventory, full signal programs, edge→segment map |
| `snapshot` | every `--rate` seconds | everything live |
| `end` | once, at simulation end | totals |

`meta` arrives first, always — a client connecting mid-run can draw the network
before the first snapshot lands.

### Writing your own client

The entire protocol, in any language:

```python
sock = socket.create_connection((HOST, 5555))
buf = b""
while True:
    buf += sock.recv(65536)
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        message = json.loads(line)
        ...
```

Keep the leftover bytes between reads, as above. A `recv` returns whatever
happened to arrive — routinely half a message, or two and a half. Parsing each
`recv` directly is the one mistake that makes this feed look intermittently
corrupt.

A client that stops reading is **disconnected**, not waited for. This is live
telemetry: the newest snapshot is what matters, and the simulation must never be
held up by a slow GUI. Reconnect and you get a fresh `meta` immediately.

## Message reference

### `meta`

| field | meaning |
|---|---|
| `scenario`, `step_length_s`, `sim_end_s`, `publish_interval_s` | run parameters |
| `network_boundary_xy`, `network_boundary_lonlat` | extent, for fitting a map view |
| `congestion_bands` | the exact thresholds behind every `congestion` string below |
| `vehicle_types` | type ids that can appear in any `vehicle_types` count |
| `junctions[]` | `junction_id`, `is_traffic_light`, `position_xy`, `position_lonlat`, `incoming_edges`, `outgoing_edges`, `shape_xy` |
| `edges[]` | `edge_id`, **`segment_ids`**, `street_name`, `from_junction`, `to_junction`, `lane_ids`, `lane_count`, `length_m`, `speed_limit_ms`, `shape_xy`, `shape_lonlat`, `reference[]` |
| `signals[]` | `junction_id`, `program_id`, `phases[]` (`state`, `duration_s`, `min_dur_s`, `max_dur_s`, `is_green_phase`), `controlled_lanes`, `links[]` |

`edges[].reference[]` carries the **real-world** TomTom values for each segment
(`street_name`, `speed_limit_kmh`, `distance_m`, `observed_avg_speed_kmh`,
`observed_samples`), so the GUI can show simulated against measured without
needing the source CSV.

Geometry is given in both SUMO `x/y` metres and WGS84 `lon/lat` — draw on a
plain canvas or on a real map, whichever you are building.

### `snapshot`

`seq`, `sim_time_s`, `wall_time`, plus:

**`network`** — `running_vehicles`, `halting_vehicles`, `mean_speed_ms`,
`mean_speed_ratio`, `congestion`, `vehicle_types` (counts per type),
`departed_this_step`, `arrived_this_step`, `pending_insertions`,
`expected_remaining`, `teleports_total`, `collisions_this_step`.

**`junctions[]`** — one per traffic light:

- `traffic_light`: `program_id`, `phase_index`, **`state`** (the raw
  `GgyrR` string, one character per link index), `phase_name`,
  `is_green_phase`, `time_in_phase_s`, `phase_duration_s`,
  `next_switch_sim_s`, **`seconds_to_switch`**.
- `approaches[]`: per controlled lane — `lane_id`, `edge_id`,
  **`segment_ids`**, `link_indices`, `signal_chars`, `has_green`,
  `vehicles`, `halting_vehicles`, `queue_length_m`, `lane_length_m`,
  `queue_ratio`, `mean_speed_ms`, `speed_limit_ms`, `speed_ratio`,
  `congestion`, `occupancy`, `waiting_time_s`, `travel_time_s`,
  `vehicle_types`, `co2_mg_s`, `fuel_ml_s`, `noise_db`.
- `totals`: `vehicles`, `queued_vehicles`, `queue_length_m`,
  `max_waiting_time_s`, `vehicle_types`, `worst_queue_ratio`,
  `green_speed_ratio`, `congestion`, **`spillback`**.

`signal_chars` is a list because one lane can feed several link indices at the
same junction — a protected left and a through movement can hold different
colours in the same phase, and collapsing them to one character would lose that.

**`segments[]`** — one per edge currently carrying traffic (edges with no
vehicles are omitted, so payload size tracks activity): `edge_id`,
**`segment_ids`**, `street_name`, `vehicles`, `halting_vehicles`,
`mean_speed_ms`, `speed_limit_ms`, `speed_ratio`, `congestion`,
`density_veh_per_km_lane`, `occupancy`, `waiting_time_s`, `travel_time_s`,
`vehicle_types`, `co2_mg_s`, `fuel_ml_s`.

**`vehicles[]`** — only with `--include-vehicles`: `id`, `type`,
`vehicle_class`, `edge_id`, `segment_ids`, `lane_id`, `position_xy`,
`position_lonlat`, `angle_deg`, `speed_ms`, `allowed_speed_ms`,
`acceleration_ms2`, `waiting_time_s`, `accumulated_waiting_s`, `time_loss_s`,
`distance_m`, `co2_mg_s`.

## How congestion is scored

Junctions and roads are scored **differently**, on purpose.

A signalised approach is *supposed* to be stopped for part of every cycle. Score
a junction on speed and every red light in the city reads as a jam. What actually
separates congestion from normal operation is whether the queue clears — so
junctions are scored on **`queue_ratio`**, the fraction of the approach its queue
fills:

| `queue_ratio` | level |
|---|---|
| ≤ 0.15 | `free` |
| ≤ 0.40 | `light` |
| ≤ 0.70 | `heavy` |
| > 0.70 | `jammed` |

At `queue_ratio` ≥ 0.95 the queue has reached the junction upstream and
`spillback` is set — the point at which one junction's problem becomes its
neighbour's.

Roads are scored on **`speed_ratio`**, speed as a fraction of that road's own
limit — the only speed measure that means the same thing on a 30 km/h side street
and a 70 km/h arterial:

| `speed_ratio` | level |
|---|---|
| ≥ 0.75 | `free` |
| ≥ 0.50 | `light` |
| ≥ 0.30 | `heavy` |
| < 0.30 | `jammed` |

Both tables are also in every `meta` message under `congestion_bands`, so a
client can render the thresholds without hard-coding them.

## Why `segment_ids` are on everything

Approaches, segments and vehicles all carry `segment_ids` — the original TomTom
CSV ids the network was built from. netconvert merged 299 source segments into
132 SUMO edges, so this is a one-to-many map, not a rename (see
`src/pravah/sumo_twin/segment_map.py`).

That mapping is what makes this feed useful outside the simulation: it lets the
receiving end line simulated numbers up against real measurements for the same
stretch of road, or against a camera keyed to a segment id — rather than against
a SUMO edge id, which means nothing anywhere else.

## Payload size

At the default 1 Hz with ~340 vehicles: **~40 KB per snapshot**, comfortable on
any LAN. `--include-vehicles` roughly triples it. If bandwidth is tight, raise
`--rate` — the per-junction and per-segment analytics are complete in every
snapshot, so a slower rate loses resolution, never fields.
