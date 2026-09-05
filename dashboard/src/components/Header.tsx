import { useEffect, useState } from 'react'
import { format } from 'date-fns'
import { HardHat, X } from 'lucide-react'
import { useDashboardStore } from '../store/useDashboardStore'
import { useLiveStore } from '../store/useLiveStore'

export default function Header() {
  const [now, setNow] = useState(() => new Date())
  const infraMode = useDashboardStore((s) => s.infraMode)
  const toggleInfraMode = useDashboardStore((s) => s.toggleInfraMode)
  const netStats = useLiveStore((s) => s.netStats)
  const feedStatus = useLiveStore((s) => s.status)

  const halting = netStats?.halting_vehicles ?? 0
  const running = netStats?.running_vehicles ?? 0
  const haltingPct = running > 0 ? Math.round((halting / running) * 100) : 0

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <header
      className="bg-navy flex items-center justify-between px-4"
      style={{ gridArea: 'header' }}
    >
      {/* Left group */}
      <div className="flex items-center gap-2.5">
        <div className="w-7 h-7 rounded-md bg-teal flex items-center justify-center">
          <span className="text-white font-bold" style={{ fontSize: 13 }}>
            P
          </span>
        </div>
        <div className="tracking-wide" style={{ fontSize: 15 }}>
          <span className="text-white font-semibold">PRAVAH</span>{' '}
          <span className="text-mint font-semibold">Dashboard</span>
        </div>

        <button
          type="button"
          onClick={toggleInfraMode}
          title="Traffic light suggestor — where the model recommends new infrastructure"
          className={`ml-3 flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-semibold transition-colors border ${
            infraMode
              ? 'bg-purple text-white border-purple'
              : 'bg-white/5 text-faint border-white/15 hover:bg-white/10 hover:text-white'
          }`}
        >
          {infraMode ? <X size={12} /> : <HardHat size={12} />}
          {infraMode ? 'Exit Infra Mode' : 'Infrastructure Development Mode'}
        </button>
      </div>

      {/* Right group: the simulation's own totals, so the state of the network
          is legible without reading the map. */}
      <div className="flex items-center gap-4">
        {netStats && (
          <div className="flex items-center gap-3.5 tabular-nums" style={{ fontSize: 12 }}>
            <Stat label="vehicles" value={`${running}`} />
            <Stat
              label="mean speed"
              value={`${Math.round((netStats.mean_speed_ms ?? 0) * 3.6)} km/h`}
            />
            <Stat label="stopped" value={`${haltingPct}%`} warn={haltingPct >= 25} />
            <Stat
              label="teleports"
              value={`${netStats.teleports_total ?? 0}`}
              warn={(netStats.teleports_total ?? 0) > 0}
            />
            <Stat label="sim clock" value={`t+${Math.round(netStats.sim_time_s ?? 0)}s`} />
          </div>
        )}
        <div className="flex items-center gap-2">
          <div
            className={`rounded-full ${feedStatus === 'live' ? 'bg-healthy animate-pulse-dot' : feedStatus === 'connecting' ? 'bg-amber' : 'bg-coral'}`}
            style={{ width: 7, height: 7 }}
          />
          <span className="text-faint" style={{ fontSize: 12 }}>
            {feedStatus === 'live' ? 'Feed live' : feedStatus === 'connecting' ? 'Connecting' : 'Feed offline'}
          </span>
        </div>
        <span className="text-faint tabular-nums" style={{ fontSize: 12 }}>
          {format(now, 'HH:mm:ss')}
        </span>
      </div>
    </header>
  )
}

function Stat({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <span className="flex items-baseline gap-1">
      <span className={warn ? 'text-amber font-semibold' : 'text-white font-semibold'}>
        {value}
      </span>
      <span className="text-faint" style={{ fontSize: 10 }}>
        {label}
      </span>
    </span>
  )
}
