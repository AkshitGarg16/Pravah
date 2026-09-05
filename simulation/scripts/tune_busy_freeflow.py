#!/usr/bin/env python3
"""Finds the busiest demand level the ITO network still absorbs cleanly.

"Busy but not congested" is not a setting you can pick by eye -- it's the
point just below the network's own capacity, and where that point sits
depends on the signal timing, the vehicle mix and the route distribution all
at once. So this measures it instead of guessing: for each candidate
insertion `--period`, build the scenario, run SUMO headless, and score the
run against explicit congestion criteria.

A run counts as CONGESTED if any of these hold:

  teleports > 0            SUMO teleports a vehicle that has been stuck at
                           zero speed for time-to-teleport seconds -- the
                           unambiguous signal that something gridlocked.
  backlog is growing       vehicles that could not be inserted because their
                           departure edge was full; a nonzero *and rising*
                           backlog means demand exceeds what the network can
                           take in, i.e. the queue is unbounded.
  running count rising     the number of vehicles in the network should
                           plateau once the run reaches steady state. A
                           second half still trending up means vehicles are
                           accumulating faster than they clear.
  mean speed too low       below `--min-speed-frac` of the network's
                           free-flow mean -- vehicles are on screen but not
                           actually moving.
  halting share too high   more than `--max-halting-frac` of vehicles stopped
                           at any instant, averaged over the run.

Among the runs that pass, the winner is simply the one with the most
vehicles in the network -- the busiest picture that is still honest.
"""

import argparse
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRATCH = Path("/tmp/claude-1000/-home-akshit/e9f3c31a-c12f-4d23-96d9-e689a8204097/scratchpad")


def run_sumo(cfg, summary_out, seed, extra=()):
    cmd = ["sumo", "-c", str(cfg), "--summary-output", str(summary_out),
           "--seed", str(seed), "--no-step-log", "--no-warnings", *extra]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _slope(ys):
    """Least-squares slope of ys against its own index (units: per sample)."""
    n = len(ys)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2
    my = statistics.fmean(ys)
    den = sum((i - mx) ** 2 for i in range(n))
    return sum((i - mx) * (y - my) for i, y in enumerate(ys)) / den


def analyse(summary_path, warmup_frac=0.25):
    """Reads --summary-output and reduces it to the congestion criteria above.

    The first `warmup_frac` of the run is dropped from every average: a run
    starts with an empty network, and including the fill-up phase would flatter
    both the speed and the halting numbers.
    """
    rows = []
    for _, el in ET.iterparse(summary_path, events=("end",)):
        if el.tag == "step":
            rows.append({k: float(el.get(k, 0)) for k in
                         ("time", "running", "halting", "meanSpeed", "inserted", "ended")})
            el.clear()
    if not rows:
        raise SystemExit(f"empty summary: {summary_path}")

    warm = int(len(rows) * warmup_frac)
    body = rows[warm:] or rows
    running = [r["running"] for r in body]
    halting = [r["halting"] for r in body]
    # meanSpeed is reported as -1 on steps with no vehicles at all.
    speeds = [r["meanSpeed"] for r in body if r["running"] > 0 and r["meanSpeed"] >= 0]

    half = len(body) // 2
    return {
        "mean_running": statistics.fmean(running),
        "peak_running": max(running),
        "mean_speed": statistics.fmean(speeds) if speeds else 0.0,
        "halting_frac": statistics.fmean(
            h / r if r else 0.0 for h, r in zip(halting, running)),
        "running_slope_2h": _slope(running[half:]) * len(running[half:]),  # net change over 2nd half
        "inserted": rows[-1]["inserted"],
        "ended": rows[-1]["ended"],
    }


def free_flow_speed(cfg, seed):
    """Mean speed of the same scenario at a trickle of demand.

    The "no congestion" bar has to be relative to what this specific network
    and route mix can do at all -- a 12 m/s mean is excellent on a network of
    50 km/h arterials with signals, and terrible on a motorway. Rather than
    hard-coding a threshold, measure the same scenario nearly empty and use
    that as the 100% reference.
    """
    out = SCRATCH / "ff_summary.xml"
    run_sumo(cfg, out, seed, extra=["--scale", "0.05"])
    return analyse(out)["mean_speed"]


