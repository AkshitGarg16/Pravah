"""
PRAVAH — spatio-temporal GNN for T+60s network-state forecasting.

Graph follows "The Graph Neural Network Approach for Pravah":
  nodes = road segments (speed, free-flow, congestion, length, pressure)
  edges = junctions, directional (signal phase + timing, heavy vehicles, EV)
  global = time of day, day of week, weather

Given the last W one-minute observations of the whole graph, the model predicts
every segment's state at T+60s; intermediate seconds come from linear projection
between the current observation and the forecast.

Training data is produced by a signalised micro-simulator, which supplies the
control-response pairs (signal actuation -> traffic response) that observational
FCD alone cannot provide.

    python backend/train_gnn_forecast.py
    python backend/train_gnn_forecast.py --no-graph      # ablation
"""

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

ARTIFACTS = Path(__file__).parent / "artifacts"

# ---------------------------------------------------------------- road network

SEGMENTS = [
    ("SG-201", "Bahadur Shah Zafar Marg", 4, 50, [(28.6289, 77.2412), (28.6310, 77.2411), (28.6338, 77.2409)]),
    ("SG-202", "Bahadur Shah Zafar Marg", 4, 50, [(28.6338, 77.2409), (28.6365, 77.2407), (28.6392, 77.2405)]),
    ("SG-203", "Vikas Marg", 3, 45, [(28.6289, 77.2412), (28.6292, 77.2440), (28.6296, 77.2478)]),
    ("SG-204", "Vikas Marg", 3, 45, [(28.6296, 77.2478), (28.6297, 77.2510), (28.6298, 77.2540)]),
    ("SG-205", "Tilak Marg", 3, 48, [(28.6289, 77.2412), (28.6278, 77.2385), (28.6262, 77.2345)]),
    ("SG-206", "Mathura Road", 6, 55, [(28.6289, 77.2412), (28.6260, 77.2425), (28.6197, 77.2455)]),
    ("SG-207", "Ring Road (Pragati Maidan)", 4, 52, [(28.6197, 77.2455), (28.6200, 77.2488), (28.6206, 77.2515)]),
    ("SG-208", "Ring Road (IP Estate)", 4, 52, [(28.6206, 77.2515), (28.6250, 77.2525), (28.6298, 77.2540)]),
    ("SG-209", "Ring Road (Rajghat)", 4, 52, [(28.6392, 77.2405), (28.6400, 77.2450), (28.6407, 77.2494)]),
    ("SG-210", "Ring Road (Yamuna Bank)", 4, 52, [(28.6407, 77.2494), (28.6355, 77.2518), (28.6298, 77.2540)]),
    ("SG-211", "Deen Dayal Upadhyay Marg", 3, 45, [(28.6289, 77.2412), (28.6303, 77.2385), (28.6316, 77.2352)]),
    ("SG-212", "Minto Road", 2, 40, [(28.6316, 77.2352), (28.6308, 77.2318), (28.6300, 77.2290)]),
    ("SG-213", "Sikandra Road", 2, 40, [(28.6262, 77.2345), (28.6288, 77.2300), (28.6300, 77.2290)]),
    ("SG-214", "Purana Quila Road", 2, 42, [(28.6262, 77.2345), (28.6215, 77.2420), (28.6197, 77.2455)]),
]

JUNCTIONS = {
    "J-ITO": ["SG-201", "SG-203", "SG-205", "SG-206", "SG-211"],
    "J-PRESS": ["SG-201", "SG-202"],
    "J-DELHI-GATE": ["SG-202", "SG-209"],
    "J-VIKAS-MID": ["SG-203", "SG-204"],
    "J-MANDI-HOUSE": ["SG-205", "SG-213", "SG-214"],
    "J-PRAGATI": ["SG-206", "SG-207", "SG-214"],
    "J-IP-METRO": ["SG-207", "SG-208"],
    "J-YAMUNA": ["SG-204", "SG-208", "SG-210"],
    "J-RAJGHAT": ["SG-209", "SG-210"],
    "J-DDU": ["SG-211", "SG-212"],
    "J-MINTO": ["SG-212", "SG-213"],
}

