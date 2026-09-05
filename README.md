# Pravah

A SUMO digital twin of the ITO corridor in Delhi, wired to a live React dashboard.

The simulation runs the real road network, and a live dashboard shows what it is
doing right now — every road coloured by measured congestion, every signal counting
down its actual phase, queues and waiting times per junction approach.

The point of the project is a controlled comparison. Both scenarios use the **same
road network and the same demand**; the only difference is the signal program:

| Scenario  | Signal program | What it represents |
| --------- | -------------- | ------------------ |
| `current` | `static`, fixed 150 s cycles with bad offsets | the junctions as they are timed today |
| `pravah`  | `actuated`, gap-responsive, 6–25 s green | the adaptive control being proposed |

Run them one after the other and watch the same traffic gridlock or flow.

---

## What's in here

```
pravah/
├── simulation/          the SUMO digital twin
│   ├── sumo/            network, routes, signal programs, scenario configs
│   ├── scripts/         scenario runners and the network/demand builders
│   ├── src/pravah/      the twin's Python package
│   ├── gui_data/        the live telemetry → dashboard pipeline
│   └── tests/
└── dashboard/           the React dashboard
    ├── backend/         pravah_middleware.py — ingest, fusion, WebSocket fan-out
    ├── src/             React 18 + Vite + TypeScript + Tailwind
    └── public/
```

## How the pieces connect

```
  simulation/gui_data/          simulation/gui_data/     dashboard/backend/        dashboard/
  pravah_telemetry.py    ──►    pravah_bridge.py   ──►   pravah_middleware.py ──► vite / browser
   SUMO under TraCI              translate + POST         fuse + fan out           React
        │                             │                        │                      │
        └── TCP JSONL :5555 ──────────┘  HTTP /api/ingest :8000 └── WS /ws/state ─────┘
```

1. **`pravah_telemetry.py`** runs SUMO under TraCI and broadcasts newline-delimited
   JSON over a raw TCP socket: per-edge speeds and occupancy, per-junction signal
   state, queue lengths, waiting times, vehicle-type counts, network totals.
2. **`pravah_bridge.py`** reads that feed, renames the fields to the dashboard's
   schema and POSTs one envelope per snapshot. It imports only the standard library,
   so it can run on a machine with no SUMO installed.
3. **`pravah_middleware.py`** holds the canonical network, ages out stale
   observations, scores congestion, and pushes snapshots to every connected browser.
4. The **dashboard** renders it.

## Requirements

- **Python 3.10+**
- **Node 18+**
- **SUMO** — installed from PyPI, no system package needed
- Linux or macOS. On Windows use WSL2 (`run_pipeline.sh` is bash).

## Install

```bash
git clone https://github.com/<you>/pravah.git
cd pravah

# SUMO and the simulation's Python dependencies
pip install eclipse-sumo traci sumolib

# the middleware
pip install -r dashboard/backend/requirements.txt

# the dashboard
cd dashboard && npm install && cd ..
```

That's the whole install. The simulation side needs nothing beyond `traci` and
`sumolib`; everything else it uses is in the standard library.

`SUMO_HOME` does not need to be set — the pipeline locates it from the installed
`sumo` package. Set it yourself only if you use a system SUMO build instead.

## Run it

One command starts all four processes and stops them together on Ctrl-C:

```bash
simulation/gui_data/run_pipeline.sh                     # adaptive signals
simulation/gui_data/run_pipeline.sh --scenario current  # today's fixed-time signals
```

Then open **http://localhost:5173**.

You should see the feed pill read `Live feed · 132/132 segments`, every road on the
map coloured, four traffic-light cards counting down in step with the map markers,
and the header stats advancing.

### Useful flags

| Flag | Effect |
| ---- | ------ |
| `--scenario pravah\|current` | which signal program to run (default `pravah`) |
| `--sumo-gui` | open SUMO's own window alongside the dashboard |
| `--fast` | run as fast as the machine allows — an hour of traffic in ~2 minutes |
| `--no-dev` | don't start the Vite dev server (you already have one) |
| `--rate` | telemetry snapshots per second (default 1.0) |
| `--port` / `--feed-port` | move the API off 8000 or the feed off 5555 |
| `--gui-repo` | point at a dashboard checkout elsewhere |

`--sumo-gui` needs a real terminal: `sumo-gui` exits silently with status 0 when it
has no controlling tty, so don't run it through a pipe.

### Give it time

By default the simulation runs in real time, and **the two scenarios are
indistinguishable for the first four minutes** — the network has to fill before the
signal program matters:

| sim clock | `pravah` (adaptive) | `current` (fixed-time) |
| --------- | ------------------- | ---------------------- |
| 4 min  | 226 vehicles @ 50 km/h | 226 vehicles @ 49 km/h |
| 10 min | 307 vehicles @ 48 km/h | 366 vehicles @ 35 km/h |
| 30 min | 318 vehicles @ 45 km/h | 786 vehicles @ 14 km/h |
| 60 min | 330 vehicles @ 44 km/h | 1723 vehicles @ 4 km/h |

So: ~10 minutes of wall clock before the difference shows, ~30 before the jam is
obvious. Use `--fast` to reach gridlock in about two minutes, then restart in real
time to watch it. Under `current`, junction `cluster_2_72` is the first to fail —
it goes red with a **SPILLBACK** badge and a climbing wait time.

## Running it by hand

`run_pipeline.sh` is only a convenience wrapper. The four processes, in four
terminals:

```bash
# 1. middleware
python3 dashboard/backend/pravah_middleware.py serve --port 8000

# 2. simulation + telemetry
simulation/gui_data/pravah_telemetry.py \
  --sumocfg simulation/sumo/pravah_ito_jam.sumocfg --realtime --gui

# 3. bridge
simulation/gui_data/pravah_bridge.py --api http://127.0.0.1:8000 --retry

# 4. dashboard
cd dashboard && npm run dev
```

