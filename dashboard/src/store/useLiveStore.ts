import { create } from 'zustand'
import type {
  PravahNetwork,
  LiveSegment,
  LiveJunction,
  LiveSnapshot,
  NetworkStats,
  FeedHealth,
  FeedStatus,
} from '../types'
import { loadNetwork, connectFeed, fetchHealth } from '../services/liveFeed'

interface LiveState {
  network: PravahNetwork | null
  networkSource: 'api' | 'bundle' | null
  segments: Record<string, LiveSegment>
  junctions: Record<string, LiveJunction>
  stats: LiveSnapshot['stats'] | null
  netStats: NetworkStats | null
  health: FeedHealth | null
  status: FeedStatus
  lastUpdate: number | null

  init: () => () => void
  applySnapshot: (snap: LiveSnapshot) => void
}

// How often to ask the middleware how its own feeds are doing. Slower than the
// snapshot rate on purpose: liveness changes on the scale of seconds, and this
// is a separate HTTP round trip rather than part of the socket stream.
const HEALTH_INTERVAL_MS = 5000

export const useLiveStore = create<LiveState>((set) => ({
  network: null,
  networkSource: null,
  segments: {},
  junctions: {},
  stats: null,
  netStats: null,
  health: null,
  status: 'idle',
  lastUpdate: null,

  applySnapshot: (snap) =>
    set({
      segments: Object.fromEntries(snap.segments.map((s) => [s.id, s])),
      junctions: Object.fromEntries(snap.junctions.map((j) => [j.id, j])),
      lastUpdate: snap.ts * 1000,
      stats: snap.stats,
      netStats: snap.network ?? null,
    }),

  init: () => {
    loadNetwork()
      .then(({ network, from }) => set({ network, networkSource: from }))
      .catch(() => set({ network: null, networkSource: null }))

    const poll = () =>
      fetchHealth()
        .then((health) => set({ health }))
        .catch(() => set({ health: null }))
    poll()
    const healthTimer = window.setInterval(poll, HEALTH_INTERVAL_MS)

    const disconnect = connectFeed({
      onNetwork: (network) => set({ network, networkSource: 'api' }),
      onState: (snap) => useLiveStore.getState().applySnapshot(snap),
      onStatus: (status) => set({ status }),
    })

    return () => {
      window.clearInterval(healthTimer)
      disconnect()
    }
  },
}))
