"""
PRAVAH — infrastructure placement agent.

Builds on train_signal_rl.py. The controller there actuates the lights that
exist; this agent decides where lights should exist in the first place, then
hands the resulting network to that controller and is scored on what it does.

Outer policy: a graph encoder reads the network, then autoregressively picks
candidate sites (signalise an unsignalised junction, or install a PRAVAH
mid-road meter on a segment) under a capex budget, with a STOP action.
Trained with REINFORCE + moving-average baseline; reward is the congestion
reduction the inner controller achieves on the rebuilt network, net of capex.

    python backend/train_infra_agent.py
    python backend/train_infra_agent.py --backend sumo --sumo-cfg sumo/pravah.sumocfg --map sumo/mapping.json
    python backend/train_infra_agent.py --controller backend/artifacts/rl_controller.pt --budget 80
"""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from train_gnn_forecast import JUNCTIONS, SEGMENTS, SIGNALISED, MessagePassing, build_graph, mlp
from train_signal_rl import (
    GREEN_SPLITS,
    MAX_APPROACHES,
    GraphActorCritic,
    Runner,
    make_backend,
    max_pressure,
    summarise,
)

ARTIFACTS = Path(__file__).parent / "artifacts"

KINDS = ["new-signal", "pravah-midroad"]


def build_candidates(g):
    """Every buildable intervention on this network, with an indicative capex."""
    sites = []
    for j, jid in enumerate(g["jun_ids"]):
        if jid in SIGNALISED:
            continue
        members = g["jun_members"][j]
        lanes = float(g["lanes"][members].sum())
        sites.append(
            {
                "id": f"BLD-{len(sites) + 1:02d}",
                "kind": "new-signal",
                "name": f"Signalise {jid}",
                "junction": j,
                "segments": members,
                "cost": round(14.0 + 2.6 * lanes, 1),
            }
        )
    for i, sid in enumerate(g["seg_ids"]):
        sites.append(
            {
                "id": f"BLD-{len(sites) + 1:02d}",
                "kind": "pravah-midroad",
                "name": f"Mid-road unit on {sid} ({SEGMENTS[i][1]})",
                "junction": -1,
                "segments": [i],
                "cost": round(11.0 + 3.2 * float(g["lanes"][i]) + 4.0 * float(g["length"][i]), 1),
            }
        )
    return sites


class PlacementPolicy(nn.Module):
    def __init__(self, f_node, f_glob, n_sites, hidden=64, layers=2, f_edge=6):
        super().__init__()
        self.node_enc = mlp(f_node, hidden, hidden)
        self.edge_enc = mlp(f_edge, hidden, hidden)
        self.glob_enc = mlp(f_glob, hidden, hidden)
        self.mp = nn.ModuleList([MessagePassing(hidden) for _ in range(layers)])
        self.site_enc = mlp(hidden + len(KINDS) + 2, hidden, hidden)
        self.score = mlp(2 * hidden, hidden, 1)
        self.stop = mlp(hidden, hidden, 1)
        self.site_emb = nn.Embedding(n_sites, hidden)

    def forward(self, node, edge, glob, src, dst, deg, site_nodes, site_mask, site_static):
        h = self.node_enc(node)
        e = self.edge_enc(edge)
        g = self.glob_enc(glob).unsqueeze(0).expand(h.shape[0], -1)
        for layer in self.mp:
            h = layer(h.unsqueeze(0), e.unsqueeze(0), g.unsqueeze(0), src, dst, deg).squeeze(0)
        pooled = h.mean(0)
        members = h[site_nodes] * site_mask.unsqueeze(-1)
        site_h = members.sum(1) / site_mask.sum(-1, keepdim=True).clamp(min=1)
        site_h = self.site_enc(torch.cat([site_h, site_static], dim=-1)) + self.site_emb.weight
        logits = self.score(torch.cat([site_h, pooled.unsqueeze(0).expand_as(site_h)], dim=-1)).squeeze(-1)
        return logits, self.stop(pooled).squeeze(-1)


