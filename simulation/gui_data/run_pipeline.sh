#!/usr/bin/env bash
# Starts the whole SUMO -> dashboard pipeline and stops it all together.
#
#   simulation            bridge              middleware           browser
#   pravah_telemetry.py ─ pravah_bridge.py ─ pravah_middleware.py ─ vite
#        TraCI      TCP :5555      HTTP /api/ingest      WS /ws/state
#
#   gui_data/run_pipeline.sh                    # free-flowing scenario
#   gui_data/run_pipeline.sh --scenario current # the jammed one
#   gui_data/run_pipeline.sh --no-dev           # leave the dev server alone
#   gui_data/run_pipeline.sh --sumo-gui         # open sumo-gui alongside
#
# Ctrl-C stops every process it started, including any it inherited on those
# ports from an earlier run.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUI_REPO="${GUI_REPO:-$(cd "$REPO/.." && pwd)/dashboard}"
SCENARIO="pravah"
RATE=1.0
API_PORT=8000
FEED_PORT=5555
START_DEV=1
REALTIME="--realtime"
SUMO_GUI=

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario) SCENARIO="$2"; shift 2 ;;
    --rate) RATE="$2"; shift 2 ;;
    --port) API_PORT="$2"; shift 2 ;;
    --feed-port) FEED_PORT="$2"; shift 2 ;;
    --gui-repo) GUI_REPO="$2"; shift 2 ;;
    --no-dev) START_DEV=0; shift ;;
    # The dashboard is the point, so the simulation runs headless by default.
    # This opens SUMO's own window too, for watching the vehicles themselves.
    --sumo-gui) SUMO_GUI="--gui"; shift ;;
    # Without --realtime the simulation runs as fast as the machine allows,
    # which is an hour of traffic in about two minutes -- useful for reaching a
    # jam quickly, useless for watching one.
    --fast) REALTIME=""; shift ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -n "$SUMO_GUI" && ! -t 1 ]]; then
  echo "--sumo-gui needs a terminal: sumo-gui exits silently with status 0 when" >&2
  echo "it has no controlling tty, so run this from a terminal, not a pipe." >&2
  exit 1
fi

case "$SCENARIO" in
  pravah)  SUMOCFG="$REPO/sumo/pravah_ito_busy.sumocfg" ;;
  current) SUMOCFG="$REPO/sumo/pravah_ito_jam.sumocfg" ;;
  *) echo "--scenario must be 'pravah' (free-flowing) or 'current' (jammed)" >&2; exit 1 ;;
esac

# SUMO_HOME points at the SUMO data directory. The pip wheel puts it inside the
# installed `sumo` package, wherever that landed for this interpreter, so ask
# Python rather than guessing a version-specific site-packages path.
if [[ -z "${SUMO_HOME:-}" ]]; then
  SUMO_HOME="$(python3 -c 'import sumo, pathlib; print(pathlib.Path(sumo.__file__).parent)' 2>/dev/null)"
fi
export SUMO_HOME
[[ -n "$SUMO_HOME" && -d "$SUMO_HOME" ]] || {
  echo "SUMO_HOME is not set and the 'sumo' package was not importable." >&2
  echo "Install it with:  pip install eclipse-sumo traci sumolib" >&2
  exit 1
}
[[ -d "$GUI_REPO" ]] || { echo "dashboard not found at $GUI_REPO" >&2; exit 1; }

PIDS=()

# Kill by listening port, never by name: a `pkill -f <pattern>` also matches the
# command line invoking it and takes the calling shell down with it.
free_port() {
  local pid
  pid=$(ss -ltnpH "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  if [[ -n "${pid:-}" ]]; then
    echo "  freeing port $1 (pid $pid)"
    kill "$pid" 2>/dev/null
    sleep 1
  fi
}

cleanup() {
  echo
  echo "stopping..."
  for pid in "${PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null
  done
  wait 2>/dev/null
  exit 0
}
trap cleanup INT TERM

NETWORK_JSON="$GUI_REPO/backend/data/network.json"
NET_XML="$REPO/sumo/network/pravah.net.xml"
if [[ ! -f "$NETWORK_JSON" || "$NET_XML" -nt "$NETWORK_JSON" ]]; then
  echo "exporting the canonical network from $NET_XML"
  "$REPO/gui_data/export_network.py" --gui-repo "$GUI_REPO" || exit 1
fi

echo "scenario: $SCENARIO  ($SUMOCFG)"
free_port "$API_PORT"
free_port "$FEED_PORT"

echo "[1/3] middleware on :$API_PORT"
python3 "$GUI_REPO/backend/pravah_middleware.py" serve --port "$API_PORT" \
  --cors "http://localhost:5173,http://127.0.0.1:5173" &
PIDS+=($!)

# The middleware has to be listening before the bridge starts posting, and the
# telemetry has to be listening before the bridge dials it.
sleep 3

echo "[2/3] simulation + telemetry on :$FEED_PORT${SUMO_GUI:+  (sumo-gui)}"
"$REPO/gui_data/pravah_telemetry.py" --sumocfg "$SUMOCFG" --port "$FEED_PORT" \
  --rate "$RATE" $REALTIME $SUMO_GUI &
PIDS+=($!)

sleep 8

echo "[3/3] bridge -> http://127.0.0.1:$API_PORT"
"$REPO/gui_data/pravah_bridge.py" --feed-port "$FEED_PORT" \
  --api "http://127.0.0.1:$API_PORT" --retry &
PIDS+=($!)

if [[ "$START_DEV" == "1" ]]; then
  echo
  echo "starting the dashboard dev server"
  ( cd "$GUI_REPO" && npm run dev ) &
  PIDS+=($!)
  sleep 3
  echo
  echo "  dashboard   http://localhost:5173"
else
  echo
  echo "  dashboard   run 'npm run dev' in $GUI_REPO"
fi
echo "  api         http://localhost:$API_PORT/api/state"
echo "  health      http://localhost:$API_PORT/api/health"
echo
echo "Ctrl-C stops everything."

wait
