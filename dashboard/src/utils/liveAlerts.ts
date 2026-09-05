import type { Alert, LiveLight, LiveSegment, NetworkSegment, NetworkStats } from '../types'

// Thresholds behind the alert feed. Kept here, named, rather than inline: these
// are the definition of "worth interrupting someone about" and are the first
// thing anyone will want to tune.
const SPILLBACK_FILL = 0.95 // queue has reached the junction upstream
const HEAVY_FILL = 0.7 // approach more than 70% full
const LONG_WAIT_S = 120 // two minutes stopped at one signal
const JAMMED_SPEED_RATIO = 0.3 // moving at under 30% of the road's own limit
const NETWORK_JAMMED_RATIO = 0.4

const MAX_ALERTS = 12

function clock(): string {
  return new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/**
 * Derives the alert feed from the current snapshot.
 *
 * Deliberately stateless: each alert describes a condition true *now*, with an
 * id derived from what it is about rather than when it fired. A condition that
 * persists keeps the same id and stays put in the list instead of flooding it
 * with one entry per second, and a condition that clears simply disappears.
 */
export function toLiveAlerts(
  lights: LiveLight[],
  liveSegments: Record<string, LiveSegment>,
  segments: NetworkSegment[],
  netStats: NetworkStats | null,
): Alert[] {
  const alerts: Alert[] = []
  const time = clock()

  for (const light of lights) {
    if (light.status === 'no-data') continue

    if (light.spillback || light.queueFill >= SPILLBACK_FILL) {
      alerts.push({
        id: `spillback:${light.id}`,
        type: 'anomaly',
        tag: 'Spillback',
        time,
        msg: `Queue at ${light.name} has filled ${Math.round(light.queueFill * 100)}% of its approach and is blocking the junction upstream.`,
        loc: light.name,
        lat: light.lat,
        lng: light.lng,
      })
    } else if (light.queueFill >= HEAVY_FILL) {
      alerts.push({
        id: `queue:${light.id}`,
        type: 'warning',
        tag: 'Queue',
        time,
        msg: `${light.name} is ${Math.round(light.queueFill * 100)}% full — ${light.queued} vehicles stopped over ${Math.round(light.queueLengthM)} m.`,
        loc: light.name,
        lat: light.lat,
        lng: light.lng,
      })
    }

    if (light.maxWaitS >= LONG_WAIT_S) {
      alerts.push({
        id: `wait:${light.id}`,
        type: 'warning',
        tag: 'Delay',
        time,
        msg: `Longest wait at ${light.name} is ${Math.round(light.maxWaitS)}s on one approach.`,
        loc: light.name,
        lat: light.lat,
        lng: light.lng,
      })
    }
  }

  // Roads are scored on speed against their own limit — the only measure that
  // means the same thing on a 30 km/h side street and a 70 km/h arterial.
  const jammed = segments
    .map((seg) => ({ seg, live: liveSegments[seg.id] }))
    .filter(({ live }) => live?.status === 'live' && live.speed !== undefined &&
      live.free_flow !== undefined && live.free_flow > 0 &&
      live.speed / live.free_flow < JAMMED_SPEED_RATIO)
    .sort((a, b) => (a.live!.speed! / a.live!.free_flow!) - (b.live!.speed! / b.live!.free_flow!))
    .slice(0, 4)

  for (const { seg, live } of jammed) {
    alerts.push({
      id: `jam:${seg.id}`,
      type: 'anomaly',
      tag: 'Congestion',
      time,
      msg: `${seg.name} is moving at ${live!.speed} km/h against a ${live!.free_flow} km/h limit${live!.halting ? ` — ${live!.halting} vehicles stopped` : ''}.`,
      loc: seg.name,
      lat: seg.coords[Math.floor(seg.coords.length / 2)][0],
      lng: seg.coords[Math.floor(seg.coords.length / 2)][1],
    })
  }

  if (netStats) {
    // A teleport is SUMO removing a vehicle that has been stuck too long to be
    // simulated. It is the clearest signal that the network has gridlocked
    // rather than merely slowed.
    if ((netStats.teleports_total ?? 0) > 0) {
      alerts.push({
        id: 'network:teleports',
        type: 'anomaly',
        tag: 'Gridlock',
        time,
        msg: `${netStats.teleports_total} vehicles removed after being stuck — the network is gridlocking, not just slowing.`,
        loc: 'Network',
        lat: 0,
        lng: 0,
      })
    }
    if ((netStats.mean_speed_ratio ?? 1) < NETWORK_JAMMED_RATIO) {
      alerts.push({
        id: 'network:speed',
        type: 'warning',
        tag: 'Network',
        time,
        msg: `Network is running at ${Math.round((netStats.mean_speed_ratio ?? 0) * 100)}% of free-flow speed, ${netStats.halting_vehicles} of ${netStats.running_vehicles} vehicles stopped.`,
        loc: 'Network',
        lat: 0,
        lng: 0,
      })
    }
  }

  if (!alerts.length) {
    alerts.push({
      id: 'ok',
      type: 'info',
      tag: 'Clear',
      time,
      msg: netStats
        ? `All signals clearing their queues. ${netStats.running_vehicles ?? 0} vehicles at ${Math.round((netStats.mean_speed_ms ?? 0) * 3.6)} km/h.`
        : 'No congestion reported.',
      loc: 'Network',
      lat: 0,
      lng: 0,
    })
  }

  const severity = { anomaly: 0, warning: 1, info: 2 }
  return alerts.sort((a, b) => severity[a.type] - severity[b.type]).slice(0, MAX_ALERTS)
}
