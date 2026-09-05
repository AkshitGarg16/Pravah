export interface TrafficLight {
  id: string
  name: string
  type: 'junction' | 'pravah-midroad'
  lat: number
  lng: number
  phase: 'green' | 'red' | 'amber'
  farPressure: number // 0-100, GNSS derived
  nearPressure: number // 0-100, camera derived
  nextChange: number // seconds until next phase
  actuation: string // human readable reason
  hvCount: number // heavy vehicles in queue
  hvImpact: string | null // e.g. "+8s green"
  evMode: boolean // emergency vehicle corridor active
}

export interface Segment {
  id: string
  coords: [number, number][] // [lat, lng] pairs
  speed: number // current km/h
  freeFlow: number // expected km/h
  congestion: number // 0 to 1
  road?: string // OSM way name, e.g. "Bahadur Shah Zafar Marg"
  osmWay?: string // OSM way id the geometry was traced from
  lanes?: number
}

export interface HealthSource {
  source: string
  status: 'ok' | 'warn' | 'err'
  detail: string
  extra: string
}

// ---- Canonical network + live feed (see backend/pravah_middleware.py) ----

export interface NetworkSegment {
  id: string // S:sumo:<edge> (or S:osm:<way>:<from>:<to> for an OSM-built network)
  osm_way: number | string
  from_node: number | string
  to_node: number | string
  name: string
  highway: string
  lanes: number
  oneway: boolean
  length_m: number
  free_flow_kmh: number
  coords: [number, number][] // [lat, lng]
  tomtom_ids?: string[] // original TomTom CSV segment ids behind this edge
}

export interface NetworkJunction {
  id: string // J:sumo:<junction> (or J:osm:<node>)
  osm_node: number | string | null
  name: string
  lat: number
  lng: number
  signalised: boolean
  approaches: string[]
}

export interface PravahNetwork {
  center: { lat: number; lng: number; radius_m: number }
  segments: NetworkSegment[]
  junctions: NetworkJunction[]
  movements: { junction: string; from: string; to: string }[]
}

export interface LiveSegment {
  id: string
  status: 'live' | 'stale' | 'no-data'
  speed?: number
  free_flow?: number
  congestion?: number
  pressure?: number
  occupancy?: number | null
  halting?: number | null
  count?: number // vehicles on the segment this step
  source?: string
  age_s?: number
  fused_from?: string[]
}

// Per-approach analytics behind a signal, straight from the simulation.
export interface LiveApproach {
  lane_id: string
  segment: string
  segment_ids: string[]
  phase: 'green' | 'amber' | 'red' | 'off'
  vehicles: number
  halting_vehicles: number
  queue_length_m: number
  queue_ratio: number
  mean_speed_ms: number
  speed_ratio: number
  congestion: CongestionLevel
  waiting_time_s: number
  vehicle_types: Record<string, number>
}

export type CongestionLevel = 'free' | 'light' | 'heavy' | 'jammed'

// Junction analytics the middleware passes through verbatim, so a metric added
// upstream needs no change here.
export interface JunctionMetrics {
  vehicles?: number
  queued_vehicles?: number
  queue_length_m?: number
  max_waiting_time_s?: number
  vehicle_types?: Record<string, number>
  worst_queue_ratio?: number
  green_speed_ratio?: number | null
  congestion?: CongestionLevel
  spillback?: boolean
  phase_index?: number
  state?: string
  phase_name?: string | null
  is_green_phase?: boolean
  time_in_phase_s?: number
  phase_duration_s?: number
  seconds_to_switch?: number
}

export interface LiveJunction {
  id: string
  status: 'live' | 'stale' | 'no-data'
  signalised: boolean
  lat: number
  lng: number
  phases?: Record<string, string>
  phase?: string | null
  time_to_change?: number | null
  program?: string | null
  hv_count?: number
  ev?: boolean
  age_s?: number
  source?: string
  metrics?: JunctionMetrics
  approaches?: LiveApproach[]
}

// Whole-network totals from the running simulation.
export interface NetworkStats {
  scenario?: string
  seq?: number
  sim_time_s?: number
  running_vehicles?: number
  halting_vehicles?: number
  mean_speed_ms?: number
  mean_speed_ratio?: number
  congestion?: CongestionLevel
  vehicle_types?: Record<string, number>
  departed_this_step?: number
  arrived_this_step?: number
  pending_insertions?: number
  expected_remaining?: number
  teleports_total?: number
  collisions_this_step?: number
  ts?: number
  source?: string
}

// /api/health — per-source liveness, as the middleware reports it.
export interface FeedHealth {
  ts: number
  sources: Record<string, {
    messages: number
    last_seen: number
    errors: number
    age_s: number | null
    status: 'ok' | 'down'
  }>
  unresolved_ids: Record<string, number>
  rejected: number
  network: { segments: number; junctions: number }
}

// One signal as the dashboard shows it: the canonical junction joined to its
// live state. Built by utils/liveLights.ts.
export interface LiveLight {
  id: string
  shortId: string
  name: string
  lat: number
  lng: number
  phase: 'green' | 'amber' | 'red' | 'off'
  secondsToChange: number | null
  program: string | null
  phaseIndex: number | null
  state: string | null
  vehicles: number
  queued: number
  queueLengthM: number
  queueFill: number // 0-1, the worst approach
  maxWaitS: number
  congestion: CongestionLevel
  spillback: boolean
  hvCount: number
  vehicleTypes: Record<string, number>
  approaches: LiveApproach[]
  status: 'live' | 'stale' | 'no-data'
  ageS: number
}

export interface LiveSnapshot {
  ts: number
  revision: number
  segments: LiveSegment[]
  junctions: LiveJunction[]
  network: NetworkStats | null
  stats: {
    segments_total: number
    segments_live: number
    segments_stale: number
    segments_no_data: number
    junctions_reporting: number
    rejected: number
  }
}

export type FeedStatus = 'idle' | 'connecting' | 'live' | 'offline'

export type RegionId = 'ito' | 'koramangala'

// A location the PRAVAH model proposes for new/upgraded infrastructure.
// Surfaced in Infrastructure Development Mode.
export interface SuggestedSite {
  id: string
  name: string
  kind: 'pravah-midroad' | 'new-signal' | 'corridor-sync' | 'camera-upgrade'
  lat: number
  lng: number
  score: number // 0-100 model priority score
  delaySaving: number // % reduction in average delay
  queueSaving: number // % reduction in peak queue length
  throughputGain: number // additional veh/h across the corridor
  affectedSegments: string[] // segment ids the site would relieve
  rationale: string
  status: 'recommended' | 'under-review' | 'planned'
  cost: string // indicative capex
}

export interface Alert {
  id: string
  type: 'anomaly' | 'warning' | 'info'
  tag: string
  time: string
  msg: string
  loc: string
  lat: number
  lng: number
}
