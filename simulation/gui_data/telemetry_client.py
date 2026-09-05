#!/usr/bin/env python3
"""Receives the PRAVAH telemetry feed on another machine.

This is both a working client and the reference for writing your own: the
whole protocol is "connect, read lines, json.loads each one", and
`stream_messages()` below is the entire implementation of that. A GUI in any
language can do the same thing without importing anything from this repo.

Three display modes:

  --mode summary   (default) one refreshing console dashboard: signal state,
                   queues and congestion per junction. Good for confirming the
                   link works and for watching a run without a GUI.
  --mode raw       pretty-prints every message as it arrives.
  --mode record    writes the feed to a .jsonl file and prints nothing but
                   progress -- for replaying or analysing later.

Usage on the receiving laptop:

    python3 telemetry_client.py --host <ip-of-the-simulating-laptop>
"""

import argparse
import json
import socket
import sys
import time
from datetime import datetime


def stream_messages(host, port, timeout=30.0):
    """Yields each decoded JSON message from the feed. The whole protocol.

    A socket read returns whatever bytes happen to have arrived, which will
    routinely be half a message or two and a half messages -- so the buffer
    below keeps the remainder between reads and only yields on a complete
    line. Reading a fixed number of bytes and parsing it directly is the one
    mistake that makes a line-delimited feed look intermittently corrupt.
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    buf = b""
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                return  # server closed
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    yield json.loads(line.decode("utf-8"))
    finally:
        sock.close()


BAR = {"free": "\033[32m", "light": "\033[33m", "heavy": "\033[35m", "jammed": "\033[31m"}
RESET = "\033[0m"


def render_summary(meta, snap):
    lines = []
    n = snap["network"]
    lines.append(f"PRAVAH telemetry  |  {meta.get('scenario','?').split('/')[-1]}"
                 f"  |  sim t={snap['sim_time_s']:.0f}s  seq={snap['seq']}")
    lines.append("=" * 78)
    types = "  ".join(f"{k}:{v}" for k, v in sorted(n["vehicle_types"].items()))
    colour = BAR.get(n["congestion"], "")
    lines.append(f"network: {n['running_vehicles']:>4} vehicles  "
                 f"{n['halting_vehicles']:>4} stopped  "
                 f"{n['mean_speed_ms']*3.6:>5.1f} km/h  "
                 f"{colour}{n['congestion']:>6}{RESET}  teleports={n['teleports_total']}")
    lines.append(f"         {types}")
    lines.append("-" * 78)
    for j in snap["junctions"]:
        tl = j["traffic_light"]
        t = j["totals"]
        colour = BAR.get(t["congestion"], "")
        lines.append(f"{j['junction_id']:<16} phase {tl['phase_index']} "
                     f"[{tl['state']}] {tl['seconds_to_switch']:>5.1f}s to switch")
        lines.append(f"{'':<16} {t['vehicles']:>4} veh  {t['queued_vehicles']:>4} queued  "
                     f"{t['queue_length_m']:>6.0f} m  wait {t['max_waiting_time_s']:>5.0f}s  "
                     f"fill {t['worst_queue_ratio']:>5.0%}  "
                     f"{colour}{t['congestion']}{RESET}"
                     f"{'  SPILLBACK' if t.get('spillback') else ''}")
        for a in sorted(j["approaches"], key=lambda a: -a["halting_vehicles"])[:3]:
            seg = ",".join(a["segment_ids"])[:28] or "-"
            state = "".join(a["signal_chars"])
            lines.append(f"{'':<18} {state:<4} {a['lane_id'][-22:]:<22} "
                         f"q={a['halting_vehicles']:<3} {a['mean_speed_ms']*3.6:>5.1f}km/h  seg {seg}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="IP of the laptop running the simulation")
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--mode", choices=("summary", "raw", "record"), default="summary")
    ap.add_argument("--out", default="pravah_feed.jsonl", help="record mode output file")
    ap.add_argument("--retry", action="store_true",
                    help="keep reconnecting if the server is not up yet or drops")
    args = ap.parse_args()

    while True:
        try:
            _consume(args)
            return
        except (ConnectionRefusedError, ConnectionResetError, socket.timeout, OSError) as exc:
            if not args.retry:
                print(f"connection failed: {exc}", file=sys.stderr)
                return 1
            print(f"connection failed ({exc}); retrying in 2s...", file=sys.stderr)
            time.sleep(2)


def _consume(args):
    meta = None
    count = 0
    out = open(args.out, "w", encoding="utf-8") if args.mode == "record" else None
    print(f"connecting to {args.host}:{args.port} ...", file=sys.stderr)
    try:
        for msg in stream_messages(args.host, args.port):
            if out:
                out.write(json.dumps(msg) + "\n")
                out.flush()

            if msg.get("type") == "meta":
                meta = msg
                if args.mode != "record":
                    print(f"connected: {len(meta['junctions'])} junctions, "
                          f"{len(meta['edges'])} edges, {len(meta['signals'])} signals, "
                          f"scenario {meta['scenario'].split('/')[-1]}", file=sys.stderr)
                if args.mode == "raw":
                    print(json.dumps(msg, indent=2))
            elif msg.get("type") == "snapshot":
                count += 1
                if args.mode == "summary":
                    # Home the cursor and clear, rather than scrolling: this is a
                    # dashboard, not a log.
                    sys.stdout.write("\033[H\033[J" + render_summary(meta or {}, msg) + "\n")
                    sys.stdout.flush()
                elif args.mode == "raw":
                    print(json.dumps(msg, indent=2))
                else:
                    print(f"\rrecorded {count} snapshots -> {args.out}", end="", file=sys.stderr)
            elif msg.get("type") == "end":
                print(f"\nsimulation ended at t={msg['sim_time_s']}s "
                      f"({msg['snapshots_sent']} snapshots)", file=sys.stderr)
                return
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
    finally:
        if out:
            out.close()
            print(f"\nwrote {count} snapshots to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main() or 0)
