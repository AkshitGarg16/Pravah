import type { LiveLight } from '../types'
import PressureBar from './PressureBar'
import IndicatorPill from './IndicatorPill'
import { CONGESTION_COLOR, PHASE_COLOR } from '../utils/liveLights'

interface LightCardProps {
  light: LiveLight
  isActive: boolean
  onSelect: () => void
}

const phaseBadge: Record<LiveLight['phase'], string> = {
  green: 'bg-healthy/10 text-healthy',
  red: 'bg-coral/10 text-coral',
  amber: 'bg-amber/10 text-amber',
  off: 'bg-gray-100 text-faint',
}

// Two-wheelers behave nothing like cars in a queue, so the mix is worth showing
// rather than collapsing into a single vehicle count.
const TYPE_LABEL: Record<string, string> = {
  car: 'car',
  twowheeler: '2W',
  bus: 'bus',
  truck: 'truck',
}

export default function LightCard({ light, isActive, onSelect }: LightCardProps) {
  const silent = light.status === 'no-data'
  const types = Object.entries(light.vehicleTypes).filter(([, n]) => n > 0)

  return (
    <div
      onClick={onSelect}
      className={`bg-surface rounded-lg p-2.5 mb-2 cursor-pointer transition-[border-color,box-shadow] hover:border-teal hover:shadow-sm hover:shadow-teal/20 ${
        isActive ? 'border-2 border-teal' : 'border border-edge'
      }`}
      style={{ opacity: silent ? 0.6 : 1 }}
    >
      {/* Row 1: id + live phase */}
      <div className="flex justify-between items-center mb-1.5">
        <span className="text-xs font-semibold text-navy truncate">{light.shortId}</span>
        <span
          className={`text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full ${phaseBadge[light.phase]}`}
        >
          {silent ? 'silent' : light.phase}
        </span>
      </div>

      {/* Row 2: name + program */}
      <div className="text-[10px] text-muted mb-2 truncate">
        {light.name}
        {light.program ? ` · program ${light.program}` : ''}
        {light.phaseIndex !== null ? ` · phase ${light.phaseIndex}` : ''}
      </div>

      {silent ? (
        <div className="text-[11px] text-faint">No signal state reported.</div>
      ) : (
        <>
          {/* Queue fill is the junction's congestion measure: a signalised
              approach is meant to be stopped part of every cycle, so what
              matters is whether the queue clears, not how fast it moves. */}
          <PressureBar
            label="Queue"
            value={light.queueFill * 100}
            color={CONGESTION_COLOR[light.congestion]}
            display={`${Math.round(light.queueFill * 100)}%`}
          />
          <PressureBar
            label="Wait"
            value={Math.min(100, (light.maxWaitS / 120) * 100)}
            color={light.maxWaitS >= 60 ? '#E4674F' : '#18A0A8'}
            display={`${Math.round(light.maxWaitS)}s`}
          />

          <div className="flex justify-between items-center text-[10px] text-muted mt-1">
            <span>
              {light.vehicles} veh · {light.queued} stopped
            </span>
            <span>{Math.round(light.queueLengthM)} m queued</span>
          </div>

          {/* Row: footer */}
          <div className="flex justify-between items-center mt-1.5 pt-1.5 border-t border-edge">
            <span className="text-[11px] font-semibold text-navy">
              Next change:{' '}
              <span className="text-teal tabular-nums">
                {light.secondsToChange === null ? '—' : `${Math.max(0, Math.round(light.secondsToChange))}s`}
              </span>
            </span>
            <span
              className="text-[10px] font-semibold capitalize"
              style={{ color: CONGESTION_COLOR[light.congestion] }}
            >
              {light.congestion}
            </span>
          </div>

          {/* Row: indicators */}
          {(light.spillback || light.hvCount > 0 || types.length > 0) && (
            <div className="flex flex-wrap gap-1.5 mt-1.5 items-center">
              {light.spillback && (
                <span className="inline-flex items-center rounded bg-coral/10 px-1.5 py-0.5 text-[9px] font-semibold text-coral">
                  SPILLBACK
                </span>
              )}
              {light.hvCount > 0 && (
                <IndicatorPill variant="hv" label={`${light.hvCount} heavy`} />
              )}
              {types.map(([type, n]) => (
                <span
                  key={type}
                  className="text-[9px] text-muted bg-white border border-edge rounded px-1.5 py-0.5"
                >
                  {n} {TYPE_LABEL[type] ?? type}
                </span>
              ))}
            </div>
          )}

          {/* Per-approach detail, worst first: which arm of the junction is the
              problem is the first question anyone asks of a queue number. */}
          {light.approaches.length > 0 && (
            <div className="mt-1.5 pt-1.5 border-t border-edge">
              {[...light.approaches]
                .sort((a, b) => b.queue_ratio - a.queue_ratio)
                .slice(0, 3)
                .map((a) => (
                  <div key={a.lane_id} className="flex items-center gap-1.5 text-[9px] mb-0.5">
                    <span
                      className="rounded-full shrink-0"
                      style={{ width: 6, height: 6, backgroundColor: PHASE_COLOR[a.phase] }}
                    />
                    <span className="text-muted truncate flex-1">{a.lane_id}</span>
                    <span className="text-faint tabular-nums shrink-0">
                      {a.halting_vehicles} q · {Math.round(a.queue_ratio * 100)}%
                    </span>
                  </div>
                ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
