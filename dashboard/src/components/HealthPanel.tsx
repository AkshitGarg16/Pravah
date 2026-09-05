import { useMemo } from 'react'
import { Activity } from 'lucide-react'
import { useLiveStore } from '../store/useLiveStore'
import type { HealthSource } from '../types'
import StatusDot from './StatusDot'

/**
 * What the pipeline itself is doing, as opposed to what the traffic is doing.
 *
 * Three rows, one per hop: the simulation feeding the middleware, the
 * middleware's own view of its network, and the browser's socket to it. When
 * the dashboard shows nothing, the row that has gone amber says which hop to
 * look at.
 */
export default function HealthPanel() {
  const health = useLiveStore((s) => s.health)
  const stats = useLiveStore((s) => s.stats)
  const netStats = useLiveStore((s) => s.netStats)
  const status = useLiveStore((s) => s.status)
  const lastUpdate = useLiveStore((s) => s.lastUpdate)

  const rows = useMemo<HealthSource[]>(() => {
    const out: HealthSource[] = []

    const sumo = health?.sources?.sumo
    out.push({
      source: 'SUMO feed',
      status: !sumo ? 'err' : sumo.status === 'ok' ? 'ok' : 'warn',
      detail: sumo ? `${sumo.messages.toLocaleString()} msgs` : 'no data',
      extra: sumo?.age_s != null ? `${sumo.age_s.toFixed(1)}s ago` : 'bridge not running',
    })

    // Unresolved ids mean the feed is naming roads the canonical network does
    // not have — the failure the SUMO-derived network exists to make impossible.
    const unresolved = Object.keys(health?.unresolved_ids ?? {}).length
    out.push({
      source: 'Middleware',
      status: !stats ? 'err' : stats.segments_no_data > 0 || unresolved > 0 ? 'warn' : 'ok',
      detail: stats ? `${stats.segments_live}/${stats.segments_total} live` : 'no snapshot',
      extra: stats
        ? `${stats.segments_stale} stale · ${stats.rejected} rejected${unresolved ? ` · ${unresolved} unresolved` : ''}`
        : 'not reachable',
    })

    out.push({
      source: 'Dashboard link',
      status: status === 'live' ? 'ok' : status === 'connecting' ? 'warn' : 'err',
      detail: status,
      extra: lastUpdate
        ? `${Math.max(0, Math.round((Date.now() - lastUpdate) / 1000))}s ago`
        : 'awaiting first snapshot',
    })

    // A teleport is SUMO giving up on a vehicle that has been stuck too long,
    // so a non-zero count means gridlock rather than a slow patch.
    out.push({
      source: 'Simulation',
      status: !netStats ? 'err' : (netStats.teleports_total ?? 0) > 0 ? 'warn' : 'ok',
      detail: netStats?.sim_time_s != null ? `t+${Math.round(netStats.sim_time_s)}s` : 'idle',
      extra: netStats
        ? `${netStats.running_vehicles ?? 0} veh · ${netStats.teleports_total ?? 0} teleports`
        : 'no simulation running',
    })

    return out
  }, [health, stats, netStats, status, lastUpdate])

  return (
    <section className="border-b border-edge" style={{ padding: 14 }}>
      <div className="flex items-center gap-1.5 mb-2">
        <Activity size={14} className="text-muted" />
        <h2 className="text-[11px] uppercase font-semibold text-muted tracking-widest">
          System Health
        </h2>
      </div>

      <div>
        {rows.map((h, i) => (
          <div
            key={h.source}
            className="flex justify-between items-center py-1.5 gap-2"
            style={{
              borderBottom: i < rows.length - 1 ? '1px solid #F0F3F6' : 'none',
            }}
          >
            <div className="flex items-center gap-2 shrink-0">
              <StatusDot status={h.status} />
              <span className="text-xs font-medium text-ink">{h.source}</span>
            </div>
            <div className="text-right text-[10px] text-muted truncate">
              <span className="font-semibold text-ink">{h.detail}</span> · {h.extra}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
