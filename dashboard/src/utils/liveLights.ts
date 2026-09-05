import type {
  PravahNetwork,
  LiveJunction,
  LiveLight,
  CongestionLevel,
} from '../types'

const PHASE_RANK: Record<string, number> = { green: 3, amber: 2, red: 1, off: 0 }

// Junctions are scored on how full their queues are, not on speed: a signalised
// approach is supposed to be stopped for part of every cycle, so speed would
// read every red light in the city as a jam. These are the same bands the
// simulation applies at source (see gui_data/pravah_telemetry.py) — repeated
// here only so a junction still gets a colour if the feed omits the label.
function bandFromQueue(fill: number): CongestionLevel {
  if (fill <= 0.15) return 'free'
  if (fill <= 0.4) return 'light'
  if (fill <= 0.7) return 'heavy'
  return 'jammed'
}

// The phase a driver arriving at the junction sees. A junction showing green on
// any approach is "green" here: that is the movement currently being served.
function dominantPhase(live: LiveJunction | undefined): LiveLight['phase'] {
  if (!live) return 'off'
  if (live.phase && PHASE_RANK[live.phase] !== undefined) {
    return live.phase as LiveLight['phase']
  }
  let best: LiveLight['phase'] = 'off'
  for (const value of Object.values(live.phases ?? {})) {
    if ((PHASE_RANK[value] ?? 0) > (PHASE_RANK[best] ?? 0)) {
      best = value as LiveLight['phase']
    }
  }
  return best
}

// A readable name for a junction the network file left unnamed: the streets of
// its approaches, which is how anyone actually refers to an intersection.
function junctionName(
  id: string,
  given: string,
  approaches: string[],
  segmentNames: Record<string, string>,
): string {
  if (given) return given
  const streets = [...new Set(approaches.map((s) => segmentNames[s]).filter(Boolean))]
  if (streets.length) return streets.slice(0, 2).join(' × ')
  return `Junction ${id.replace(/^J:[^:]+:/, '')}`
}

/**
 * Joins the canonical network to the live feed to produce the signals the
 * dashboard lists.
 *
 * Every signalised junction appears whether or not it is reporting, so a signal
 * going silent is visible as a silent signal rather than as a card that
 * vanishes. Junctions that report without being flagged signalised are included
 * too — that is what the feed is asserting about them.
 */
export function toLiveLights(
  network: PravahNetwork | null,
  liveJunctions: Record<string, LiveJunction>,
): LiveLight[] {
  if (!network) return []

  const segmentNames: Record<string, string> = {}
  for (const s of network.segments) segmentNames[s.id] = s.name

  const lights: LiveLight[] = []
  for (const j of network.junctions) {
    const live = liveJunctions[j.id]
    if (!j.signalised && live?.status !== 'live') continue

    const m = live?.metrics ?? {}
    const queueFill = m.worst_queue_ratio ?? 0
    lights.push({
      id: j.id,
      shortId: j.id.replace(/^J:[^:]+:/, ''),
      name: junctionName(j.id, j.name, j.approaches, segmentNames),
      lat: j.lat,
      lng: j.lng,
      phase: dominantPhase(live),
      secondsToChange: live?.time_to_change ?? null,
      program: live?.program ?? null,
      phaseIndex: m.phase_index ?? null,
      state: m.state ?? null,
      vehicles: m.vehicles ?? 0,
      queued: m.queued_vehicles ?? 0,
      queueLengthM: m.queue_length_m ?? 0,
      queueFill,
      maxWaitS: m.max_waiting_time_s ?? 0,
      congestion: m.congestion ?? bandFromQueue(queueFill),
      spillback: m.spillback ?? false,
      hvCount: live?.hv_count ?? 0,
      vehicleTypes: m.vehicle_types ?? {},
      approaches: live?.approaches ?? [],
      status: live?.status ?? 'no-data',
      ageS: live?.age_s ?? 0,
    })
  }

  // Worst first: the junction that needs attention should not be scrolled to.
  return lights.sort((a, b) => b.queueFill - a.queueFill || a.shortId.localeCompare(b.shortId))
}

export const CONGESTION_COLOR: Record<CongestionLevel, string> = {
  free: '#2FA98C',
  light: '#E8A33D',
  heavy: '#E4674F',
  jammed: '#B3341C',
}

export const PHASE_COLOR: Record<LiveLight['phase'], string> = {
  green: '#2FA98C',
  amber: '#E8A33D',
  red: '#E4674F',
  off: '#9AABB8',
}
