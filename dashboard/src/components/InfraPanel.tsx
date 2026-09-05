import { MapPin } from 'lucide-react'
import { useDashboardStore } from '../store/useDashboardStore'
import SiteCard from './SiteCard'

// Left rail of the Traffic Light Suggestor: ranked candidate sites.
export default function InfraPanel() {
  const sites = useDashboardStore((s) => s.sites)
  const searchQuery = useDashboardStore((s) => s.searchQuery)
  const selectedSiteId = useDashboardStore((s) => s.selectedSiteId)
  const selectSite = useDashboardStore((s) => s.selectSite)
  const scanning = useDashboardStore((s) => s.scanning)

  const q = searchQuery.trim().toLowerCase()
  const filtered = q
    ? sites.filter(
        (s) => s.id.toLowerCase().includes(q) || s.name.toLowerCase().includes(q),
      )
    : sites

  return (
    <aside
      className="bg-card border-r border-edge overflow-y-auto thin-scroll h-full"
      style={{ padding: 14 }}
    >
      <div className="flex items-center gap-1.5 mb-1">
        <MapPin size={14} className="text-muted" />
        <h2 className="text-[11px] uppercase font-semibold text-muted tracking-widest">
          Candidate Sites
        </h2>
      </div>
      <p className="text-[10px] text-faint leading-snug mb-2.5">
        Ranked by predicted network gain. Click a site to ping it on the map.
      </p>

      {scanning && (
        <div className="text-[10px] text-purple font-medium mb-2">
          Re-scoring against the live feed…
        </div>
      )}

      {filtered.length === 0 ? (
        <p className="text-[11px] text-faint mt-4 text-center">
          No candidate sites match “{searchQuery}”.
        </p>
      ) : (
        filtered.map((site) => (
          <SiteCard
            key={site.id}
            site={site}
            rank={sites.indexOf(site) + 1}
            isActive={site.id === selectedSiteId}
            onSelect={() => selectSite(site.id === selectedSiteId ? null : site.id)}
          />
        ))
      )}
    </aside>
  )
}
