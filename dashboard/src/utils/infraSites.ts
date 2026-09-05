import type { PravahNetwork, LiveSegment, SuggestedSite } from '../types'

/**
 * Candidate hardware sites, placed on the road rather than beside it.
 *
 * Infrastructure Development Mode used to plot eleven hand-written sites at
 * fixed coordinates. Those came from the old OSM extract, about two kilometres
 * west of the simulated network, so the pins landed on roads the feed does not
 * report on — some of them nowhere near a road at all.
 *
 * These are derived from the canonical network instead. One candidate per road
 * segment, positioned at the segment's **midpoint measured along its geometry**
 * (not the average of its endpoints, which leaves a curved road's marker off in
 * a field), ranked by how badly that segment is actually running right now.
 *
 * Everything reported per site is a measured deficit, not a model prediction:
 * the speed figure is the share of free-flow travel time the segment is
 * currently losing, and the occupancy figure is how full it is. Treat them as
 * the size of the problem at that spot, which is what ranks the sites.
 */

const MIN_LENGTH_M = 40 // shorter than this and a mid-road unit has no room
const DEFAULT_LIMIT = 14

// Indicative capex per intervention, for the deployment-cost column. Order of
// magnitude only -- these are not quotes.
const COST: Record<SuggestedSite['kind'], number> = {
  'pravah-midroad': 18.4,
  'new-signal': 31.0,
  'corridor-sync': 7.5,
  'camera-upgrade': 11.2,
}

/** Metres between two lat/lng points, flat-earth: fine over a 2 km network. */
function metres(a: [number, number], b: [number, number]): number {
  const latRad = ((a[0] + b[0]) / 2) * (Math.PI / 180)
  const dy = (b[0] - a[0]) * 110_574
  const dx = (b[1] - a[1]) * 111_320 * Math.cos(latRad)
  return Math.hypot(dx, dy)
}

/**
 * The point half the polyline's length from its start, interpolated inside
 * whichever leg contains it. For a straight two-point segment this is the plain
 * midpoint; for a curved one it stays on the road.
 */
export function midpointAlong(coords: [number, number][]): [number, number] | null {
  if (!coords || coords.length === 0) return null
  if (coords.length === 1) return coords[0]

  const legs: number[] = []
  let total = 0
  for (let i = 1; i < coords.length; i++) {
    const d = metres(coords[i - 1], coords[i])
    legs.push(d)
    total += d
  }
  if (total === 0) return coords[0]

  let walked = 0
  const half = total / 2
  for (let i = 0; i < legs.length; i++) {
    if (walked + legs[i] >= half) {
      const t = legs[i] === 0 ? 0 : (half - walked) / legs[i]
      const [aLat, aLng] = coords[i]
      const [bLat, bLng] = coords[i + 1]
      return [aLat + (bLat - aLat) * t, aLng + (bLng - aLng) * t]
    }
    walked += legs[i]
  }
  return coords[coords.length - 1]
}

