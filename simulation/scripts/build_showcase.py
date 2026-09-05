#!/usr/bin/env python3
"""Assembles the PRAVAH control-room showcase artifact from comparison.json
and network_geometry.json -- a self-contained HTML file with no external
dependencies (data is embedded inline), so it can be published as-is.

Usage:
    python scripts/build_showcase.py \
        --comparison sumo/video/comparison.json \
        --network sumo/video/network_geometry.json \
        --out sumo/video/showcase.html
"""

import argparse
import json
from pathlib import Path

TEMPLATE = r"""<title>Pravah Control Room</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #F4F7F6;
  --surface: #FFFFFF;
  --surface-2: #E5EEEC;
  --border: #CDDBD7;
  --text: #0E1F1B;
  --text-dim: #4B615C;
  --accent: #0E8F79;
  --accent-soft: #CFEEE7;
  --good: #16A34A;
  --warn: #B45309;
  --bad: #DC2626;
  --road: #A9BFB9;
  --font-display: "Oswald", "Arial Narrow", sans-serif;
  --font-body: "Public Sans", "Segoe UI", sans-serif;
  --font-mono: "IBM Plex Mono", "Consolas", monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0A1412;
    --surface: #101F1C;
    --surface-2: #16302B;
    --border: #21403A;
    --text: #E4F2EE;
    --text-dim: #8FA9A3;
    --accent: #37E6C4;
    --accent-soft: #143B34;
    --good: #4ADE80;
    --warn: #FBBF24;
    --bad: #F87171;
    --road: #35524C;
  }
}
:root[data-theme="dark"] {
  --bg: #0A1412;
  --surface: #101F1C;
  --surface-2: #16302B;
  --border: #21403A;
  --text: #E4F2EE;
  --text-dim: #8FA9A3;
  --accent: #37E6C4;
  --accent-soft: #143B34;
  --good: #4ADE80;
  --warn: #FBBF24;
  --bad: #F87171;
  --road: #35524C;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-body);
  padding: clamp(16px, 3vw, 40px);
}
.wrap { max-width: 1280px; margin: 0 auto; display: flex; flex-direction: column; gap: 28px; }

header { display: flex; flex-direction: column; gap: 6px; }
.eyebrow {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--accent);
}
h1 {
  font-family: var(--font-display);
  font-weight: 600;
  font-size: clamp(2rem, 4vw, 2.9rem);
  letter-spacing: 0.01em;
  margin: 0;
  text-wrap: balance;
}
.sub { color: var(--text-dim); font-size: 1rem; max-width: 62ch; line-height: 1.5; }

.panels { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
@media (max-width: 860px) { .panels { grid-template-columns: 1fr; } }

.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-2);
}
.panel-title {
  font-family: var(--font-display);
  font-size: 1.05rem;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}
.panel-title.mp { color: var(--accent); }
.panel-stats {
  font-family: var(--font-mono);
  font-size: 0.82rem;
  color: var(--text-dim);
  display: flex;
  gap: 14px;
  font-variant-numeric: tabular-nums;
}
.panel-stats b { color: var(--text); font-weight: 500; }
canvas { display: block; width: 100%; height: auto; background: var(--bg); }

.transport {
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 12px 18px;
}
button.play {
  font-family: var(--font-display);
  font-size: 0.95rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  background: var(--accent);
  color: var(--bg);
  border: none;
  border-radius: 3px;
  padding: 9px 20px;
  cursor: pointer;
}
button.play:focus-visible, button.speed:focus-visible, input[type=range]:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.speeds { display: flex; gap: 4px; }
button.speed {
  font-family: var(--font-mono);
  font-size: 0.78rem;
  background: transparent;
  color: var(--text-dim);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 6px 10px;
  cursor: pointer;
}
button.speed.active { color: var(--accent); border-color: var(--accent); }
input[type=range] { flex: 1; accent-color: var(--accent); }
.clock {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--text-dim);
  min-width: 5.5ch;
  text-align: right;
}

.metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
@media (max-width: 860px) { .metrics { grid-template-columns: repeat(2, 1fr); } }
.stat {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.stat-label {
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-dim);
}
.stat-value {
  font-family: var(--font-mono);
  font-size: 1.6rem;
  font-variant-numeric: tabular-nums;
}
.stat-value.good { color: var(--good); }
.stat-value.bad { color: var(--bad); }
.stat-note { font-size: 0.8rem; color: var(--text-dim); }

.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
@media (max-width: 860px) { .charts { grid-template-columns: 1fr; } }
.chart-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 16px 18px;
}
.chart-title {
  font-family: var(--font-display);
  font-size: 0.95rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-bottom: 10px;
}
.legend { display: flex; gap: 18px; font-family: var(--font-mono); font-size: 0.76rem; color: var(--text-dim); margin-top: 8px; }
.legend span::before {
  content: "";
  display: inline-block;
  width: 10px; height: 2px;
  margin-right: 6px;
  vertical-align: middle;
}
.legend .lf::before { background: var(--text-dim); }
.legend .lm::before { background: var(--accent); }

footer {
  color: var(--text-dim);
  font-size: 0.82rem;
  line-height: 1.6;
  border-top: 1px solid var(--border);
  padding-top: 16px;
}
footer b { color: var(--text); font-weight: 500; }
</style>

<div class="wrap">
  <header>
    <div class="eyebrow">Pravah &middot; Digital Twin &middot; Signal Control Replay</div>
    <h1>Same road. Same demand. Different signal logic.</h1>
    <p class="sub">Both panels replay the identical vehicle demand on the identical network from the same
      random seed &mdash; the only variable is how the traffic lights decide when to switch.
      <b style="color:var(--text)">Left</b> runs the network's own fixed-time program.
      <b style="color:var(--accent)">Right</b> runs PRAVAH's max-pressure controller, which reads live queue
      lengths every control step and switches whichever light is doing the most good right now.</p>
  </header>

  <div class="panels">
    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">Fixed-Time (baseline)</span>
        <span class="panel-stats">speed <b id="fx-speed">&mdash;</b> m/s &nbsp; waiting <b id="fx-wait">&mdash;</b> s</span>
      </div>
      <canvas id="cv-fixed" width="900" height="560"></canvas>
    </div>
    <div class="panel">
      <div class="panel-head">
        <span class="panel-title mp">Max-Pressure (Pravah)</span>
        <span class="panel-stats">speed <b id="mp-speed">&mdash;</b> m/s &nbsp; waiting <b id="mp-wait">&mdash;</b> s</span>
      </div>
      <canvas id="cv-mp" width="900" height="560"></canvas>
    </div>
  </div>

  <div class="transport">
    <button class="play" id="playBtn">Play</button>
    <div class="speeds">
      <button class="speed" data-speed="1">1&times;</button>
      <button class="speed active" data-speed="4">4&times;</button>
      <button class="speed" data-speed="10">10&times;</button>
    </div>
    <input type="range" id="scrub" min="0" max="1000" value="0">
    <span class="clock" id="clock">00:00</span>
  </div>

  <div class="metrics" id="metrics"></div>

  <div class="charts">
    <div class="chart-card">
      <div class="chart-title">Network-wide waiting time</div>
      <svg id="chart-wait" viewBox="0 0 400 120" preserveAspectRatio="none" style="width:100%;height:110px;"></svg>
      <div class="legend"><span class="lf">Fixed-time</span><span class="lm">Max-pressure</span></div>
    </div>
    <div class="chart-card">
      <div class="chart-title">Mean edge speed</div>
      <svg id="chart-speed" viewBox="0 0 400 120" preserveAspectRatio="none" style="width:100%;height:110px;"></svg>
      <div class="legend"><span class="lf">Fixed-time</span><span class="lm">Max-pressure</span></div>
    </div>
  </div>

  <footer>
    <b>Network:</b> 132 edges / 99 junctions / 3 signalized, built from the real submitted TomTom corridor geometry &nbsp;&middot;&nbsp;
    <b>Demand:</b> identical randomTrips seed <span id="f-seed"></span>, <span id="f-duration"></span>s simulated &nbsp;&middot;&nbsp;
    <b>Control interval:</b> <span id="f-interval"></span>s. Simulated in SUMO, driven over TraCI by PravahEnv; rendered client-side from recorded trajectories, no video encoding involved.
  </footer>
</div>

<script>
const NET = __NET_JSON__;
const RUNS = __RUNS_JSON__;

function tlsColor(state) {
  const s = state.toLowerCase();
  if (s.includes('y')) return getVar('--warn');
  if (s.includes('g')) return getVar('--good');
  return getVar('--bad');
}
function getVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function makeProjector() {
  const b = NET.bounds;
  const margin = 40;
  return function(w, h) {
    const dataW = b.xmax - b.xmin, dataH = b.ymax - b.ymin;
    const scale = Math.min((w - 2 * margin) / dataW, (h - 2 * margin) / dataH);
    const offX = (w - dataW * scale) / 2;
    const offY = (h - dataH * scale) / 2;
    return function(x, y) {
      return [offX + (x - b.xmin) * scale, h - (offY + (y - b.ymin) * scale)];
    };
  };
}
const projectorFactory = makeProjector();

function drawPanel(canvas, frame) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  const project = projectorFactory(w, h);

  ctx.fillStyle = getVar('--bg');
  ctx.fillRect(0, 0, w, h);

  ctx.strokeStyle = getVar('--road');
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  for (const edge of NET.edges) {
    for (let i = 0; i < edge.length; i++) {
      const [px, py] = project(edge[i][0], edge[i][1]);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
  }
  ctx.stroke();

  if (frame) {
    const accent = getVar('--accent');
    ctx.fillStyle = accent;
    ctx.shadowColor = accent;
    ctx.shadowBlur = 4;
    for (const v of frame.vehicles) {
      const [px, py] = project(v.x, v.y);
      ctx.beginPath();
      ctx.arc(px, py, 2.4, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.shadowBlur = 0;

    for (const [tid, pos] of Object.entries(NET.tls_positions)) {
      const state = frame.tls[tid];
      if (!state) continue;
      const [px, py] = project(pos[0], pos[1]);
      ctx.fillStyle = tlsColor(state);
      ctx.beginPath();
      ctx.arc(px, py, 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = getVar('--bg');
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }
}

function frameAt(frames, t) {
  let lo = 0, hi = frames.length - 1;
  if (t <= frames[0].t) return frames[0];
  if (t >= frames[hi].t) return frames[hi];
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (frames[mid].t <= t) lo = mid; else hi = mid - 1;
  }
  return frames[lo];
}

function metricAt(series, t, maxT) {
  if (!series.length) return 0;
  const idx = Math.min(series.length - 1, Math.max(0, Math.round((t / maxT) * (series.length - 1))));
  return series[idx];
}

const fixed = RUNS.fixed_time, mp = RUNS.max_pressure;
const maxT = Math.min(fixed.frames[fixed.frames.length - 1].t, mp.frames[mp.frames.length - 1].t);

const cvFixed = document.getElementById('cv-fixed');
const cvMp = document.getElementById('cv-mp');
const scrub = document.getElementById('scrub');
const clock = document.getElementById('clock');
const playBtn = document.getElementById('playBtn');

let t = 0, playing = false, speed = 4, lastTs = null;

function fmtClock(sec) {
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}

function render() {
  const ff = frameAt(fixed.frames, t);
  const mf = frameAt(mp.frames, t);
  drawPanel(cvFixed, ff);
  drawPanel(cvMp, mf);
  document.getElementById('fx-speed').textContent = metricAt(fixed.summary.speed_series, t, maxT).toFixed(1);
  document.getElementById('fx-wait').textContent = metricAt(fixed.summary.waiting_series, t, maxT).toFixed(0);
  document.getElementById('mp-speed').textContent = metricAt(mp.summary.speed_series, t, maxT).toFixed(1);
  document.getElementById('mp-wait').textContent = metricAt(mp.summary.waiting_series, t, maxT).toFixed(0);
  clock.textContent = fmtClock(t);
  scrub.value = Math.round((t / maxT) * 1000);
}

function tick(ts) {
  if (playing) {
    if (lastTs !== null) {
      const dtReal = (ts - lastTs) / 1000;
      t = Math.min(maxT, t + dtReal * speed);
      if (t >= maxT) playing = false, playBtn.textContent = 'Replay';
    }
    lastTs = ts;
    render();
  } else {
    lastTs = null;
  }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

playBtn.addEventListener('click', () => {
  if (!playing && t >= maxT) t = 0;
  playing = !playing;
  playBtn.textContent = playing ? 'Pause' : 'Play';
});
scrub.addEventListener('input', () => {
  t = (scrub.value / 1000) * maxT;
  playing = false;
  playBtn.textContent = 'Play';
  render();
});
document.querySelectorAll('button.speed').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('button.speed').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    speed = Number(btn.dataset.speed);
  });
});

function pct(a, b) {
  if (!a) return null;
  return ((b - a) / a) * 100;
}
function fmtPct(p) {
  if (p === null) return 'n/a';
  return (p > 0 ? '+' : '') + p.toFixed(1) + '%';
}

const waitDelta = pct(fixed.summary.final_total_waiting_time, mp.summary.final_total_waiting_time);
const speedDelta = pct(fixed.summary.mean_speed, mp.summary.mean_speed);
const arrivedDelta = pct(fixed.summary.total_arrived, mp.summary.total_arrived);

const stats = [
  { label: 'Total waiting time', value: fmtPct(waitDelta), note: fixed.summary.final_total_waiting_time.toFixed(0) + 's → ' + mp.summary.final_total_waiting_time.toFixed(0) + 's', good: waitDelta !== null && waitDelta < 0 },
  { label: 'Mean edge speed', value: fmtPct(speedDelta), note: fixed.summary.mean_speed.toFixed(2) + ' → ' + mp.summary.mean_speed.toFixed(2) + ' m/s', good: speedDelta !== null && speedDelta >= 0 },
  { label: 'Vehicles arrived', value: fmtPct(arrivedDelta), note: fixed.summary.total_arrived + ' → ' + mp.summary.total_arrived, good: arrivedDelta !== null && arrivedDelta >= 0 },
  { label: 'Teleports (gridlock events)', value: String(mp.summary.total_teleported) + ' vs ' + String(fixed.summary.total_teleported), note: 'lower is better', good: mp.summary.total_teleported <= fixed.summary.total_teleported },
];
const metricsEl = document.getElementById('metrics');
metricsEl.innerHTML = stats.map(s =>
  `<div class="stat"><div class="stat-label">${s.label}</div><div class="stat-value ${s.good ? 'good' : 'bad'}">${s.value}</div><div class="stat-note">${s.note}</div></div>`
).join('');

function drawChart(svgId, seriesA, seriesB) {
  const svg = document.getElementById(svgId);
  const all = seriesA.concat(seriesB);
  const min = Math.min(...all), max = Math.max(...all, min + 1);
  const W = 400, H = 120, pad = 6;
  function path(series, color) {
    const n = series.length;
    const pts = series.map((v, i) => {
      const x = pad + (i / (n - 1)) * (W - 2 * pad);
      const y = H - pad - ((v - min) / (max - min)) * (H - 2 * pad);
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  }
  svg.innerHTML = path(seriesA, getVar('--text-dim')) + path(seriesB, getVar('--accent'));
}
drawChart('chart-wait', fixed.summary.waiting_series, mp.summary.waiting_series);
drawChart('chart-speed', fixed.summary.speed_series, mp.summary.speed_series);

document.getElementById('f-seed').textContent = RUNS.meta.seed;
document.getElementById('f-duration').textContent = RUNS.meta.duration;
document.getElementById('f-interval').textContent = RUNS.meta.control_interval;

render();
</script>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    comparison = json.load(open(args.comparison))
    network = json.load(open(args.network))

    html = TEMPLATE.replace("__NET_JSON__", json.dumps(network))
    html = html.replace("__RUNS_JSON__", json.dumps(comparison))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        f.write(html)
    print(f"Wrote showcase: {args.out} ({Path(args.out).stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