SIGNALISED = {"J-ITO", "J-PRESS", "J-DELHI-GATE", "J-VIKAS-MID", "J-MANDI-HOUSE", "J-PRAGATI"}
ADAPTIVE = {"J-ITO", "J-VIKAS-MID", "J-PRAGATI"}

DT = 10
OBS_EVERY = 60
SUB_STEPS = OBS_EVERY // DT
K_JAM = 110.0
SAT_FLOW = 1800.0
V_MIN = 6.0
AMBER = 4.0


def haversine_km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def build_graph():
    seg_ids = [s[0] for s in SEGMENTS]
    idx = {sid: i for i, sid in enumerate(seg_ids)}
    lanes = np.array([s[2] for s in SEGMENTS], dtype=np.float64)
    v_free = np.array([s[3] for s in SEGMENTS], dtype=np.float64)
    length = np.array(
        [sum(haversine_km(c[k], c[k + 1]) for k in range(len(c) - 1)) for *_, c in SEGMENTS]
    )

    src, dst, ejun, japproach = [], [], [], []
    jun_ids = list(JUNCTIONS)
    for j, jid in enumerate(jun_ids):
        members = JUNCTIONS[jid]
        for u in members:
            for v in members:
                if u == v:
                    continue
                src.append(idx[u])
                dst.append(idx[v])
                ejun.append(j)
                japproach.append(members.index(u))

    return {
        "seg_ids": seg_ids,
        "jun_ids": jun_ids,
        "lanes": lanes,
        "v_free": v_free,
        "length": length,
        "capacity": K_JAM * lanes * length,
        "src": np.array(src),
        "dst": np.array(dst),
        "edge_jun": np.array(ejun),
        "edge_approach": np.array(japproach),
        "jun_members": [[idx[m] for m in JUNCTIONS[jid]] for jid in jun_ids],
        "jun_signalised": np.array([jid in SIGNALISED for jid in jun_ids]),
        "jun_adaptive": np.array([jid in ADAPTIVE for jid in jun_ids]),
    }


# ------------------------------------------------------------------ simulator


def demand_profile(hour):
    return (
        0.45
        + 0.95 * math.exp(-(((hour - 9.6) / 1.35) ** 2))
        + 1.10 * math.exp(-(((hour - 18.4) / 1.5) ** 2))
        + 0.25 * math.exp(-(((hour - 13.5) / 2.2) ** 2))
    )


