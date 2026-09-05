# Pravah dashboard

The GUI half of [Pravah](../README.md). **Start with the root README** — this half
shows nothing on its own; it renders whatever the simulation is feeding it.

React 18 + Vite + TypeScript, Tailwind, react-map-gl (Mapbox GL JS), zustand,
lucide-react. Light theme only, no routing, no component library — every component
is hand-built.

## Run

```bash
npm install
pip install -r backend/requirements.txt

python3 backend/pravah_middleware.py serve --port 8000   # terminal 1
npm run dev                                              # terminal 2
```

Then start the simulation and bridge from `../simulation` — see the root README.
Without them the dashboard connects, draws the road network from
`public/network.json`, and waits, with every panel saying so.

## Layout

```
backend/
  pravah_middleware.py   ingest, id crosswalk, fusion, WebSocket fan-out
  data/network.json      the canonical network (generated from the SUMO net)
  data/network.osm.json  the earlier OSM extract, kept for reference
  train_*.py             offline model training, not part of the live pipeline
src/
  store/useLiveStore.ts       everything the feed reports
  store/useDashboardStore.ts  everything the user has done — view, selection, mode
  utils/liveLights.ts         junctions → light cards
  utils/liveAlerts.ts         feed → alert list
  services/liveFeed.ts        WebSocket client and REST fallbacks
  components/                 map, panels, cards
```

The split between the two stores is the thing to know: `useLiveStore` holds traffic,
`useDashboardStore` holds interaction. Nothing invents traffic any more — an earlier
version cycled every light's phase and re-randomised its pressures on a one-second
timer, which is now the simulation's job.

## Environment

Copy `.env.example` to `.env`:

| Variable            | Default                 | Meaning |
| ------------------- | ----------------------- | ------- |
| `VITE_MAPBOX_TOKEN` | empty                   | only for the Mapbox basemap; the default basemap is OpenStreetMap and needs no token. Bring your own from mapbox.com if you want it |
| `VITE_PRAVAH_API`   | `http://localhost:8000` | where the browser looks for the middleware |

## Panels

- **Map** — road segments coloured by live congestion with hover popups;
  phase-coloured signal markers, teal ring on the selection.
- **Traffic lights** — one card per signalised junction: phase, seconds to change,
  queue fill, max wait, vehicle mix, spillback, and the three worst approaches.
- **System health** — one row per hop of the pipeline (SUMO feed, middleware,
  dashboard link, simulation), so a dead dashboard says which hop to look at.
- **Alerts** — derived from the feed: spillback, jammed segments, waits over 120 s,
  climbing teleport counts. Clicking one flies the map to it.
- **Infrastructure mode** — the site ranker; still runs on its own scored data.