`--retry` means start order doesn't matter — the bridge waits for whatever isn't up.

## Two machines

The bridge has no SUMO dependency, so the simulation and the dashboard can live on
different computers. Put the **middleware on the GUI machine**: it then talks to the
browser over `localhost`, so the default CORS list and API URL are already correct
and only the simulation machine needs configuring.

```bash
# on the GUI machine (B)
python3 dashboard/backend/pravah_middleware.py serve --port 8000
cd dashboard && npm run dev

# on the simulation machine (A)
simulation/gui_data/pravah_telemetry.py \
  --sumocfg simulation/sumo/pravah_ito_jam.sumocfg --realtime
simulation/gui_data/pravah_bridge.py --api http://<B-ip>:8000 --retry
```

If you would rather run the middleware on A and keep B a thin client, two things
have to change, because B's browser is then a cross-origin caller:

```bash
# on A
python3 dashboard/backend/pravah_middleware.py serve --cors "http://<B-ip>:5173"
# on B
echo "VITE_PRAVAH_API=http://<A-ip>:8000" >> dashboard/.env
npm run dev -- --host
```

Open ports 8000 (and 5173 in the second arrangement) if either machine runs a firewall.

## Configuration

Copy `dashboard/.env.example` to `dashboard/.env` to change either setting:

| Variable            | Default                 | Meaning |
| ------------------- | ----------------------- | ------- |
| `VITE_MAPBOX_TOKEN` | empty                   | only needed for the Mapbox basemap; the default basemap is OpenStreetMap and needs no token |
| `VITE_PRAVAH_API`   | `http://localhost:8000` | where the browser looks for the middleware |
| `PRAVAH_CORS`       | `localhost:5173,127.0.0.1:5173` | origins the middleware will accept (server side) |

### Ports

| Port | Process |
| ---- | ------- |
| 5555 | telemetry TCP feed |
| 8000 | middleware HTTP + WebSocket |
| 5173 | Vite dev server |

### Endpoints

| Endpoint | Purpose |
| -------- | ------- |
| `GET /api/network` | the canonical network the dashboard draws |
| `GET /api/state` | the current snapshot |
| `GET /api/health` | per-source health, message counts, unresolved ids |
| `POST /api/ingest` | where the bridge posts |
| `WS /ws/state` | the live push the browser subscribes to |

## The canonical network

The dashboard draws from `dashboard/backend/data/network.json` — **132 road
segments, 99 junctions (4 signalised), 183 movements**, generated from the SUMO
network itself by `simulation/gui_data/export_network.py`:

```bash
simulation/gui_data/export_network.py
```

`run_pipeline.sh` reruns this automatically whenever `pravah.net.xml` is newer than
the exported JSON, so you only need it by hand if you edit the network.

Generating it from SUMO rather than fetching an OSM extract is deliberate. Segment
ids are `S:sumo:<edge>` and junction ids `J:sumo:<junction>` on both sides of the
pipe, which makes the middleware's id crosswalk an identity map: every id the
simulation reports is a road the dashboard already knows about, so coverage is 100%
and `unresolved_ids` stays empty. The previous OSM extract was centred a kilometre
west of the SUMO network and most edges had no counterpart at all. The earlier
extract is kept beside it as `network.osm.json`.

## Checking it works

```bash
curl -s localhost:8000/api/network | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["segments"]), "segments")'
curl -s localhost:8000/api/state   | python3 -m json.tool | head -20
curl -s localhost:8000/api/health  | python3 -m json.tool
```

A healthy pipeline reports `segments_live: 132`, `segments_stale: 0`,
`segments_no_data: 0`, `junctions_reporting: 4`, `rejected: 0`, and an empty
`unresolved_ids`.

## Troubleshooting

**No SUMO window.** The simulation runs headless by default — the dashboard is the
point. Add `--sumo-gui`, from a real terminal.

**`Address already in use`.** A process from an earlier run still holds the port.
`run_pipeline.sh` frees 8000 and 5555 itself; to do it by hand, find the owner with
`ss -ltnp "sport = :5555"` and kill that pid. Don't use `pkill -f` — the pattern
matches the command line invoking it and takes your own shell down with it.

**Roads grey / "no data".** The bridge isn't posting. Check `/api/health`: if
`sources.sumo` is missing, the bridge isn't running; if its `age_s` is climbing, the
telemetry feed has stopped.

**Dashboard empty but the API has data.** A CORS or API-URL mismatch. The browser
console will say which. See the two-machine section above.

**`current` doesn't look congested.** It isn't yet — see the timing table. Give it
ten minutes, or use `--fast`.

## Tests

```bash
cd simulation && python3 -m pytest tests/ -q     # 45 passed, 14 skipped
cd dashboard  && npm run build                   # tsc must pass clean
```

The skips are the tests that need a TomTom API key. If pytest dies inside its own
plugin loader with `No module named '_pytest.scope'`, an old distro pytest is
clashing with a newer `anyio`; `-p no:anyio` gets past it, or install pytest into
the same environment as everything else.

## Rebuilding the scenarios

The committed network and routes are ready to run; these regenerate them from
source if you want to change the corridor or the demand.

```bash
simulation/scripts/build_sumo_network.py       # OSM → pravah.net.xml
simulation/scripts/generate_calibrated_demand.py
simulation/scripts/build_busy_freeflow.py      # the actuated scenario
simulation/scripts/build_jam_scenario.py       # the fixed-time one
simulation/scripts/compare_signal_plans.py     # the headline comparison
```

Route alternatives (`*.rou.alt.xml`) and trip files are duarouter intermediates and
are not committed — the scenarios read only the final `.rou.xml`.
