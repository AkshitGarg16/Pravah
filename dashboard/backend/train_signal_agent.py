"""
PRAVAH — adaptive signal control agent.

Tabular Q-learning controller for a two-phase intersection, evaluated against
fixed-time and max-pressure baselines on held-out traffic days.

    python backend/train_signal_agent.py
"""

import json
from pathlib import Path

import numpy as np

ARTIFACTS = Path(__file__).parent / "artifacts"

STEP_SECONDS = 5
EPISODE_STEPS = 240
SAT_FLOW = 4
MIN_GREEN_STEPS = 3
LOST_TIME_STEPS = 1
SWITCH_PENALTY = 6.0

Q_BINS = np.array([2, 5, 9, 14, 20, 28, 40])
ELAPSED_BINS = np.array([2, 4, 6, 9])
N_Q = len(Q_BINS) + 1
N_ELAPSED = len(ELAPSED_BINS) + 1
N_PHASE = 2
N_ACTIONS = 2

ALPHA = 0.15
GAMMA = 0.95
EPISODES = 4000
EPS_START, EPS_END = 1.0, 0.05


class Intersection:
    """Two-phase junction: phase 0 serves north-south, phase 1 serves east-west."""

    def __init__(self, rng, demand_scale=1.0):
        self.rng = rng
        self.demand_scale = demand_scale
        self.reset()

    def reset(self):
        self.t = 0
        self.phase = 0
        self.elapsed = 0
        self.clearing = 0
        self.queues = np.array([6.0, 6.0])
        self.total_wait = 0.0
        self.served = 0
        self.arrived = 0
        return self.state()

    def arrival_rates(self):
        peak = 1.0 + 0.55 * np.sin(2 * np.pi * self.t / EPISODE_STEPS)
        ns = 2.6 * peak * self.demand_scale
        ew = 1.9 * (2.0 - peak) * self.demand_scale
        return np.array([ns, ew])

    def state(self):
        qns, qew = np.digitize(self.queues, Q_BINS)
        el = np.digitize(self.elapsed, ELAPSED_BINS)
        return int(qns), int(qew), int(self.phase), int(el)

    def step(self, action):
        switching = action == 1 and self.elapsed >= MIN_GREEN_STEPS
        if switching:
            self.phase = 1 - self.phase
            self.elapsed = 0
            self.clearing = LOST_TIME_STEPS

        if self.clearing > 0:
            self.clearing -= 1
        else:
            discharged = min(self.queues[self.phase], SAT_FLOW)
            self.queues[self.phase] -= discharged
            self.served += discharged

        arrivals = self.rng.poisson(self.arrival_rates())
        self.queues += arrivals
        self.arrived += arrivals.sum()

        self.total_wait += self.queues.sum() * STEP_SECONDS
        self.elapsed += 1
        self.t += 1

        reward = -self.queues.sum() - (SWITCH_PENALTY if switching else 0.0)
        done = self.t >= EPISODE_STEPS
        return self.state(), reward, done

    def metrics(self):
        served = max(self.served, 1.0)
        return {
            "avg_delay_s": self.total_wait / served,
            "avg_queue": self.total_wait / (EPISODE_STEPS * STEP_SECONDS * 2),
            "throughput_vph": self.served * 3600 / (EPISODE_STEPS * STEP_SECONDS),
            "clearance_rate": self.served / max(self.arrived, 1.0),
        }


def fixed_time_policy(env, cycle_steps=6):
    return 1 if env.elapsed >= cycle_steps else 0


def max_pressure_policy(env):
    if env.elapsed < MIN_GREEN_STEPS:
        return 0
    return 1 if env.queues[1 - env.phase] > env.queues[env.phase] * 1.25 else 0


def train():
    rng = np.random.default_rng(7)
    q = np.zeros((N_Q, N_Q, N_PHASE, N_ELAPSED, N_ACTIONS))
    history = []

    for ep in range(EPISODES):
        eps = EPS_END + (EPS_START - EPS_END) * np.exp(-3.0 * ep / EPISODES)
        env = Intersection(rng, demand_scale=rng.uniform(0.75, 1.25))
        s = env.reset()
        done = False
        while not done:
            a = rng.integers(N_ACTIONS) if rng.random() < eps else int(np.argmax(q[s]))
            s2, r, done = env.step(a)
            q[s][a] += ALPHA * (r + GAMMA * q[s2].max() - q[s][a])
            s = s2
        if (ep + 1) % 500 == 0:
            history.append({"episode": ep + 1, "epsilon": round(eps, 3), **env.metrics()})
            print(
                f"  episode {ep + 1:>5}  eps={eps:.2f}  "
                f"delay={env.metrics()['avg_delay_s']:6.1f}s"
            )
    return q, history


def evaluate(policy_fn, episodes=60, seed=99):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(episodes):
        env = Intersection(rng, demand_scale=rng.uniform(0.8, 1.2))
        s = env.reset()
        done = False
        while not done:
            s, _, done = env.step(policy_fn(env, s))
        out.append(env.metrics())
    return {k: float(np.mean([m[k] for m in out])) for k in out[0]}


def main():
    print("Training adaptive signal agent (tabular Q-learning)")
    q, history = train()

    results = {
        "fixed_time_30s": evaluate(lambda env, s: fixed_time_policy(env)),
        "max_pressure": evaluate(lambda env, s: max_pressure_policy(env)),
        "pravah_q_agent": evaluate(lambda env, s: int(np.argmax(q[s]))),
    }

    base = results["fixed_time_30s"]["avg_delay_s"]
    print("\nHeld-out evaluation (60 days, unseen demand seeds)")
    print(f"{'policy':<18}{'avg delay':>12}{'avg queue':>12}{'veh/h':>10}{'vs fixed':>11}")
    for name, m in results.items():
        gain = (base - m["avg_delay_s"]) / base * 100
        print(
            f"{name:<18}{m['avg_delay_s']:>10.1f}s{m['avg_queue']:>12.1f}"
            f"{m['throughput_vph']:>10.0f}{gain:>10.1f}%"
        )

    agent = results["pravah_q_agent"]
    print(
        f"\nAgent cuts average delay {(base - agent['avg_delay_s']) / base * 100:.1f}% "
        f"and clears {agent['clearance_rate'] * 100:.1f}% of arrivals."
    )

    ARTIFACTS.mkdir(exist_ok=True)
    np.savez_compressed(ARTIFACTS / "signal_agent_q.npz", q=q, q_bins=Q_BINS, elapsed_bins=ELAPSED_BINS)
    (ARTIFACTS / "signal_agent_report.json").write_text(
        json.dumps({"training": history, "evaluation": results}, indent=2)
    )
    print(f"Saved policy to {ARTIFACTS / 'signal_agent_q.npz'}")


if __name__ == "__main__":
    main()
