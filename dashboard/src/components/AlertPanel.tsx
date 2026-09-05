import { useMemo } from 'react'
import { Bell } from 'lucide-react'
import { useDashboardStore } from '../store/useDashboardStore'
import { useLiveStore } from '../store/useLiveStore'
import { toLiveLights } from '../utils/liveLights'
import { toLiveAlerts } from '../utils/liveAlerts'
import type { Alert } from '../types'

const leftBorder: Record<Alert['type'], string> = {
  anomaly: '#E4674F',
  warning: '#E8A33D',
  info: '#18A0A8',
}

const bgTint: Record<Alert['type'], string> = {
  anomaly: 'rgba(228, 103, 79, 0.05)',
  warning: 'rgba(232, 163, 61, 0.05)',
  info: 'rgba(24, 160, 168, 0.05)',
}

const bgTintHover: Record<Alert['type'], string> = {
  anomaly: 'rgba(228, 103, 79, 0.12)',
  warning: 'rgba(232, 163, 61, 0.12)',
  info: 'rgba(24, 160, 168, 0.12)',
}

function tagClass(tag: string): string {
  const t = tag.toLowerCase()
  if (t.includes('anomaly') || t.includes('hv') || t.includes('queue')) {
    return 'bg-coral/10 text-coral'
  }
  if (t.includes('system') || t.includes('camera') || t.includes('warn')) {
    return 'bg-amber/10 text-amber'
  }
  return 'bg-healthy/10 text-healthy'
}

export default function AlertPanel() {
  const network = useLiveStore((s) => s.network)
  const liveJunctions = useLiveStore((s) => s.junctions)
  const liveSegments = useLiveStore((s) => s.segments)
  const netStats = useLiveStore((s) => s.netStats)
  const flyTo = useDashboardStore((s) => s.flyTo)

  const alerts = useMemo(
    () =>
      toLiveAlerts(
        toLiveLights(network, liveJunctions),
        liveSegments,
        network?.segments ?? [],
        netStats,
      ),
    [network, liveJunctions, liveSegments, netStats],
  )

  return (
    <section className="flex-1 flex flex-col overflow-hidden">
      <div className="flex justify-between items-center px-3.5 pt-3.5 pb-2">
        <div className="flex items-center gap-1.5">
          <Bell size={14} className="text-muted" />
          <h2 className="text-[11px] uppercase font-semibold text-muted tracking-widest">
            Alerts
          </h2>
        </div>
        <span className="bg-coral text-white text-[10px] font-semibold px-2 py-0.5 rounded-full">
          {alerts.length}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto thin-scroll px-3.5 pb-3.5">
        {alerts.map((alert) => {
          const located = alert.lat !== 0 || alert.lng !== 0
          return (
          <div
            key={alert.id}
            onClick={() => located && flyTo(alert.lat, alert.lng, 17)}
            className={`p-2.5 mb-1.5 rounded-md transition-colors ${located ? 'cursor-pointer' : ''}`}
            style={{
              borderLeft: `3px solid ${leftBorder[alert.type]}`,
              backgroundColor: bgTint[alert.type],
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = bgTintHover[alert.type]
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = bgTint[alert.type]
            }}
          >
            <div className="flex justify-between items-center mb-1">
              <span
                className={`text-[9px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded ${tagClass(alert.tag)}`}
              >
                {alert.tag}
              </span>
              <span className="text-[10px] text-faint">{alert.time}</span>
            </div>
            <p className="text-[11px] text-ink leading-snug">{alert.msg}</p>
            {located ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  flyTo(alert.lat, alert.lng, 17)
                }}
                className="block text-[10px] text-teal font-medium mt-1 hover:underline"
              >
                View on map: {alert.loc}
              </button>
            ) : (
              <span className="block text-[10px] text-faint mt-1">{alert.loc}</span>
            )}
          </div>
          )
        })}
      </div>
    </section>
  )
}
