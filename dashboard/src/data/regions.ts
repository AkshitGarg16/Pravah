import type { RegionId, TrafficLight, Segment, HealthSource, Alert, SuggestedSite } from '../types'
import { lights, segments, health, alerts } from './mockData'
import { itoLights, itoSegments, itoHealth, itoAlerts } from './itoData'
import { itoSites, koramangalaSites } from './suggestions'

export interface RegionDef {
  id: RegionId
  label: string
  center: { longitude: number; latitude: number; zoom: number }
  lights: TrafficLight[]
  segments: Segment[]
  health: HealthSource[]
  alerts: Alert[]
  sites: SuggestedSite[]
}

export const regions: Record<RegionId, RegionDef> = {
  ito: {
    id: 'ito',
    label: 'ITO Junction, New Delhi',
    // Centre of the SUMO network, not of the old OSM extract: the two are
    // about 2 km apart and the simulated roads are the eastern set.
    center: { longitude: 77.2613, latitude: 28.6298, zoom: 13.6 },
    lights: itoLights,
    segments: itoSegments,
    health: itoHealth,
    alerts: itoAlerts,
    sites: itoSites,
  },
  koramangala: {
    id: 'koramangala',
    label: 'Koramangala, Bengaluru',
    center: { longitude: 77.622, latitude: 12.933, zoom: 15 },
    lights,
    segments,
    health,
    alerts,
    sites: koramangalaSites,
  },
}

export const regionList = [regions.ito, regions.koramangala]