def simulate(g, days, hours_per_day, start_hour, rng):
    n_nodes = len(g["seg_ids"])
    n_edges = len(g["src"])
    n_jun = len(g["jun_ids"])
    src, dst, ejun = g["src"], g["dst"], g["edge_jun"]
    cap = g["capacity"]
    lanes, v_free, length = g["lanes"], g["v_free"], g["length"]

    max_members = max(len(m) for m in g["jun_members"])
    members = np.full((n_jun, max_members), -1)
    n_members = np.zeros(n_jun, dtype=int)
    for j, m in enumerate(g["jun_members"]):
        members[j, : len(m)] = m
        n_members[j] = len(m)

    turn = rng.uniform(0.4, 1.0, size=n_edges)
    gen_weight = rng.uniform(0.6, 1.4, size=n_nodes) * lanes * length

    steps_per_day = hours_per_day * 3600 // DT
    total_steps = days * steps_per_day
    n_obs = total_steps // SUB_STEPS

    node_out = np.zeros((n_obs, n_nodes, 5), dtype=np.float32)
    edge_out = np.zeros((n_obs, n_edges, 6), dtype=np.float32)
    glob_out = np.zeros((n_obs, 7), dtype=np.float32)
    fine_out = np.zeros((total_steps, n_nodes, 3), dtype=np.float32)

    n = cap * rng.uniform(0.10, 0.20, size=n_nodes)
    served = np.zeros(n_jun, dtype=int)
    timer = rng.uniform(15, 40, size=n_jun)
    ev_timer = np.zeros(n_jun)
    ev_approach = np.zeros(n_jun, dtype=int)
    hv = rng.poisson(1.0, size=n_edges).astype(np.float64)
    incident = np.ones(n_nodes)
    incident_timer = np.zeros(n_nodes)
    inflow_ema = np.zeros(n_nodes)
    outflow_ema = np.zeros(n_nodes)

    rain_day = np.zeros(days)
    for d in range(days):
        rain_day[d] = rng.uniform(0.3, 1.0) if rng.random() < 0.25 else 0.0

    for step in range(total_steps):
        day = step // steps_per_day
        sec = (step % steps_per_day) * DT
        hour = start_hour + sec / 3600.0
        dow = (day + 2) % 7
        rain = rain_day[day] * (0.5 + 0.5 * math.sin(sec / 4000.0))
        rain = max(0.0, rain)
        weekend = 1.0 if dow >= 5 else 0.0

        weather_v = 1.0 - 0.28 * rain
        weather_s = 1.0 - 0.18 * rain
        demand = demand_profile(hour) * (0.72 if weekend else 1.0)

        incident_timer = np.maximum(incident_timer - DT, 0)
        incident = np.where(incident_timer > 0, incident, 1.0)
        if rng.random() < 0.0008:
            i = rng.integers(n_nodes)
            incident[i] = rng.uniform(0.35, 0.6)
            incident_timer[i] = rng.uniform(600, 1500)

        occupancy = n / cap
        queue_at = occupancy[members.clip(min=0)] * (members >= 0)

        expire = timer <= 0
        if expire.any():
            nxt = np.where(expire, (served + 1) % np.maximum(n_members, 1), served)
            base = np.where(g["jun_signalised"], 32.0, 18.0)
            extra = np.where(
                g["jun_adaptive"],
                26.0 * queue_at[np.arange(n_jun), nxt.clip(max=max_members - 1)],
                0.0,
            )
            served = np.where(expire, nxt, served)
            timer = np.where(expire, base + extra, timer)

        ev_timer = np.maximum(ev_timer - DT, 0)
        new_ev = (rng.random(n_jun) < 0.0006) & (ev_timer <= 0)
        if new_ev.any():
            ev_approach = np.where(new_ev, rng.integers(0, max_members, n_jun) % np.maximum(n_members, 1), ev_approach)
            ev_timer = np.where(new_ev, rng.uniform(40, 90, n_jun), ev_timer)
        ev_on = ev_timer > 0
        served = np.where(ev_on, ev_approach, served)

        served_seg = members[np.arange(n_jun), served.clip(max=max_members - 1)]
        is_amber = (timer <= AMBER) & g["jun_signalised"] & ~ev_on

        edge_green = src == served_seg[ejun]
        phase_factor = np.where(
            edge_green,
            np.where(is_amber[ejun], 0.35, 1.0),
            np.where(g["jun_signalised"][ejun], 0.04, 0.30),
        )

        hv_penalty = 1.0 / (1.0 + 0.09 * hv)
        sat = SAT_FLOW * lanes[src] * DT / 3600.0
        desired = sat * phase_factor * hv_penalty * weather_s * incident[src] * turn
        desired = np.minimum(desired, np.maximum(n[src], 0) * 0.9)

        want_out = np.zeros(n_nodes)
        np.add.at(want_out, src, desired)
        scale_out = np.where(want_out > 1e-9, np.minimum(1.0, n / np.maximum(want_out, 1e-9)), 1.0)
        flow = desired * scale_out[src]

        space = np.maximum(cap - n, 0.0)
        want_in = np.zeros(n_nodes)
        np.add.at(want_in, dst, flow)
        scale_in = np.where(want_in > 1e-9, np.minimum(1.0, space / np.maximum(want_in, 1e-9)), 1.0)
        flow = flow * scale_in[dst]

        out_v = np.zeros(n_nodes)
        in_v = np.zeros(n_nodes)
        np.add.at(out_v, src, flow)
        np.add.at(in_v, dst, flow)

        external = gen_weight * demand * DT / 60.0 * rng.uniform(0.8, 1.2, size=n_nodes)
        external = np.minimum(external, np.maximum(cap - n - in_v + out_v, 0.0))
        exits = 0.035 * n * DT / 60.0

        n = np.clip(n + in_v + external - out_v - exits, 0.0, cap)

        inflow_ema = 0.85 * inflow_ema + 0.15 * (in_v + external)
        outflow_ema = 0.85 * outflow_ema + 0.15 * (out_v + exits)

        occupancy = n / cap
        speed = np.maximum(V_MIN, v_free * weather_v * incident * (1.0 - occupancy) ** 0.85)
        congestion = np.clip(1.0 - speed / v_free, 0.0, 1.0)
        imbalance = (inflow_ema - outflow_ema) / np.maximum(inflow_ema + outflow_ema, 1e-6)
        pressure = np.clip(0.65 * occupancy + 0.35 * (0.5 + 0.5 * imbalance), 0.0, 1.0)

        fine_out[step] = np.stack([speed, congestion, pressure], axis=1)

        if step % SUB_STEPS == SUB_STEPS - 1:
            o = step // SUB_STEPS
            node_out[o] = np.stack([speed, v_free, congestion, length, pressure], axis=1)

            hv = rng.poisson(np.clip(2.6 * occupancy[src] * lanes[src] / 3.0, 0.02, 6.0))
            ttc = np.where(edge_green, timer[ejun], timer[ejun] + 30.0 * g["edge_approach"])
            edge_out[o] = np.stack(
                [
                    edge_green & ~is_amber[ejun],
                    edge_green & is_amber[ejun],
                    ~edge_green,
                    np.clip(ttc / 60.0, 0, 3),
                    hv / 5.0,
                    (ev_on[ejun] & edge_green).astype(np.float64),
                ],
                axis=1,
            )
            glob_out[o] = [
                math.sin(2 * math.pi * hour / 24),
                math.cos(2 * math.pi * hour / 24),
                math.sin(2 * math.pi * dow / 7),
                math.cos(2 * math.pi * dow / 7),
                weekend,
                rain,
                1.0 - 0.6 * rain,
            ]

        timer -= DT

    return node_out, edge_out, glob_out, fine_out, steps_per_day