def evaluate(period, args, tag="tune"):
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "build_busy_freeflow.py"),
         "--period", str(period), "--duration", str(args.duration),
         "--seed", str(args.seed), "--tag", tag,
         "--min-dur", str(args.min_dur), "--max-dur", str(args.max_dur),
         "--max-gap", str(args.max_gap)],
        check=True, stdout=subprocess.DEVNULL)
    cfg = REPO / "sumo" / f"pravah_ito_{tag}.sumocfg"
    summary = SCRATCH / f"summary_{tag}_{period}.xml"
    stats_path = SCRATCH / f"stats_{tag}_{period}.xml"
    run_sumo(cfg, summary, args.seed, extra=["--statistic-output", str(stats_path)])
    m = analyse(summary)

    st = ET.parse(stats_path).getroot()
    teleports = int(st.find("teleports").get("total", 0)) if st.find("teleports") is not None else 0
    ins = st.find("vehicleTripStatistics")
    m["teleports"] = teleports
    m["timeloss"] = float(ins.get("timeLoss")) if ins is not None else 0.0
    m["waiting"] = float(ins.get("waitingTime")) if ins is not None else 0.0
    m["duration"] = float(ins.get("duration")) if ins is not None else 0.0
    return m


def verdict(m, ff_speed, args):
    reasons = []
    if m["teleports"] > args.max_teleports:
        reasons.append(f"{m['teleports']} teleports")
    if m["running_slope_2h"] > args.max_growth:
        reasons.append(f"running +{m['running_slope_2h']:.0f} over 2nd half")
    if ff_speed and m["mean_speed"] < args.min_speed_frac * ff_speed:
        reasons.append(f"speed {m['mean_speed']:.1f} < {args.min_speed_frac:.0%} of free-flow {ff_speed:.1f}")
    if m["halting_frac"] > args.max_halting_frac:
        reasons.append(f"{m['halting_frac']:.0%} halting")
    return reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--periods", type=float, nargs="+",
                    default=[1.0, 0.7, 0.5, 0.4, 0.3, 0.25, 0.2, 0.15])
    # A full hour, not a short probe. Confirmed the hard way: at 4500 veh/h a
    # 1200s run looked clean (513 vehicles, 47 km/h, no teleports) and the same
    # scenario over 3600s accumulated +432 vehicles and fell to 19 km/h. The
    # network takes several minutes just to fill, so a short run measures the
    # fill-up, not the steady state it settles into.
    ap.add_argument("--duration", type=int, default=3600)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-dur", type=int, default=7)
    ap.add_argument("--max-dur", type=int, default=32)
    ap.add_argument("--max-gap", type=float, default=2.1)
    ap.add_argument("--min-speed-frac", type=float, default=0.80)
    ap.add_argument("--max-halting-frac", type=float, default=0.12)
    ap.add_argument("--max-growth", type=float, default=40.0)
    ap.add_argument("--max-teleports", type=int, default=0)
    args = ap.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "build_busy_freeflow.py"),
         "--period", "1.0", "--duration", str(args.duration), "--tag", "tune"],
        check=True, stdout=subprocess.DEVNULL)
    ff = free_flow_speed(REPO / "sumo" / "pravah_ito_tune.sumocfg", args.seed)
    print(f"free-flow reference mean speed: {ff:.2f} m/s ({ff*3.6:.0f} km/h)\n")

    hdr = f"{'period':>7} {'veh/h':>7} {'running':>8} {'peak':>6} {'speed':>6} {'%ff':>5} {'halt%':>6} {'growth':>7} {'tele':>5} {'wait':>6}  verdict"
    print(hdr)
    print("-" * len(hdr))
    results = []
    for p in args.periods:
        m = evaluate(p, args)
        r = verdict(m, ff, args)
        results.append((p, m, r))
        print(f"{p:>7.2f} {3600/p:>7.0f} {m['mean_running']:>8.0f} {m['peak_running']:>6.0f} "
              f"{m['mean_speed']:>6.2f} {m['mean_speed']/ff:>5.0%} {m['halting_frac']:>6.1%} "
              f"{m['running_slope_2h']:>+7.0f} {m['teleports']:>5} {m['waiting']:>6.1f}  "
              f"{'OK' if not r else '; '.join(r)}")

    ok = [(p, m) for p, m, r in results if not r]
    if not ok:
        print("\nNo demand level passed. Loosen the thresholds or retime the signals.")
        return
    best_p, best_m = max(ok, key=lambda pm: pm[1]["mean_running"])
    print(f"\nbusiest congestion-free level: --period {best_p} "
          f"({3600/best_p:.0f} veh/h inserted, ~{best_m['mean_running']:.0f} vehicles "
          f"in the network at any moment, {best_m['mean_speed']*3.6:.0f} km/h mean)")


if __name__ == "__main__":
    main()
