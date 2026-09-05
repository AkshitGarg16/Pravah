"""
PRAVAH — network-wide signal control with multi-agent PPO.

One shared graph policy controls every junction at once: it reads the graph
state (segment nodes, junction edges, global context) and emits, per junction,
which approach gets priority and how much green it gets for the next minute.
Junction heads share weights, so the joint action space stays linear in the
number of junctions instead of exponential.

Three interchangeable state-transition backends:
  sim   built-in signalised micro-simulator (default, no dependencies)
  gnn   the trained T+60s STGNN used as a learned state-action function
  sumo  SUMO/TraCI, wired through a segment/junction mapping file

    python backend/train_signal_rl.py
    python backend/train_signal_rl.py --backend gnn --world-model backend/artifacts/gnn_stgnn.pt
    python backend/train_signal_rl.py --backend sumo --sumo-cfg sumo/pravah.sumocfg --map sumo/mapping.json
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from train_gnn_forecast import (
    ADAPTIVE,
    DT,
    JUNCTIONS,
    K_JAM,
    SAT_FLOW,
    SIGNALISED,
    SUB_STEPS,
    V_MIN,
    MessagePassing,
    PravahSTGNN,
    build_graph,
    demand_profile,
    mlp,
)

ARTIFACTS = Path(__file__).parent / "artifacts"

MAX_APPROACHES = 5
GREEN_SPLITS = [20.0, 30.0, 42.0, 55.0]
AMBER_LOST = 4.0
EPISODE_MINUTES = 60
START_HOUR = 8.0


# ------------------------------------------------------------------- backends


class GraphState:
    """Everything the policy sees at one decision point."""

    def __init__(self, node, edge, glob, occupancy, served, info):
        self.node = node
        self.edge = edge
        self.glob = glob
        self.occupancy = occupancy
        self.served = served
        self.info = info


class SimBackend:
    """Signalised micro-simulator stepped in 60s macro-steps of DT-second ticks."""

    def __init__(self, g, seed=0):
        self.g = g
        self.rng = np.random.default_rng(seed)
        self.n_nodes = len(g["seg_ids"])
        self.n_edges = len(g["src"])
        self.n_jun = len(g["jun_ids"])
        self.members = g["jun_members"]
        self.n_members = np.array([len(m) for m in self.members])
        self.turn = self.rng.uniform(0.4, 1.0, size=self.n_edges)
        self.gen_weight = self.rng.uniform(0.6, 1.4, size=self.n_nodes) * g["lanes"] * g["length"]
        self.installed_signal = np.array([j in SIGNALISED for j in g["jun_ids"]])
        self.meters = np.zeros(self.n_nodes)

    def install_signal(self, j):
        self.installed_signal[j] = True

    def install_meter(self, i):
        self.meters[i] = 1.0

    def reset(self, seed=None):
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        g = self.g
        self.n = g["capacity"] * self.rng.uniform(0.10, 0.22, size=self.n_nodes)
        self.minute = 0
        self.dow = int(self.rng.integers(0, 7))
        self.rain = self.rng.uniform(0.3, 0.9) if self.rng.random() < 0.25 else 0.0
        self.incident = np.ones(self.n_nodes)
        self.incident_left = np.zeros(self.n_nodes)
        self.prev_served = np.zeros(self.n_jun, dtype=int)
        self.hv = self.rng.poisson(1.0, size=self.n_edges).astype(np.float64)
        self.ev = np.zeros(self.n_jun)
        self.ev_approach = np.zeros(self.n_jun, dtype=int)
        self.inflow_ema = np.zeros(self.n_nodes)
        self.outflow_ema = np.zeros(self.n_nodes)
        return self.observe(np.zeros(self.n_jun, dtype=int), np.zeros(self.n_jun, dtype=int))

    def _schedule(self, phase, split):
        """Per-junction approach served at each inner tick of the coming minute."""
        sched = np.zeros((self.n_jun, SUB_STEPS), dtype=int)
        for j in range(self.n_jun):
            k = self.n_members[j]
            chosen = int(phase[j]) % k
            green_ticks = int(round(GREEN_SPLITS[int(split[j])] / DT))
            green_ticks = max(1, min(SUB_STEPS, green_ticks))
            others = [a for a in range(k) if a != chosen]
            seq = [chosen] * green_ticks
            for t in range(SUB_STEPS - green_ticks):
                seq.append(others[t % len(others)] if others else chosen)
            sched[j] = seq[:SUB_STEPS]
            if self.ev[j] > 0:
                sched[j] = self.ev_approach[j]
        return sched

    def step(self, phase, split):
        g = self.g
        src, dst, ejun = g["src"], g["dst"], g["edge_jun"]
        cap, lanes = g["capacity"], g["lanes"]
        sched = self._schedule(phase, split)

        hour = START_HOUR + self.minute / 60.0
        weekend = 1.0 if self.dow >= 5 else 0.0
        weather_v = 1.0 - 0.28 * self.rain
        weather_s = 1.0 - 0.18 * self.rain
        demand = demand_profile(hour) * (0.72 if weekend else 1.0)

        self.incident_left = np.maximum(self.incident_left - 60, 0)
        self.incident = np.where(self.incident_left > 0, self.incident, 1.0)
        if self.rng.random() < 0.01:
            i = int(self.rng.integers(self.n_nodes))
            self.incident[i] = self.rng.uniform(0.35, 0.6)
            self.incident_left[i] = self.rng.uniform(300, 900)

        self.ev = np.maximum(self.ev - 60, 0)
        new_ev = (self.rng.random(self.n_jun) < 0.02) & (self.ev <= 0)
        if new_ev.any():
            self.ev_approach = np.where(
                new_ev, self.rng.integers(0, MAX_APPROACHES, self.n_jun) % self.n_members, self.ev_approach
            )
            self.ev = np.where(new_ev, self.rng.uniform(60, 120, self.n_jun), self.ev)

        served_total = 0.0
        ev_served = 0.0
        for tick in range(SUB_STEPS):
            served_idx = sched[:, tick]
            served_seg = np.array(
                [self.members[j][served_idx[j] % self.n_members[j]] for j in range(self.n_jun)]
            )
            edge_green = src == served_seg[ejun]
            controlled = self.installed_signal[ejun]
            phase_factor = np.where(edge_green, 1.0, np.where(controlled, 0.04, 0.30))

            hv_penalty = 1.0 / (1.0 + 0.09 * self.hv)
            sat = SAT_FLOW * lanes[src] * DT / 3600.0
            desired = sat * phase_factor * hv_penalty * weather_s * self.incident[src] * self.turn
            desired = desired * (1.0 - 0.45 * self.meters[src] * (self.n[dst] / cap[dst]))
            desired = np.minimum(desired, np.maximum(self.n[src], 0.0) * 0.9)

            want_out = np.zeros(self.n_nodes)
            np.add.at(want_out, src, desired)
            scale_out = np.where(want_out > 1e-9, np.minimum(1.0, self.n / np.maximum(want_out, 1e-9)), 1.0)
            flow = desired * scale_out[src]

            space = np.maximum(cap - self.n, 0.0)
            want_in = np.zeros(self.n_nodes)
            np.add.at(want_in, dst, flow)
            scale_in = np.where(want_in > 1e-9, np.minimum(1.0, space / np.maximum(want_in, 1e-9)), 1.0)
            flow = flow * scale_in[dst]

            out_v = np.zeros(self.n_nodes)
            in_v = np.zeros(self.n_nodes)
            np.add.at(out_v, src, flow)
            np.add.at(in_v, dst, flow)

            external = self.gen_weight * demand * DT / 60.0 * self.rng.uniform(0.85, 1.15, self.n_nodes)
            external = np.minimum(external, np.maximum(cap - self.n - in_v + out_v, 0.0))
            exits = 0.035 * self.n * DT / 60.0

            self.n = np.clip(self.n + in_v + external - out_v - exits, 0.0, cap)
            self.inflow_ema = 0.85 * self.inflow_ema + 0.15 * (in_v + external)
            self.outflow_ema = 0.85 * self.outflow_ema + 0.15 * (out_v + exits)
            served_total += float(out_v.sum())
            ev_mask = (self.ev > 0) & (served_idx == self.ev_approach)
            ev_served += float(ev_mask.sum())

        switches = float((phase != self.prev_served).sum())
        self.prev_served = np.asarray(phase, dtype=int).copy()
        self.hv = self.rng.poisson(np.clip(2.6 * (self.n / cap)[src] * lanes[src] / 3.0, 0.02, 6.0))
        self.minute += 1

        obs = self.observe(phase, split)
        info = {
            "throughput": served_total,
            "switches": switches,
            "ev_served": ev_served,
            "ev_pending": float((self.ev > 0).sum()),
            "mean_congestion": float(obs.node[:, 2].mean()),
            "mean_speed": float(obs.node[:, 0].mean()),
        }
        return obs, info, self.minute >= EPISODE_MINUTES

    def observe(self, phase, split):
        g = self.g
        cap = g["capacity"]
        occupancy = self.n / cap
        weather_v = 1.0 - 0.28 * self.rain
        speed = np.maximum(V_MIN, g["v_free"] * weather_v * self.incident * (1.0 - occupancy) ** 0.85)
        congestion = np.clip(1.0 - speed / g["v_free"], 0.0, 1.0)
        imbalance = (self.inflow_ema - self.outflow_ema) / np.maximum(
            self.inflow_ema + self.outflow_ema, 1e-6
        )
        pressure = np.clip(0.65 * occupancy + 0.35 * (0.5 + 0.5 * imbalance), 0.0, 1.0)

        node = np.stack(
            [speed, g["v_free"], congestion, g["length"], pressure, occupancy, g["lanes"] / 6.0], axis=1
        ).astype(np.float32)

        src, ejun = g["src"], g["edge_jun"]
        served_seg = np.array(
            [self.members[j][int(phase[j]) % self.n_members[j]] for j in range(self.n_jun)]
        )
        edge_green = (src == served_seg[ejun]).astype(np.float32)
        edge = np.stack(
            [
                edge_green,
                1.0 - edge_green,
                np.clip(self.hv / 5.0, 0, 2),
                (self.ev[ejun] > 0).astype(np.float32),
                self.installed_signal[ejun].astype(np.float32),
                np.array([GREEN_SPLITS[int(split[j])] / 60.0 for j in ejun], dtype=np.float32),
            ],
            axis=1,
        ).astype(np.float32)

        hour = START_HOUR + self.minute / 60.0
        glob = np.array(
            [
                math.sin(2 * math.pi * hour / 24),
                math.cos(2 * math.pi * hour / 24),
                math.sin(2 * math.pi * self.dow / 7),
                math.cos(2 * math.pi * self.dow / 7),
                1.0 if self.dow >= 5 else 0.0,
                self.rain,
                1.0 - 0.6 * self.rain,
            ],
            dtype=np.float32,
        )
        return GraphState(node, edge, glob, occupancy, np.asarray(phase, dtype=int), {})


class GNNWorldModel(SimBackend):
    """Uses the trained T+60s STGNN as the state-action function.

    Physics still resolves the queues; the STGNN supplies the observable node
    state (speed / congestion / pressure) one minute ahead, so the controller is
    trained against the same forecaster the dashboard runs on.
    """

    def __init__(self, g, checkpoint, window=6, seed=0):
        super().__init__(g, seed)
        ckpt = torch.load(checkpoint, map_location="cpu", weights_only=False)
        cfg = ckpt["config"]
        self.window = window
        self.norm = ckpt["norm"]
        self.model = PravahSTGNN(5, 6, 7, cfg["hidden"], cfg["layers"], use_graph=not cfg["no_graph"])
        self.model.load_state_dict(ckpt["state_dict"])
        self.model.eval()
        self.src = torch.tensor(g["src"], dtype=torch.long)
        self.dst = torch.tensor(g["dst"], dtype=torch.long)
        self.deg = (
            torch.zeros(self.n_nodes, 1)
            .index_add_(0, self.dst, torch.ones(self.n_edges, 1))
            .clamp(min=1)
        )
        self.history = []

    def _forecast(self, obs):
        node5 = obs.node[:, :5]
        edge6 = obs.edge[:, :6]
        self.history.append((node5, edge6, obs.glob))
        self.history = self.history[-self.window :]
        if len(self.history) < self.window:
            return obs
        nmu, nsd = self.norm["node"]
        emu, esd = self.norm["edge"]
        gmu, gsd = self.norm["glob"]
        xn = torch.tensor(np.stack([(h[0] - nmu) / nsd for h in self.history])[None], dtype=torch.float32)
        xe = torch.tensor(np.stack([(h[1] - emu) / esd for h in self.history])[None], dtype=torch.float32)
        xg = torch.tensor(np.stack([(h[2] - gmu) / gsd for h in self.history])[None], dtype=torch.float32)
        with torch.no_grad():
            delta = self.model(xn, xe, xg, self.src, self.dst, self.deg).numpy()[0] * self.norm["delta"]
        node = obs.node.copy()
        node[:, 0] = np.maximum(V_MIN, node[:, 0] + delta[:, 0])
        node[:, 2] = np.clip(node[:, 2] + delta[:, 1], 0.0, 1.0)
        node[:, 4] = np.clip(node[:, 4] + delta[:, 2], 0.0, 1.0)
        obs.node = node
        return obs

    def reset(self, seed=None):
        self.history = []
        return self._forecast(super().reset(seed))

    def step(self, phase, split):
        obs, info, done = super().step(phase, split)
        obs = self._forecast(obs)
        info["mean_congestion"] = float(obs.node[:, 2].mean())
        info["mean_speed"] = float(obs.node[:, 0].mean())
        return obs, info, done


class SumoBackend:
    """TraCI backend. Expects SUMO installed and a mapping file:

        {"segments": {"SG-201": ["edge_id_a", "edge_id_b"], ...},
         "junctions": {"J-ITO": "tls_id", ...}}
    """

    def __init__(self, g, sumo_cfg, mapping_path, gui=False, seed=0):
        try:
            import traci
        except ImportError as exc:
            raise SystemExit(
                "SUMO python tools not found. Install SUMO and put $SUMO_HOME/tools on PYTHONPATH."
            ) from exc
        self.traci = traci
        self.g = g
        self.cfg = sumo_cfg
        self.gui = gui
        self.seed = seed
        self.map = json.loads(Path(mapping_path).read_text())
        self.seg_edges = [self.map["segments"][s] for s in g["seg_ids"]]
        self.tls = [self.map["junctions"].get(j) for j in g["jun_ids"]]
        self.n_nodes = len(g["seg_ids"])
        self.n_edges = len(g["src"])
        self.n_jun = len(g["jun_ids"])
        self.members = g["jun_members"]
        self.n_members = np.array([len(m) for m in self.members])
        self.minute = 0
        self.prev_served = np.zeros(self.n_jun, dtype=int)
        self.started = False

    def reset(self, seed=None):
        if self.started:
            self.traci.close()
        binary = "sumo-gui" if self.gui else "sumo"
        self.traci.start(
            [binary, "-c", self.cfg, "--step-length", str(DT), "--no-warnings", "true",
             "--seed", str(seed if seed is not None else self.seed)]
        )
        self.started = True
        self.minute = 0
        self.prev_served = np.zeros(self.n_jun, dtype=int)
        return self.observe(np.zeros(self.n_jun, dtype=int), np.zeros(self.n_jun, dtype=int))

    def _apply(self, j, approach):
        """Pick the signal program phase whose green movements come from `approach`."""
        tls = self.tls[j]
        if tls is None:
            return
        seg = self.g["seg_ids"][self.members[j][approach % self.n_members[j]]]
        want = set(self.map["segments"][seg])
        links = self.traci.trafficlight.getControlledLinks(tls)
        logic = self.traci.trafficlight.getAllProgramLogics(tls)[0]
        best, best_hits = 0, -1
        for idx, ph in enumerate(logic.phases):
            hits = sum(
                1
                for k, link in enumerate(links)
                if link and link[0][0].rsplit("_", 1)[0] in want and k < len(ph.state)
                and ph.state[k] in "Gg"
            )
            if hits > best_hits:
                best, best_hits = idx, hits
        self.traci.trafficlight.setPhase(tls, best)

    def step(self, phase, split):
        for j in range(self.n_jun):
            self._apply(j, int(phase[j]))
        green_ticks = [max(1, int(round(GREEN_SPLITS[int(s)] / DT))) for s in split]
        departed = 0
        for tick in range(SUB_STEPS):
            for j in range(self.n_jun):
                if tick == green_ticks[j]:
                    self._apply(j, (int(phase[j]) + 1) % self.n_members[j])
            self.traci.simulationStep()
            departed += self.traci.simulation.getArrivedNumber()
        self.minute += 1
        switches = float((phase != self.prev_served).sum())
        self.prev_served = np.asarray(phase, dtype=int).copy()
        obs = self.observe(phase, split)
        info = {
            "throughput": float(departed),
            "switches": switches,
            "ev_served": 0.0,
            "ev_pending": 0.0,
            "mean_congestion": float(obs.node[:, 2].mean()),
            "mean_speed": float(obs.node[:, 0].mean()),
        }
        return obs, info, self.minute >= EPISODE_MINUTES

    def observe(self, phase, split):
        g = self.g
        speed = np.zeros(self.n_nodes)
        occupancy = np.zeros(self.n_nodes)
        halting = np.zeros(self.n_nodes)
        for i, edges in enumerate(self.seg_edges):
            spd = [self.traci.edge.getLastStepMeanSpeed(e) for e in edges]
            occ = [self.traci.edge.getLastStepOccupancy(e) for e in edges]
            hlt = [self.traci.edge.getLastStepHaltingNumber(e) for e in edges]
            speed[i] = max(V_MIN / 3.6, float(np.mean(spd))) * 3.6
            occupancy[i] = float(np.mean(occ))
            halting[i] = float(np.sum(hlt))
        congestion = np.clip(1.0 - speed / g["v_free"], 0.0, 1.0)
        pressure = np.clip(0.65 * occupancy + 0.35 * halting / (halting.max() + 1e-6), 0.0, 1.0)
        node = np.stack(
            [speed, g["v_free"], congestion, g["length"], pressure, occupancy, g["lanes"] / 6.0], axis=1
        ).astype(np.float32)

        src, ejun = g["src"], g["edge_jun"]
        served_seg = np.array(
            [self.members[j][int(phase[j]) % self.n_members[j]] for j in range(self.n_jun)]
        )
        edge_green = (src == served_seg[ejun]).astype(np.float32)
        edge = np.stack(
            [
                edge_green,
                1.0 - edge_green,
                np.zeros(self.n_edges, dtype=np.float32),
                np.zeros(self.n_edges, dtype=np.float32),
                np.array([self.tls[j] is not None for j in ejun], dtype=np.float32),
                np.array([GREEN_SPLITS[int(split[j])] / 60.0 for j in ejun], dtype=np.float32),
            ],
            axis=1,
        ).astype(np.float32)
        hour = START_HOUR + self.minute / 60.0
        glob = np.array(
            [math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24), 0.0, 1.0, 0.0, 0.0, 1.0],
            dtype=np.float32,
        )
        return GraphState(node, edge, glob, occupancy, np.asarray(phase, dtype=int), {})


def make_backend(args, g, seed):
    if args.backend == "gnn":
        return GNNWorldModel(g, args.world_model, seed=seed)
    if args.backend == "sumo":
        return SumoBackend(g, args.sumo_cfg, args.map, gui=args.gui, seed=seed)
    return SimBackend(g, seed=seed)


# ---------------------------------------------------------------------- reward


def reward(info, obs, w_switch=0.12):
    return (
        info["throughput"] / 400.0
        - 2.4 * info["mean_congestion"]
        - 0.9 * float(np.percentile(obs.node[:, 2], 90))
        - w_switch * info["switches"] / max(len(obs.served), 1)
        + 0.25 * info["ev_served"]
    )


# ---------------------------------------------------------------------- policy


class GraphActorCritic(nn.Module):
    def __init__(self, f_node, f_edge, f_glob, n_jun, hidden=64, layers=2):
        super().__init__()
        self.node_enc = mlp(f_node, hidden, hidden)
        self.edge_enc = mlp(f_edge, hidden, hidden)
        self.glob_enc = mlp(f_glob, hidden, hidden)
        self.mp = nn.ModuleList([MessagePassing(hidden) for _ in range(layers)])
        self.jun_emb = nn.Embedding(n_jun, hidden)
        self.phase_head = mlp(2 * hidden, hidden, MAX_APPROACHES)
        self.split_head = mlp(2 * hidden, hidden, len(GREEN_SPLITS))
        self.critic = mlp(2 * hidden, hidden, 1)

    def encode(self, node, edge, glob, src, dst, deg, jun_index, jun_mask):
        h = self.node_enc(node)
        e = self.edge_enc(edge)
        g = self.glob_enc(glob).unsqueeze(1).expand(-1, h.shape[1], -1)
        for layer in self.mp:
            h = layer(h, e, g, src, dst, deg)
        members = h[:, jun_index] * jun_mask.unsqueeze(-1)
        jh = members.sum(2) / jun_mask.sum(-1, keepdim=True).clamp(min=1)
        jh = torch.cat([jh + self.jun_emb.weight.unsqueeze(0), g[:, : jh.shape[1]]], dim=-1)
        pooled = torch.cat([h.mean(1), g[:, 0]], dim=-1)
        return jh, pooled

    def forward(self, node, edge, glob, src, dst, deg, jun_index, jun_mask, approach_mask):
        jh, pooled = self.encode(node, edge, glob, src, dst, deg, jun_index, jun_mask)
        phase_logits = self.phase_head(jh).masked_fill(~approach_mask, -1e9)
        return phase_logits, self.split_head(jh), self.critic(pooled).squeeze(-1)


# -------------------------------------------------------------------- rollouts


class Runner:
    def __init__(self, g, args):
        self.g = g
        self.args = args
        self.n_jun = len(g["jun_ids"])
        self.src = torch.tensor(g["src"], dtype=torch.long)
        self.dst = torch.tensor(g["dst"], dtype=torch.long)
        self.deg = (
            torch.zeros(len(g["seg_ids"]), 1)
            .index_add_(0, self.dst, torch.ones(len(g["src"]), 1))
            .clamp(min=1)
        )
        idx = np.zeros((self.n_jun, MAX_APPROACHES), dtype=np.int64)
        msk = np.zeros((self.n_jun, MAX_APPROACHES), dtype=np.float32)
        for j, m in enumerate(g["jun_members"]):
            idx[j, : len(m)] = m
            msk[j, : len(m)] = 1.0
        self.jun_index = torch.tensor(idx)
        self.jun_mask = torch.tensor(msk)
        self.approach_mask = self.jun_mask.bool().unsqueeze(0)

    def tensors(self, obs):
        return (
            torch.tensor(obs.node).unsqueeze(0),
            torch.tensor(obs.edge).unsqueeze(0),
            torch.tensor(obs.glob).unsqueeze(0),
        )

    def policy_action(self, model, obs, greedy=False):
        n, e, gl = self.tensors(obs)
        pl, sl, v = model(n, e, gl, self.src, self.dst, self.deg,
                          self.jun_index, self.jun_mask, self.approach_mask)
        pd, sd = torch.distributions.Categorical(logits=pl[0]), torch.distributions.Categorical(logits=sl[0])
        if greedy:
            pa, sa = pl[0].argmax(-1), sl[0].argmax(-1)
        else:
            pa, sa = pd.sample(), sd.sample()
        logp = pd.log_prob(pa).sum() + sd.log_prob(sa).sum()
        ent = pd.entropy().sum() + sd.entropy().sum()
        return pa.numpy(), sa.numpy(), logp, ent, v[0]

    def rollout(self, model, env, seed):
        obs = env.reset(seed)
        buf = {k: [] for k in ["node", "edge", "glob", "phase", "split", "logp", "value", "reward"]}
        stats = []
        done = False
        while not done:
            pa, sa, logp, _, v = self.policy_action(model, obs)
            buf["node"].append(obs.node)
            buf["edge"].append(obs.edge)
            buf["glob"].append(obs.glob)
            nxt, info, done = env.step(pa, sa)
            buf["phase"].append(pa)
            buf["split"].append(sa)
            buf["logp"].append(logp.detach())
            buf["value"].append(v.detach())
            buf["reward"].append(reward(info, nxt))
            stats.append(info)
            obs = nxt
        return buf, stats


def fixed_time(runner, env, seed):
    obs = env.reset(seed)
    stats, done, t = [], False, 0
    while not done:
        phase = np.array([t % len(m) for m in env.g["jun_members"]])
        obs, info, done = env.step(phase, np.full(runner.n_jun, 1))
        stats.append(info)
        t += 1
    return stats


def max_pressure(runner, env, seed):
    obs = env.reset(seed)
    stats, done = [], False
    while not done:
        phase = np.zeros(runner.n_jun, dtype=int)
        for j, m in enumerate(env.g["jun_members"]):
            phase[j] = int(np.argmax(obs.node[m, 4]))
        obs, info, done = env.step(phase, np.full(runner.n_jun, 2))
        stats.append(info)
    return stats


def summarise(stats):
    return {
        "mean_congestion": float(np.mean([s["mean_congestion"] for s in stats])),
        "mean_speed": float(np.mean([s["mean_speed"] for s in stats])),
        "throughput": float(np.sum([s["throughput"] for s in stats])),
        "switch_rate": float(np.mean([s["switches"] for s in stats])),
    }


# -------------------------------------------------------------------- training


def train(args):
    torch.manual_seed(args.seed)
    g = build_graph()
    runner = Runner(g, args)
    env = make_backend(args, g, args.seed)

    probe = env.reset(args.seed)
    model = GraphActorCritic(
        probe.node.shape[1], probe.edge.shape[1], probe.glob.shape[0], runner.n_jun, args.hidden, args.layers
    )
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-5)
    print(f"Backend: {args.backend} | junctions: {runner.n_jun} | "
          f"joint action space: {np.prod([len(m) for m in g['jun_members']]) * len(GREEN_SPLITS) ** runner.n_jun:.3g}")
    print(f"Policy: shared graph encoder + per-junction heads, "
          f"{sum(p.numel() for p in model.parameters()):,} parameters\n")

    for it in range(args.iters):
        buffers, all_stats = [], []
        for ep in range(args.episodes):
            buf, stats = runner.rollout(model, env, seed=args.seed + it * 100 + ep)
            buffers.append(buf)
            all_stats += stats

        obs_n = torch.tensor(np.concatenate([np.stack(b["node"]) for b in buffers]))
        obs_e = torch.tensor(np.concatenate([np.stack(b["edge"]) for b in buffers]))
        obs_g = torch.tensor(np.concatenate([np.stack(b["glob"]) for b in buffers]))
        act_p = torch.tensor(np.concatenate([np.stack(b["phase"]) for b in buffers]))
        act_s = torch.tensor(np.concatenate([np.stack(b["split"]) for b in buffers]))
        old_logp = torch.stack([x for b in buffers for x in b["logp"]])

        advs, rets = [], []
        for b in buffers:
            values = torch.stack(b["value"]).numpy()
            rew = np.array(b["reward"], dtype=np.float32)
            adv, gae = np.zeros_like(rew), 0.0
            for t in reversed(range(len(rew))):
                nxt = values[t + 1] if t + 1 < len(rew) else 0.0
                delta = rew[t] + args.gamma * nxt - values[t]
                gae = delta + args.gamma * args.lam * gae
                adv[t] = gae
            advs.append(adv)
            rets.append(adv + values)
        adv = torch.tensor(np.concatenate(advs))
        ret = torch.tensor(np.concatenate(rets))
        adv = (adv - adv.mean()) / (adv.std() + 1e-6)

        mask = runner.approach_mask.expand(obs_n.shape[0], -1, -1)
        for _ in range(args.ppo_epochs):
            pl, sl, v = model(obs_n, obs_e, obs_g, runner.src, runner.dst, runner.deg,
                              runner.jun_index, runner.jun_mask, mask)
            pdist = torch.distributions.Categorical(logits=pl)
            sdist = torch.distributions.Categorical(logits=sl)
            logp = pdist.log_prob(act_p).sum(-1) + sdist.log_prob(act_s).sum(-1)
            ratio = torch.exp(logp - old_logp)
            pg = -torch.min(ratio * adv, ratio.clamp(1 - args.clip, 1 + args.clip) * adv).mean()
            vloss = F.mse_loss(v, ret)
            ent = (pdist.entropy().sum(-1) + sdist.entropy().sum(-1)).mean()
            loss = pg + 0.5 * vloss - args.ent_coef * ent
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 0.5)
            opt.step()

        s = summarise(all_stats)
        mean_r = float(np.mean([np.mean(b["reward"]) for b in buffers]))
        print(f"  iter {it + 1:>3}/{args.iters}  reward {mean_r:>7.3f}  "
              f"congestion {s['mean_congestion']:.3f}  speed {s['mean_speed']:.1f} km/h  "
              f"switches/min {s['switch_rate']:.2f}")

    print("\nHeld-out evaluation (unseen demand seeds)")
    results = {}
    eval_seeds = [9000 + i for i in range(args.eval_episodes)]
    for name, fn in [
        ("fixed-time", lambda s: fixed_time(runner, env, s)),
        ("max-pressure", lambda s: max_pressure(runner, env, s)),
        ("pravah-ppo", lambda s: runner.rollout(model, env, s)[1]),
    ]:
        runs = [summarise(fn(s)) for s in eval_seeds]
        results[name] = {k: float(np.mean([r[k] for r in runs])) for k in runs[0]}

    base = results["fixed-time"]
    print(f"{'policy':<15}{'congestion':>12}{'speed':>10}{'throughput':>13}{'vs fixed':>11}")
    for name, m in results.items():
        gain = (base["mean_congestion"] - m["mean_congestion"]) / base["mean_congestion"] * 100
        print(f"{name:<15}{m['mean_congestion']:>12.3f}{m['mean_speed']:>9.1f}k"
              f"{m['throughput']:>13.0f}{gain:>10.1f}%")

    ARTIFACTS.mkdir(exist_ok=True)
    torch.save(
        {"state_dict": model.state_dict(), "config": vars(args),
         "dims": [probe.node.shape[1], probe.edge.shape[1], probe.glob.shape[0], runner.n_jun]},
        ARTIFACTS / "rl_controller.pt",
    )
    (ARTIFACTS / "rl_controller_report.json").write_text(json.dumps(results, indent=2))
    print(f"\nSaved {ARTIFACTS / 'rl_controller.pt'}")
    return model, runner, env


def build_parser():
    p = argparse.ArgumentParser()
    p.add_argument("--backend", choices=["sim", "gnn", "sumo"], default="sim")
    p.add_argument("--world-model", default=str(ARTIFACTS / "gnn_stgnn.pt"))
    p.add_argument("--sumo-cfg", default=None)
    p.add_argument("--map", default=None)
    p.add_argument("--gui", action="store_true")
    p.add_argument("--iters", type=int, default=40)
    p.add_argument("--episodes", type=int, default=4)
    p.add_argument("--eval-episodes", type=int, default=6)
    p.add_argument("--ppo-epochs", type=int, default=4)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--layers", type=int, default=2)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--gamma", type=float, default=0.97)
    p.add_argument("--lam", type=float, default=0.95)
    p.add_argument("--clip", type=float, default=0.2)
    p.add_argument("--ent-coef", type=float, default=0.01)
    p.add_argument("--seed", type=int, default=21)
    return p


if __name__ == "__main__":
    train(build_parser().parse_args())