# -------------------------------------------------------------------- dataset


def build_samples(node, edge, glob, fine, obs_per_day, window):
    xs_n, xs_e, xs_g, ys, cur, interp = [], [], [], [], [], []
    days = node.shape[0] // obs_per_day
    for d in range(days):
        base = d * obs_per_day
        for t in range(window - 1, obs_per_day - 1):
            o = base + t
            xs_n.append(node[o - window + 1 : o + 1])
            xs_e.append(edge[o - window + 1 : o + 1])
            xs_g.append(glob[o - window + 1 : o + 1])
            ys.append(node[o + 1][:, [0, 2, 4]])
            cur.append(node[o][:, [0, 2, 4]])
            f = o * SUB_STEPS + SUB_STEPS - 1
            interp.append(fine[f + 1 : f + SUB_STEPS])
    return (
        np.asarray(xs_n, dtype=np.float32),
        np.asarray(xs_e, dtype=np.float32),
        np.asarray(xs_g, dtype=np.float32),
        np.asarray(ys, dtype=np.float32),
        np.asarray(cur, dtype=np.float32),
        np.asarray(interp, dtype=np.float32),
    )


# ---------------------------------------------------------------------- model


def mlp(i, h, o):
    return nn.Sequential(nn.Linear(i, h), nn.ReLU(), nn.Linear(h, o))


class MessagePassing(nn.Module):
    def __init__(self, h):
        super().__init__()
        self.msg = mlp(3 * h + h, h, h)
        self.upd = mlp(3 * h, h, h)
        self.norm = nn.LayerNorm(h)

    def forward(self, x, e, gexp, src, dst, deg):
        m = self.msg(torch.cat([x[:, src], x[:, dst], e, gexp[:, src]], dim=-1))
        agg = torch.zeros_like(x).index_add_(1, dst, m) / deg
        return self.norm(x + self.upd(torch.cat([x, agg, gexp], dim=-1)))


