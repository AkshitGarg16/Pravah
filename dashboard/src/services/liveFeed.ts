import type { PravahNetwork, LiveSnapshot, FeedHealth } from '../types'

const API = (import.meta.env.VITE_PRAVAH_API as string | undefined) ?? 'http://localhost:8000'

// Canonical topology. Served by the middleware; falls back to the bundled OSM
// extract so the map always draws real roads, never invented ones.
export async function loadNetwork(): Promise<{ network: PravahNetwork; from: 'api' | 'bundle' }> {
  try {
    const res = await fetch(`${API}/api/network`, { signal: AbortSignal.timeout(4000) })
    if (res.ok) return { network: await res.json(), from: 'api' }
  } catch {
    /* middleware not up — use the bundled extract */
  }
  const res = await fetch('/network.json')
  if (!res.ok) throw new Error('no canonical network available')
  return { network: await res.json(), from: 'bundle' }
}

// Per-source liveness, staleness counters and any ids the middleware could not
// resolve. Polled rather than streamed: it describes the pipeline, not the
// traffic, and changes far more slowly than a snapshot.
export async function fetchHealth(): Promise<FeedHealth> {
  const res = await fetch(`${API}/api/health`, { signal: AbortSignal.timeout(4000) })
  if (!res.ok) throw new Error(`health ${res.status}`)
  return res.json()
}

export interface FeedHandlers {
  onNetwork?: (network: PravahNetwork) => void
  onState: (snapshot: LiveSnapshot) => void
  onStatus: (status: 'connecting' | 'live' | 'offline') => void
}

// WebSocket client with exponential backoff. Returns a disposer.
export function connectFeed(handlers: FeedHandlers): () => void {
  const url = API.replace(/^http/, 'ws') + '/ws/state'
  let ws: WebSocket | null = null
  let retry = 1000
  let timer: number | undefined
  let closed = false

  const open = () => {
    if (closed) return
    handlers.onStatus('connecting')
    ws = new WebSocket(url)

    ws.onopen = () => {
      retry = 1000
      handlers.onStatus('live')
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'network') handlers.onNetwork?.(msg.payload)
        else if (msg.type === 'state') handlers.onState(msg.payload)
      } catch {
        /* ignore malformed frames */
      }
    }

    ws.onerror = () => ws?.close()

    ws.onclose = () => {
      if (closed) return
      handlers.onStatus('offline')
      timer = window.setTimeout(open, retry)
      retry = Math.min(retry * 2, 15000)
    }
  }

  open()

  return () => {
    closed = true
    if (timer) window.clearTimeout(timer)
    ws?.close()
  }
}
