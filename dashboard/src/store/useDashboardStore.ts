import { create } from 'zustand'
import type { RegionId, SuggestedSite } from '../types'
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
  sites: SuggestedSite[]
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
  selectSite: (id: string | null) => void
  rescanSites: () => void
}

const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min

const clamp = (n: number, min: number, max: number) => Math.min(max, Math.max(min, n))

const byScore = (a: SuggestedSite, b: SuggestedSite) => b.score - a.score

const initial = regions[DEFAULT_REGION]

export const useDashboardStore = create<DashboardState>((set, get) => ({
  region: DEFAULT_REGION,
  sites: [...initial.sites].sort(byScore),
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
      sites: [...region.sites].sort(byScore),
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

  selectSite: (id) => {
    set({ selectedSiteId: id })
    const site = get().sites.find((s) => s.id === id)
    if (site) {
      get().flyTo(site.lat, site.lng, 16.5)
    }
  },

  // Re-runs the ranker against the current feed. Scores drift slightly because
  // the live pressure/speed inputs have moved since the last pass.
  rescanSites: () => {
    if (get().scanning) return
    set({ scanning: true })
    setTimeout(() => {
      set((state) => ({
        scanning: false,
        sites: state.sites
          .map((s) => ({ ...s, score: clamp(s.score + rand(-4, 4), 10, 99) }))
          .sort(byScore),
      }))
    }, 1400)
  },
}))