class PravahSTGNN(nn.Module):
    def __init__(self, f_node, f_edge, f_glob, hidden=64, layers=2, use_graph=True):
        super().__init__()
        self.use_graph = use_graph
        self.node_enc = mlp(f_node, hidden, hidden)
        self.edge_enc = mlp(f_edge, hidden, hidden)
        self.glob_enc = mlp(f_glob, hidden, hidden)
        self.mp = nn.ModuleList([MessagePassing(hidden) for _ in range(layers)])
        self.temporal = nn.GRU(hidden, hidden, batch_first=True)
        self.head = mlp(hidden + f_node, hidden, 3)

    def forward(self, xn, xe, xg, src, dst, deg):
        B, T, N, _ = xn.shape
        E = xe.shape[2]
        h = self.node_enc(xn.reshape(B * T, N, -1))
        e = self.edge_enc(xe.reshape(B * T, E, -1))
        g = self.glob_enc(xg.reshape(B * T, -1)).unsqueeze(1).expand(-1, N, -1)
        if self.use_graph:
            for layer in self.mp:
                h = layer(h, e, g, src, dst, deg)
        else:
            h = h + g
        h = h.reshape(B, T, N, -1).permute(0, 2, 1, 3).reshape(B * N, T, -1)
        _, hT = self.temporal(h)
        z = torch.cat([hT[-1], xn[:, -1].reshape(B * N, -1)], dim=-1)
        return self.head(z).reshape(B, N, 3)


# ------------------------------------------------------------------- training


