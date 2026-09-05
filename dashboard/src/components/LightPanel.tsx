import { useMemo } from 'react'
import { TrafficCone } from 'lucide-react'
import { useDashboardStore } from '../store/useDashboardStore'
import { useLiveStore } from '../store/useLiveStore'
import { toLiveLights } from '../utils/liveLights'
import LightCard from './LightCard'

export default function LightPanel() {
  const network = useLiveStore((s) => s.network)
  const liveJunctions = useLiveStore((s) => s.junctions)
  const feedStatus = useLiveStore((s) => s.status)

  const searchQuery = useDashboardStore((s) => s.searchQuery)
  const selectedLightId = useDashboardStore((s) => s.selectedLightId)
  const selectLight = useDashboardStore((s) => s.selectLight)

  const lights = useMemo(
    () => toLiveLights(network, liveJunctions),
    [network, liveJunctions],
  )

  const q = searchQuery.trim().toLowerCase()
  const filtered = q
    ? lights.filter(
        (l) => l.shortId.toLowerCase().includes(q) || l.name.toLowerCase().includes(q),
      )
    : lights

  return (
    <aside
      className="bg-card border-r border-edge overflow-y-auto thin-scroll h-full"
      style={{ padding: 14 }}
    >
      <div className="flex items-center justify-between mb-2.5">
        <div className="flex items-center gap-1.5">
          <TrafficCone size={14} className="text-muted" />
          <h2 className="text-[11px] uppercase font-semibold text-muted tracking-widest">
            Traffic Lights
          </h2>
        </div>
        <span className="text-[10px] text-faint tabular-nums">{lights.length}</span>
      </div>

      {lights.length === 0 ? (
        <p className="text-[11px] text-faint mt-4 text-center leading-relaxed">
          {feedStatus === 'live'
            ? 'No signals in the canonical network.'
            : 'Waiting for the feed — start the simulation and the bridge.'}
        </p>
      ) : filtered.length === 0 ? (
        <p className="text-[11px] text-faint mt-4 text-center">
          No lights match “{searchQuery}”.
        </p>
      ) : (
        filtered.map((light) => (
          <LightCard
            key={light.id}
            light={light}
            isActive={light.id === selectedLightId}
            onSelect={() => selectLight(light.id, light.lat, light.lng)}
          />
        ))
      )}
    </aside>
  )
}