class Planner:
    def __init__(self, g, sites, args):
        self.g = g
        self.sites = sites
        self.args = args
        max_seg = max(len(s["segments"]) for s in sites)
        idx = np.zeros((len(sites), max_seg), dtype=np.int64)
        msk = np.zeros((len(sites), max_seg), dtype=np.float32)
        static = np.zeros((len(sites), len(KINDS) + 2), dtype=np.float32)
        for k, s in enumerate(sites):
            idx[k, : len(s["segments"])] = s["segments"]
            msk[k, : len(s["segments"])] = 1.0
            static[k, KINDS.index(s["kind"])] = 1.0
            static[k, len(KINDS)] = s["cost"] / 40.0
            static[k, len(KINDS) + 1] = len(s["segments"]) / MAX_APPROACHES
        self.site_nodes = torch.tensor(idx)
        self.site_mask = torch.tensor(msk)
        self.site_static = torch.tensor(static)

    def sample_plan(self, policy, obs, runner, greedy=False):
        chosen, logps, ents = [], [], []
        spent = 0.0
        node = torch.tensor(obs.node)
        edge = torch.tensor(obs.edge)
        glob = torch.tensor(obs.glob)
        taken = torch.zeros(len(self.sites), dtype=torch.bool)

        for _ in range(self.args.max_sites):
            logits, stop = policy(node, edge, glob, runner.src, runner.dst, runner.deg,
                                  self.site_nodes, self.site_mask, self.site_static)
            afford = torch.tensor(
                [spent + s["cost"] <= self.args.budget for s in self.sites], dtype=torch.bool
            )
            valid = afford & ~taken
            if not valid.any():
                break
            all_logits = torch.cat([logits.masked_fill(~valid, -1e9), stop.reshape(1)])
            dist = torch.distributions.Categorical(logits=all_logits)
            a = int(all_logits.argmax()) if greedy else int(dist.sample())
            logps.append(dist.log_prob(torch.tensor(a)))
            ents.append(dist.entropy())
            if a == len(self.sites):
                break
            taken[a] = True
            chosen.append(a)
            spent += self.sites[a]["cost"]

        logp = torch.stack(logps).sum() if logps else torch.zeros(())
        ent = torch.stack(ents).sum() if ents else torch.zeros(())
        return chosen, spent, logp, ent


def load_controller(path, probe, runner):
    if path and Path(path).exists():
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        f_node, f_edge, f_glob, n_jun = ckpt["dims"]
        model = GraphActorCritic(f_node, f_edge, f_glob, n_jun,
                                 ckpt["config"]["hidden"], ckpt["config"]["layers"])
        model.load_state_dict(ckpt["state_dict"])
        model.eval()
        print(f"Inner controller: loaded {path}")
        return model
    print("Inner controller: no checkpoint found, falling back to max-pressure actuation")
    return None


def evaluate_plan(args, g, runner, controller, plan, seeds):
    """Install the plan, actuate with the inner controller, report congestion."""
    out = []
    for seed in seeds:
        env = make_backend(args, g, seed)
        for k in plan:
            site = runner.sites[k]
            if site["kind"] == "new-signal":
                env.install_signal(site["junction"])
            else:
                env.install_meter(site["segments"][0])
        if controller is None:
            stats = max_pressure(runner.inner, env, seed)
        else:
            obs = env.reset(seed)
            stats, done = [], False
            with torch.no_grad():
                while not done:
                    pa, sa, _, _, _ = runner.inner.policy_action(controller, obs, greedy=True)
                    obs, info, done = env.step(pa, sa)
                    stats.append(info)
        out.append(summarise(stats))
    return {k: float(np.mean([o[k] for o in out])) for k in out[0]}


class PlannerRunner(Planner):
    """Planner plus the inner-controller Runner and cached do-nothing baselines."""

    def __init__(self, g, sites, args, inner):
        super().__init__(g, sites, args)
        self.inner = inner
        self.src, self.dst, self.deg = inner.src, inner.dst, inner.deg
        self.sites = sites
        self._baseline = {}

    def baseline(self, args, controller, seeds):
        key = tuple(seeds)
        if key not in self._baseline:
            self._baseline[key] = evaluate_plan(args, self.g, self, controller, [], seeds)
        return self._baseline[key]


