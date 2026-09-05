import { useMemo } from 'react'
import { Sparkles, Layers, SlidersHorizontal } from 'lucide-react'
import { useDashboardStore } from '../store/useDashboardStore'
import { useLiveStore } from '../store/useLiveStore'
import { toInfraSites } from '../utils/infraSites'
import { kindLabel, getPriority, priorityColor, priorityLabel } from '../utils/sites'
import type { SuggestedSite } from '../types'

const kinds: SuggestedSite['kind'][] = [
  'pravah-midroad',
  'new-signal',
  'corridor-sync',
  'camera-upgrade',
]

// What the ranker actually reads, in the order it weights them. Every one is a
// measured quantity off the live feed -- there is no model behind this, and the
// numbers on each card are the size of the problem, not a predicted saving.
const rankingInputs: [string, string][] = [
  ['Speed deficit', 'observed speed vs the segment limit (70%)'],
  ['Road occupancy', 'how full the segment is, spillback included (20%)'],
  ['Road width', 'lanes going to waste while it is blocked (10%)'],
  ['Downstream junction', 'sets the intervention: re-sync, new signal or mid-road'],
  ['Segment length', 'anything under 40 m has no room for a unit'],
]

// Right rail of the Traffic Light Suggestor.
export default function InfraSummary() {
  const network = useLiveStore((s) => s.network)
  const liveSegments = useLiveStore((s) => s.segments)
  const sites = useMemo(() => toInfraSites(network, liveSegments), [network, liveSegments])
  // Coverage is measured against the canonical network the feed reports on.
  const segments = network?.segments ?? []
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
          <Stat label="Avg speed deficit" value={`${avgDelay}%`} accent="#E4674F" />
          <Stat label="Capacity shortfall" value={`${throughput}/h`} accent="#E4674F" />
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
            <Row label="Site score" value={String(selected.score)} />
            <Row label="Priority" value={priorityLabel[getPriority(selected.score)]} />
            <Row label="Speed deficit" value={`${selected.delaySaving}%`} />
            <Row label="Road occupied" value={`${selected.queueSaving}%`} />
            <Row label="Capacity short" value={`${selected.throughputGain} veh/h`} />
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
              Recomputed from the simulation feed on every snapshot, so the ranking moves
              as the network congests. Select a site for its full breakdown.
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
