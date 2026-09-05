interface PressureBarProps {
  label: string
  value: number // 0-100, the bar's fill
  color: string
  /** What to print at the end of the row. Defaults to the rounded value. */
  display?: string
}

export default function PressureBar({ label, value, color, display }: PressureBarProps) {
  const clamped = Math.max(0, Math.min(100, value))

  return (
    <div className="flex items-center gap-2 mb-1">
      <span className="w-10 shrink-0 text-[10px] text-muted">{label}</span>
      <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${clamped}%`, backgroundColor: color }}
        />
      </div>
      <span
        className="w-12 shrink-0 text-right text-[10px] font-semibold tabular-nums"
        style={{ color }}
      >
        {display ?? Math.round(clamped)}
      </span>
    </div>
  )
}