def plan_reward(base, built, spent, budget, cost_weight):
    gain = (base["mean_congestion"] - built["mean_congestion"]) / base["mean_congestion"] * 100
    return gain - cost_weight * (spent / budget) * 100, gain


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--backend", choices=["sim", "gnn", "sumo"], default="sim")
    p.add_argument("--world-model", default=str(ARTIFACTS / "gnn_stgnn.pt"))
    p.add_argument("--controller", default=str(ARTIFACTS / "rl_controller.pt"))
    p.add_argument("--sumo-cfg", default=None)
    p.add_argument("--map", default=None)
    p.add_argument("--gui", action="store_true")
    p.add_argument("--budget", type=float, default=80.0, help="capex budget in lakh")
    p.add_argument("--max-sites", type=int, default=5)
    p.add_argument("--iters", type=int, default=60)
    p.add_argument("--plans", type=int, default=4, help="plans sampled per iteration")
    p.add_argument("--eval-seeds", type=int, default=2)
    p.add_argument("--cost-weight", type=float, default=0.35)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--layers", type=int, default=2)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--ent-coef", type=float, default=0.02)
    p.add_argument("--seed", type=int, default=33)
    args = p.parse_args()

    torch.manual_seed(args.seed)
    g = build_graph()
    sites = build_candidates(g)
    inner = Runner(g, args)
    env = make_backend(args, g, args.seed)
    probe = env.reset(args.seed)
    controller = load_controller(args.controller, probe, inner)
    runner = PlannerRunner(g, sites, args, inner)

    policy = PlacementPolicy(probe.node.shape[1], probe.glob.shape[0], len(sites),
                             args.hidden, args.layers, probe.edge.shape[1])
    opt = torch.optim.AdamW(policy.parameters(), lr=args.lr)

    unsignalised = [j for j in g["jun_ids"] if j not in SIGNALISED]
    print(f"Network: {len(g['seg_ids'])} segments, {len(g['jun_ids'])} junctions "
          f"({len(unsignalised)} unsignalised)")
    print(f"Candidates: {len(sites)} buildable sites | budget Rs {args.budget:.0f}L | "
          f"max {args.max_sites} per plan\n")

    seeds = [4000 + i for i in range(args.eval_seeds)]
    base = runner.baseline(args, controller, seeds)
    print(f"Do-nothing baseline: congestion {base['mean_congestion']:.3f}, "
          f"speed {base['mean_speed']:.1f} km/h\n")

    baseline_r, best = None, {"reward": -1e9}
    for it in range(args.iters):
        losses, rewards = [], []
        for _ in range(args.plans):
            plan, spent, logp, ent = runner.sample_plan(policy, probe, inner)
            built = evaluate_plan(args, g, runner, controller, plan, seeds)
            r, gain = plan_reward(base, built, spent, args.budget, args.cost_weight)
            baseline_r = r if baseline_r is None else 0.9 * baseline_r + 0.1 * r
            losses.append(-(r - baseline_r) * logp - args.ent_coef * ent)
            rewards.append(r)
            if r > best["reward"]:
                best = {"reward": r, "plan": list(plan), "spent": spent, "gain": gain, "built": built}

        loss = torch.stack(losses).mean()
        opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(policy.parameters(), 1.0)
        opt.step()

        if (it + 1) % 5 == 0:
            print(f"  iter {it + 1:>3}/{args.iters}  reward {np.mean(rewards):>7.2f}  "
                  f"baseline {baseline_r:>7.2f}  best {best['reward']:.2f}")

    greedy_plan, greedy_spent, _, _ = runner.sample_plan(policy, probe, inner, greedy=True)
    greedy_built = evaluate_plan(args, g, runner, controller, greedy_plan, seeds)
    greedy_r, greedy_gain = plan_reward(base, greedy_built, greedy_spent, args.budget, args.cost_weight)

    final = best if best["reward"] >= greedy_r else {
        "reward": greedy_r, "plan": greedy_plan, "spent": greedy_spent,
        "gain": greedy_gain, "built": greedy_built,
    }

    print(f"\nRecommended build (Rs {final['spent']:.1f}L of Rs {args.budget:.0f}L, "
          f"congestion -{final['gain']:.1f}%)")
    print(f"{'site':<10}{'intervention':<44}{'capex':>9}{'marginal':>11}")
    ranked = []
    for k in final["plan"]:
        without = [x for x in final["plan"] if x != k]
        alt = evaluate_plan(args, g, runner, controller, without, seeds)
        marginal = (alt["mean_congestion"] - final["built"]["mean_congestion"]) / base["mean_congestion"] * 100
        s = sites[k]
        print(f"{s['id']:<10}{s['name'][:43]:<44}{s['cost']:>8.1f}L{marginal:>10.1f}%")
        ranked.append({**s, "marginal_gain_pct": round(float(marginal), 2)})

    ranked.sort(key=lambda x: -x["marginal_gain_pct"])
    ARTIFACTS.mkdir(exist_ok=True)
    torch.save({"state_dict": policy.state_dict(), "config": vars(args), "sites": sites},
               ARTIFACTS / "infra_agent.pt")
    (ARTIFACTS / "infra_plan.json").write_text(
        json.dumps(
            {
                "budget_lakh": args.budget,
                "capex_lakh": final["spent"],
                "congestion_reduction_pct": round(float(final["gain"]), 2),
                "baseline": base,
                "with_build": final["built"],
                "sites": ranked,
            },
            indent=2,
        )
    )
    print(f"\nSaved {ARTIFACTS / 'infra_plan.json'} and {ARTIFACTS / 'infra_agent.pt'}")


if __name__ == "__main__":
    main()