def run(args):
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    g = build_graph()

    print(f"Graph: {len(g['seg_ids'])} segment nodes, {len(g['src'])} directed junction edges, "
          f"{len(g['jun_ids'])} junctions")
    print(f"Simulating {args.days} days x {args.hours}h at {DT}s resolution...")
    t0 = time.time()
    node, edge, glob, fine, steps_per_day = simulate(g, args.days, args.hours, 7.0, rng)
    obs_per_day = steps_per_day // SUB_STEPS
    print(f"  {node.shape[0]} network observations in {time.time() - t0:.1f}s "
          f"(mean congestion {node[:, :, 2].mean():.2f}, peak {node[:, :, 2].max():.2f})")

    xn, xe, xg, y, cur, interp = build_samples(node, edge, glob, fine, obs_per_day, args.window)
    n_test = int(len(xn) * 0.2)
    n_val = int(len(xn) * 0.1)
    tr = slice(0, len(xn) - n_val - n_test)
    va = slice(len(xn) - n_val - n_test, len(xn) - n_test)
    te = slice(len(xn) - n_test, len(xn))
    print(f"Samples: {tr.stop} train / {n_val} val / {n_test} test (window {args.window} min)")

    nmu, nsd = xn[tr].mean((0, 1, 2)), xn[tr].std((0, 1, 2)) + 1e-6
    emu, esd = xe[tr].mean((0, 1, 2)), xe[tr].std((0, 1, 2)) + 1e-6
    gmu, gsd = xg[tr].mean((0, 1)), xg[tr].std((0, 1)) + 1e-6
    delta = y - cur
    dsd = delta[tr].std((0, 1)) + 1e-6

    def tensors(sl):
        return (
            torch.tensor((xn[sl] - nmu) / nsd),
            torch.tensor((xe[sl] - emu) / esd),
            torch.tensor((xg[sl] - gmu) / gsd),
            torch.tensor(delta[sl] / dsd),
        )

    tr_t, va_t, te_t = tensors(tr), tensors(va), tensors(te)
    src = torch.tensor(g["src"], dtype=torch.long)
    dst = torch.tensor(g["dst"], dtype=torch.long)
    deg = torch.zeros(len(g["seg_ids"]), 1).index_add_(
        0, dst, torch.ones(len(g["src"]), 1)
    ).clamp(min=1)

    model = PravahSTGNN(xn.shape[-1], xe.shape[-1], xg.shape[-1], args.hidden,
                        args.layers, use_graph=not args.no_graph)
    params = sum(p.numel() for p in model.parameters())
    print(f"Model: {'MLP-GRU ablation (no message passing)' if args.no_graph else 'STGNN'}, "
          f"{params:,} parameters\n")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)
    lossf = nn.SmoothL1Loss(beta=0.5)
    best, best_state = float("inf"), None

    for ep in range(args.epochs):
        model.train()
        perm = torch.randperm(tr_t[0].shape[0])
        total = 0.0
        for i in range(0, len(perm), args.batch):
            b = perm[i : i + args.batch]
            pred = model(tr_t[0][b], tr_t[1][b], tr_t[2][b], src, dst, deg)
            loss = lossf(pred, tr_t[3][b])
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += loss.item() * len(b)
        sched.step()

        model.eval()
        with torch.no_grad():
            vloss = lossf(model(va_t[0], va_t[1], va_t[2], src, dst, deg), va_t[3]).item()
        if vloss < best:
            best, best_state = vloss, {k: v.clone() for k, v in model.state_dict().items()}
        print(f"  epoch {ep + 1:>3}/{args.epochs}  train {total / len(perm):.4f}  val {vloss:.4f}")

    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        pred = model(te_t[0], te_t[1], te_t[2], src, dst, deg).numpy() * dsd

    truth = y[te]
    current = cur[te]
    names = ["speed (km/h)", "congestion", "pressure"]

    gnn_mae = np.abs(current + pred - truth).mean((0, 1))
    per_mae = np.abs(current - truth).mean((0, 1))
    gnn_rmse = np.sqrt(((current + pred - truth) ** 2).mean((0, 1)))
    per_rmse = np.sqrt(((current - truth) ** 2).mean((0, 1)))

    print("\nT+60s forecast on held-out days")
    print(f"{'target':<16}{'persistence':>14}{'GNN':>10}{'skill':>9}{'RMSE gain':>11}")
    for i, nm in enumerate(names):
        skill = (per_mae[i] - gnn_mae[i]) / per_mae[i] * 100
        rgain = (per_rmse[i] - gnn_rmse[i]) / per_rmse[i] * 100
        print(f"{nm:<16}{per_mae[i]:>14.3f}{gnn_mae[i]:>10.3f}{skill:>8.1f}%{rgain:>10.1f}%")

    congested = truth[:, :, 1] > 0.5
    if congested.any():
        cg = np.abs((current + pred - truth)[:, :, 0])[congested].mean()
        cp = np.abs((current - truth)[:, :, 0])[congested].mean()
        print(f"\nCongested segments only (congestion > 0.5): speed MAE "
              f"{cp:.2f} -> {cg:.2f} km/h ({(cp - cg) / cp * 100:.1f}% better)")

    steps = np.arange(1, SUB_STEPS)
    alpha = steps / SUB_STEPS
    proj = current[:, None] + alpha[None, :, None, None] * pred[:, None]
    hold = np.repeat(current[:, None], len(steps), axis=1)
    itruth = interp[te]
    print("\nLinear projection over the intermediate 50s (vs simulator ground truth)")
    print(f"{'offset':<10}{'hold last obs':>16}{'projected':>12}{'gain':>9}")
    for k, s in enumerate(steps):
        h_mae = np.abs(hold[:, k, :, 0] - itruth[:, k, :, 0]).mean()
        p_mae = np.abs(proj[:, k, :, 0] - itruth[:, k, :, 0]).mean()
        print(f"+{s * DT:<9}s{h_mae:>16.3f}{p_mae:>12.3f}{(h_mae - p_mae) / h_mae * 100:>8.1f}%")

    ARTIFACTS.mkdir(exist_ok=True)
    tag = "ablation" if args.no_graph else "stgnn"
    torch.save(
        {
            "state_dict": model.state_dict(),
            "config": vars(args),
            "norm": {"node": (nmu, nsd), "edge": (emu, esd), "glob": (gmu, gsd), "delta": dsd},
            "graph": {k: v for k, v in g.items() if isinstance(v, (list, np.ndarray))},
        },
        ARTIFACTS / f"gnn_{tag}.pt",
    )
    (ARTIFACTS / f"gnn_{tag}_report.json").write_text(
        json.dumps(
            {
                "targets": names,
                "mae_persistence": per_mae.tolist(),
                "mae_model": gnn_mae.tolist(),
                "skill_pct": ((per_mae - gnn_mae) / per_mae * 100).tolist(),
                "params": params,
                "test_samples": int(n_test),
            },
            indent=2,
        )
    )
    print(f"\nSaved {ARTIFACTS / f'gnn_{tag}.pt'}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=24)
    p.add_argument("--hours", type=int, default=8)
    p.add_argument("--window", type=int, default=6)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--layers", type=int, default=2)
    p.add_argument("--epochs", type=int, default=25)
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--lr", type=float, default=2e-3)
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--no-graph", action="store_true")
    run(p.parse_args())


if __name__ == "__main__":
    main()
