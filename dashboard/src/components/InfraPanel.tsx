import { useMemo } from 'react'
import { MapPin } from 'lucide-react'
import { useDashboardStore } from '../store/useDashboardStore'
import { useLiveStore } from '../store/useLiveStore'
import { toInfraSites } from '../utils/infraSites'
import SiteCard from './SiteCard'

// Left rail of the Traffic Light Suggestor: ranked candidate sites.
export default function InfraPanel() {
  const network = useLiveStore((s) => s.network)
  const liveSegments = useLiveStore((s) => s.segments)
  const feedStatus = useLiveStore((s) => s.status)
  const sites = useMemo(() => toInfraSites(network, liveSegments), [network, liveSegments])

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
        One candidate per road, placed at the segment's midpoint and ranked by how
        far below its limit that road is running. Click a site to ping it on the map.
      </p>

      {scanning && (
        <div className="text-[10px] text-purple font-medium mb-2">
          Re-scoring against the live feed…
        </div>
      )}

      {sites.length === 0 ? (
        <p className="text-[11px] text-faint mt-4 text-center leading-relaxed">
          {feedStatus === 'live'
            ? 'No segments long enough to site a unit on.'
            : 'Waiting for the feed — start the simulation and the bridge.'}
        </p>
      ) : filtered.length === 0 ? (
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
            onSelect={() =>
              site.id === selectedSiteId
                ? selectSite(null)
                : selectSite(site.id, site.lat, site.lng)
            }
          />
        ))
      )}
    </aside>
  )
}
