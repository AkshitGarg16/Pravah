import { create } from 'zustand'
import type { RegionId } from '../types'
import { regions } from '../data/regions'

export interface ViewState {
  longitude: number
  latitude: number
  zoom: number
}

// A fly-to request. `nonce` changes on every call so MapView re-triggers the
// animated flyTo even when the same coordinates are requested twice.
export interface FlyTarget {
  latitude: number
  longitude: number
  zoom: number
  nonce: number
}

export type Basemap = 'osm' | 'mapbox'

const DEFAULT_REGION: RegionId = 'ito'

/**
 * View state, selection and Infrastructure Development Mode.
 *
 * Everything the traffic feed reports — signals, congestion, health, alerts —
 * lives in useLiveStore and is derived from the simulation. This store holds
 * only what the *user* has done: where the map is pointed, what is selected,
 * which mode is open. It used to invent traffic too: a `tickTimers` action
 * cycled every light's phase and re-randomised its pressures once a second,
 * which is now the feed's job.
 */
interface DashboardState {
  region: RegionId
  selectedLightId: string | null
  searchQuery: string
  viewState: ViewState
  flyTarget: FlyTarget | null
  basemap: Basemap

  // Infrastructure Development Mode
  infraMode: boolean
  selectedSiteId: string | null
  scanning: boolean

  setRegion: (id: RegionId) => void
  selectLight: (id: string, lat: number, lng: number) => void
  setSearchQuery: (q: string) => void
  setViewState: (v: ViewState) => void
  setBasemap: (b: Basemap) => void
  flyTo: (lat: number, lng: number, zoom?: number) => void
  setInfraMode: (on: boolean) => void
  toggleInfraMode: () => void
  // Candidate sites are derived from the live network by toInfraSites, so the
  // caller passes the coordinates it already has rather than the store keeping
  // a second copy of the list to look them up in.
  selectSite: (id: string | null, lat?: number, lng?: number) => void
  rescanSites: () => void
}

const initial = regions[DEFAULT_REGION]

export const useDashboardStore = create<DashboardState>((set, get) => ({
  region: DEFAULT_REGION,
  selectedLightId: null,
  searchQuery: '',
  viewState: initial.center,
  flyTarget: null,
  basemap: 'osm',

  infraMode: false,
  selectedSiteId: null,
  scanning: false,

  setRegion: (id) => {
    const region = regions[id]
    set({
      region: id,
      selectedLightId: null,
      selectedSiteId: null,
      searchQuery: '',
      viewState: region.center,
    })
    get().flyTo(region.center.latitude, region.center.longitude, region.center.zoom)
  },

  selectLight: (id, lat, lng) => {
    set({ selectedLightId: id })
    get().flyTo(lat, lng, 17)
  },

  setSearchQuery: (q) => set({ searchQuery: q }),

  setViewState: (v) => set({ viewState: v }),

  setBasemap: (b) => set({ basemap: b }),

  flyTo: (lat, lng, zoom = 17) =>
    set({
      flyTarget: {
        latitude: lat,
        longitude: lng,
        zoom,
        nonce: Date.now(),
      },
    }),

  setInfraMode: (on) =>
    set({ infraMode: on, searchQuery: '', selectedSiteId: null, scanning: false }),

  toggleInfraMode: () => get().setInfraMode(!get().infraMode),

  selectSite: (id, lat, lng) => {
    set({ selectedSiteId: id })
    if (id !== null && lat !== undefined && lng !== undefined) {
      get().flyTo(lat, lng, 16.5)
    }
  },

  // The ranking is recomputed from the feed on every snapshot, so there is
  // nothing here to kick off -- this only runs the scanning affordance so the
  // button reads as doing something. It used to nudge each score by a random
  // few points, which made a live measurement look like a fresh model run.
  rescanSites: () => {
    if (get().scanning) return
    set({ scanning: true })
    setTimeout(() => set({ scanning: false }), 900)
  },
}))
