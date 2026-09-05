import type { SuggestedSite } from '../types'
import { getPriority, priorityColor, priorityLabel, kindLabel } from '../utils/sites'

interface SiteCardProps {
  site: SuggestedSite
  rank: number
  isActive: boolean
  onSelect: () => void
}

const statusBadge: Record<SuggestedSite['status'], string> = {
  recommended: 'bg-healthy/10 text-healthy',
  'under-review': 'bg-amber/10 text-amber',
  planned: 'bg-teal/10 text-teal',
}

export default function SiteCard({ site, rank, isActive, onSelect }: SiteCardProps) {
  const priority = getPriority(site.score)
  const color = priorityColor[priority]

  return (
    <div
      onClick={onSelect}
      className={`bg-surface rounded-lg p-2.5 mb-2 cursor-pointer transition-[border-color,box-shadow] hover:shadow-sm ${
        isActive ? 'border-2' : 'border border-edge'
      }`}
      style={isActive ? { borderColor: color } : undefined}
    >
      {/* Row 1: rank + name + priority */}
      <div className="flex justify-between items-start gap-2 mb-1">
        <div className="flex items-center gap-1.5 min-w-0">
          <span
            className="rounded-full flex items-center justify-center text-white font-semibold shrink-0"
            style={{ width: 16, height: 16, fontSize: 9, backgroundColor: color }}
          >
            {rank}
          </span>
          <span className="text-xs font-semibold text-navy truncate">{site.name}</span>
        </div>
        <span
          className="text-[9px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded-full shrink-0"
          style={{ backgroundColor: `${color}1A`, color }}
        >
          {priorityLabel[priority]}
        </span>
      </div>

      <div className="text-[10px] text-muted mb-2">
        {site.id} · {kindLabel[site.kind]}
      </div>

      {/* Model score bar */}
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[10px] text-muted w-9 shrink-0">Score</span>
        <div className="flex-1 h-1.5 rounded-full bg-edge overflow-hidden">
          <div
            className="h-full rounded-full transition-[width] duration-500"
            style={{ width: `${site.score}%`, backgroundColor: color }}
          />
        </div>
        <span className="text-[10px] font-semibold tabular-nums w-6 text-right" style={{ color }}>
          {site.score}
        </span>
      </div>

      {/* Predicted impact */}
      <div className="grid grid-cols-3 gap-1 mb-2">
        <Metric label="Avg delay" value={`−${site.delaySaving}%`} />
        <Metric label="Peak queue" value={`−${site.queueSaving}%`} />
        <Metric label="Throughput" value={`+${site.throughputGain}`} />
      </div>

      <p className="text-[10px] text-ink leading-snug">{site.rationale}</p>

      {/* Footer: affected segments + status + cost */}
      <div className="flex flex-wrap gap-1 mt-2">
        {site.affectedSegments.map((id) => (
          <span
            key={id}
            className="text-[9px] font-medium text-muted bg-card border border-edge rounded px-1.5 py-0.5"
          >
            {id}
          </span>
        ))}
      </div>
      <div className="flex justify-between items-center mt-1.5 pt-1.5 border-t border-edge">
        <span
          className={`text-[9px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded ${statusBadge[site.status]}`}
        >
          {site.status.replace('-', ' ')}
        </span>
        <span className="text-[10px] text-muted">
          Est. capex <span className="font-semibold text-ink">{site.cost}</span>
        </span>
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-card border border-edge rounded px-1 py-1 text-center">
      <div className="text-[11px] font-semibold text-healthy tabular-nums">{value}</div>
      <div className="text-[8px] uppercase tracking-wide text-faint">{label}</div>
    </div>
  )
}
