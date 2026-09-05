import { useMemo } from 'react'
import { Search } from 'lucide-react'
import { useDashboardStore } from '../store/useDashboardStore'
import { useLiveStore } from '../store/useLiveStore'
import { toLiveLights } from '../utils/liveLights'
import { regionList } from '../data/regions'
import type { RegionId } from '../types'

export default function SearchBar() {
  const searchQuery = useDashboardStore((s) => s.searchQuery)
  const setSearchQuery = useDashboardStore((s) => s.setSearchQuery)
  const network = useLiveStore((s) => s.network)
  const liveJunctions = useLiveStore((s) => s.junctions)
  const region = useDashboardStore((s) => s.region)
  const setRegion = useDashboardStore((s) => s.setRegion)

  const lightCount = useMemo(
    () => toLiveLights(network, liveJunctions).length,
    [network, liveJunctions],
  )

  return (
    <div
      className="bg-card border-b border-edge flex items-center gap-3 px-5"
      style={{ gridArea: 'search' }}
    >
      <label className="text-muted text-xs font-medium">Region</label>
      <select
        className="min-w-[200px] bg-surface border border-edge rounded-md px-3 py-1.5 text-sm outline-none focus:border-teal focus:ring-1 focus:ring-teal/20"
        value={region}
        onChange={(e) => setRegion(e.target.value as RegionId)}
      >
        {regionList.map((r) => (
          <option key={r.id} value={r.id}>
            {r.label}
          </option>
        ))}
      </select>

      <label className="text-muted text-xs font-medium">Junction</label>
      <div className="relative flex-1 max-w-xs">
        <Search
          size={14}
          className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
        />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search junction or segment ID..."
          className="w-full bg-surface border border-edge rounded-md pl-8 pr-3 py-1.5 text-sm outline-none focus:border-teal focus:ring-1 focus:ring-teal/20"
        />
      </div>

      <span className="ml-auto bg-teal text-white text-xs font-medium px-3 py-0.5 rounded-full">
        {lightCount} signals · {network?.segments.length ?? 0} segments
      </span>
    </div>
  )
}
