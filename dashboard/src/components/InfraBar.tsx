import { RefreshCw, Radar } from 'lucide-react'
import { useDashboardStore } from '../store/useDashboardStore'
import { regionList } from '../data/regions'
import type { RegionId } from '../types'

// Replaces SearchBar while Infrastructure Development Mode is on.
export default function InfraBar() {
  const region = useDashboardStore((s) => s.region)
  const setRegion = useDashboardStore((s) => s.setRegion)
  const sites = useDashboardStore((s) => s.sites)
  const searchQuery = useDashboardStore((s) => s.searchQuery)
  const setSearchQuery = useDashboardStore((s) => s.setSearchQuery)
  const scanning = useDashboardStore((s) => s.scanning)
  const rescanSites = useDashboardStore((s) => s.rescanSites)

  const totalDelay = sites.reduce((acc, s) => acc + s.delaySaving, 0)

  return (
    <div
      className="bg-card border-b border-edge flex items-center gap-3 px-5"
      style={{ gridArea: 'search' }}
    >
      <span className="flex items-center gap-1.5 bg-purple/10 text-purple text-[11px] font-semibold px-2.5 py-1 rounded-md">
        <Radar size={12} className={scanning ? 'animate-spin-slow' : undefined} />
        Traffic Light Suggestor
      </span>

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

      <input
        type="text"
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        placeholder="Filter candidate sites..."
        className="w-56 bg-surface border border-edge rounded-md px-3 py-1.5 text-sm outline-none focus:border-teal focus:ring-1 focus:ring-teal/20"
      />

      <button
        type="button"
        onClick={rescanSites}
        disabled={scanning}
        className="flex items-center gap-1.5 border border-edge rounded-md px-2.5 py-1.5 text-xs font-medium text-ink hover:border-teal hover:text-teal transition-colors disabled:opacity-60"
      >
        <RefreshCw size={12} className={scanning ? 'animate-spin-slow' : undefined} />
        {scanning ? 'Scanning…' : 'Re-run scan'}
      </button>

      <span className="ml-auto bg-purple text-white text-xs font-medium px-3 py-0.5 rounded-full">
        {sites.length} candidate sites · −{totalDelay}% combined delay
      </span>
    </div>
  )
}