export function toInfraSites(
  network: PravahNetwork | null,
  liveSegments: Record<string, LiveSegment>,
  limit: number = DEFAULT_LIMIT,
): SuggestedSite[] {
  if (!network) return []

  // Which junctions already have signals, and how many roads meet at each: a
  // segment feeding an unsignalised four-way is a different proposition from
  // one feeding a signal that just needs re-timing.
  const signalised = new Set<string>()
  const approachCount: Record<string, number> = {}
  for (const j of network.junctions ?? []) {
    if (j.signalised) signalised.add(j.id)
    approachCount[String(j.osm_node ?? j.id)] = (j.approaches ?? []).length
    approachCount[j.id] = (j.approaches ?? []).length
  }

  const scored = (network.segments ?? [])
    .map((seg) => {
      const coords = (seg.coords ?? []) as [number, number][]
      const point = midpointAlong(coords)
      if (!point) return null
      if ((seg.length_m ?? 0) < MIN_LENGTH_M) return null

      const live = liveSegments[seg.id]
      // The middleware normalises every source to km/h before it stores a
      // snapshot (its _speed_kmh), so these are already km/h -- do not convert.
      // Prefer the free-flow it scored against over the network's own figure.
      const freeFlow = live?.free_flow ?? seg.free_flow_kmh ?? 40
      const speedKmh = live?.speed ?? freeFlow
      // 1 = running at the limit, 0 = stopped. Speeds slightly above the limit
      // are real and get clamped rather than counted as a negative deficit.
      const ratio = freeFlow > 0 ? Math.max(0, Math.min(1, speedKmh / freeFlow)) : 1
      const deficit = 1 - ratio

      const halting = live?.halting ?? 0
      // How full the road is, 0..1. The snapshot carries occupancy but not a
      // vehicle count, so this is the density term -- and it is the better one
      // anyway: a road at 95% occupancy is spilling back whatever its speed.
      const load = Math.max(0, Math.min(1, live?.occupancy ?? 0))
      const lanes = seg.lanes || 1

      // Score: the speed deficit is what a signal can act on, weighted up by
      // how full the road is and how much of it is being wasted. Kept on 10..99
      // so the priority bands stay meaningful.
      const raw = deficit * 70 + load * 20 + Math.min(1, lanes / 3) * 10
      const score = Math.max(10, Math.min(99, Math.round(raw)))

      const downstream = String(seg.to_node ?? '')
      const downstreamId = `J:sumo:${downstream}`
      const isSignalised = signalised.has(downstreamId) || signalised.has(downstream)
      const arms = approachCount[downstreamId] ?? approachCount[downstream] ?? 0

      const kind: SuggestedSite['kind'] = isSignalised
        ? 'corridor-sync'
        : arms >= 3
          ? 'new-signal'
          : 'pravah-midroad'

      // Saturation flow of roughly 1800 veh/h/lane is the standard planning
      // figure; the shortfall against it is what the segment is not carrying.
      const throughputGain = Math.round(lanes * 1800 * deficit)

      const name = seg.name?.trim()
        ? `${seg.name} · mid-segment`
        : `Unnamed link ${seg.id.replace('S:sumo:', '')} · mid-segment`

      return {
        seg,
        score,
        site: {
          id: seg.id,
          name,
          kind,
          lat: point[0],
          lng: point[1],
          score,
          delaySaving: Math.round(deficit * 100),
          queueSaving: Math.round(load * 100),
          throughputGain,
          affectedSegments: [seg.id],
          rationale: buildRationale(speedKmh, freeFlow, halting, load, seg.length_m ?? 0, kind),
          status: (score >= 80
            ? 'recommended'
            : score >= 65
              ? 'under-review'
              : 'planned') as SuggestedSite['status'],
          cost: `₹${(COST[kind] * Math.max(1, lanes / 2)).toFixed(1)}L`,
        } satisfies SuggestedSite,
      }
    })
    .filter((x): x is NonNullable<typeof x> => x !== null)

  // Worst first, then by id so the ranking does not jitter between snapshots
  // when two segments happen to score the same.
  scored.sort((a, b) => b.score - a.score || a.site.id.localeCompare(b.site.id))
  return scored.slice(0, limit).map((x) => x.site)
}

function buildRationale(
  speedKmh: number,
  freeFlowKmh: number,
  halting: number,
  occupancy: number,
  lengthM: number,
  kind: SuggestedSite['kind'],
): string {
  const observed = `Running at ${speedKmh.toFixed(0)} km/h against a ${freeFlowKmh.toFixed(0)} km/h limit`
  const queue =
    halting > 0
      ? `, ${halting} vehicle${halting === 1 ? '' : 's'} stopped on it and ${Math.round(occupancy * 100)}% of the road occupied`
      : occupancy > 0
        ? `, ${Math.round(occupancy * 100)}% of the road occupied but nothing stopped`
        : ', clear at this moment'
  const action =
    kind === 'corridor-sync'
      ? 'It already feeds a signal, so re-timing that signal is the cheaper intervention than new hardware here.'
      : kind === 'new-signal'
        ? 'It feeds an unsignalised junction with three or more arms — a stochastic merge that signalising turns into a schedulable phase.'
        : `A mid-road PRAVAH unit at the ${Math.round(lengthM / 2)} m mark can hold platoons before they reach the next junction.`
  return `${observed}${queue}. ${action}`
}
