import { Sparkles, Layers, SlidersHorizontal } from 'lucide-react'
import { useDashboardStore } from '../store/useDashboardStore'
import { useLiveStore } from '../store/useLiveStore'
import { kindLabel, getPriority, priorityColor, priorityLabel } from '../utils/sites'
import type { SuggestedSite } from '../types'

const kinds: SuggestedSite['kind'][] = [
  'pravah-midroad',
  'new-signal',
  'corridor-sync',
  'camera-upgrade',
]

// Model inputs, in the order the ranker weights them.
const rankingInputs: [string, string][] = [
  ['Pressure imbalance', 'far vs near, per cycle'],
  ['Speed deficit', 'observed vs OSM free-flow'],
  ['Spillback frequency', 'queue overruns per peak hour'],
  ['Corridor coupling', 'offset drift between neighbours'],
  ['Deployment cost', 'capex per second of delay saved'],
]

// Right rail of the Traffic Light Suggestor.
export default function InfraSummary() {
  const sites = useDashboardStore((s) => s.sites)
  // Coverage is measured against the canonical network the feed reports on.
  const segments = useLiveStore((s) => s.network?.segments ?? [])
  const selectedSiteId = useDashboardStore((s) => s.selectedSiteId)

  const selected = sites.find((s) => s.id === selectedSiteId) ?? null
  const avgDelay = sites.length
    ? Math.round(sites.reduce((a, s) => a + s.delaySaving, 0) / sites.length)
    : 0
  const throughput = sites.reduce((a, s) => a + s.throughputGain, 0)
  const covered = new Set(sites.flatMap((s) => s.affectedSegments)).size
  const coverage = segments.length ? Math.round((covered / segments.length) * 100) : 0

  return (
    <>
      <section className="border-b border-edge" style={{ padding: 14 }}>
        <div className="flex items-center gap-1.5 mb-2">
          <Sparkles size={14} className="text-muted" />
          <h2 className="text-[11px] uppercase font-semibold text-muted tracking-widest">
            Model Output
          </h2>
        </div>

        <div className="grid grid-cols-2 gap-1.5">
          <Stat label="Candidate sites" value={String(sites.length)} />
          <Stat label="Avg delay saved" value={`−${avgDelay}%`} accent="#2FA98C" />
          <Stat label="Throughput gain" value={`+${throughput}/h`} accent="#2FA98C" />
          <Stat label="Segment coverage" value={`${coverage}%`} />
        </div>

        <div className="mt-2.5">
          {(['high', 'medium', 'low'] as const).map((p) => {
            const count = sites.filter((s) => getPriority(s.score) === p).length
            return (
              <div key={p} className="flex items-center justify-between py-0.5">
                <div className="flex items-center gap-1.5">
                  <span
                    className="rounded-full"
                    style={{ width: 7, height: 7, backgroundColor: priorityColor[p] }}
                  />
                  <span className="text-[11px] text-ink">{priorityLabel[p]}</span>
                </div>
                <span className="text-[11px] font-semibold text-ink tabular-nums">{count}</span>
              </div>
            )
          })}
        </div>
      </section>

      <section className="border-b border-edge" style={{ padding: 14 }}>
        <div className="flex items-center gap-1.5 mb-2">
          <Layers size={14} className="text-muted" />
          <h2 className="text-[11px] uppercase font-semibold text-muted tracking-widest">
            Intervention Mix
          </h2>
        </div>
        {kinds.map((k) => {
          const count = sites.filter((s) => s.kind === k).length
          return (
            <div
              key={k}
              className="flex justify-between items-center py-1.5"
              style={{ borderBottom: '1px solid #F0F3F6' }}
            >
              <span className="text-xs font-medium text-ink">{kindLabel[k]}</span>
              <span className="text-[11px] font-semibold text-ink tabular-nums">{count}</span>
            </div>
          )
        })}
      </section>

      <section className="flex-1 overflow-y-auto thin-scroll" style={{ padding: 14 }}>
        <div className="flex items-center gap-1.5 mb-2">
          <SlidersHorizontal size={14} className="text-muted" />
          <h2 className="text-[11px] uppercase font-semibold text-muted tracking-widest">
            {selected ? 'Selected Site' : 'How Sites Are Ranked'}
          </h2>
        </div>

        {selected ? (
          <div>
            <div className="text-xs font-semibold text-navy mb-0.5">{selected.name}</div>
            <div className="text-[10px] text-muted mb-2">
              {selected.id} · {kindLabel[selected.kind]}
            </div>
            <Row label="Model score" value={String(selected.score)} />
            <Row label="Priority" value={priorityLabel[getPriority(selected.score)]} />
            <Row label="Avg delay" value={`−${selected.delaySaving}%`} />
            <Row label="Peak queue" value={`−${selected.queueSaving}%`} />
            <Row label="Throughput" value={`+${selected.throughputGain} veh/h`} />
            <Row label="Est. capex" value={selected.cost} />
            <Row label="Status" value={selected.status.replace('-', ' ')} />
            <Row label="Relieves" value={selected.affectedSegments.join(', ')} />
            <p className="text-[10px] text-ink leading-snug mt-2 pt-2 border-t border-edge">
              {selected.rationale}
            </p>
          </div>
        ) : (
          <div>
            {rankingInputs.map(([name, detail]) => (
              <div key={name} className="py-1.5" style={{ borderBottom: '1px solid #F0F3F6' }}>
                <div className="text-xs font-medium text-ink">{name}</div>
                <div className="text-[10px] text-muted">{detail}</div>
              </div>
            ))}
            <p className="text-[10px] text-faint leading-snug mt-2">
              Scores are recomputed from the live GNSS, FCD and camera feeds joined onto the
              OSM road graph. Select a site for its full breakdown.
            </p>
          </div>
        )}
      </section>
    </>
  )
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="bg-surface border border-edge rounded-md px-2 py-1.5">
      <div
        className="text-sm font-semibold tabular-nums"
        style={{ color: accent ?? '#16222E' }}
      >
        {value}
      </div>
      <div className="text-[9px] uppercase tracking-wide text-faint">{label}</div>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center gap-2 py-1">
      <span className="text-[11px] text-muted shrink-0">{label}</span>
      <span className="text-[11px] font-semibold text-ink text-right capitalize">{value}</span>
    </div>
  )
}
