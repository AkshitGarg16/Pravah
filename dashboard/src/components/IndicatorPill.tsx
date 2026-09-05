import { Truck, Siren } from 'lucide-react'

interface IndicatorPillProps {
  variant: 'hv' | 'ev'
  label: string
}

export default function IndicatorPill({ variant, label }: IndicatorPillProps) {
  if (variant === 'hv') {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-purple/10 px-1.5 py-0.5 text-[9px] font-semibold text-purple">
        <Truck size={10} strokeWidth={2.25} />
        {label}
      </span>
    )
  }

  return (
    <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[9px] font-semibold text-coral animate-ev-pulse">
      <Siren size={10} strokeWidth={2.25} className="animate-ev-dot" />
      {label}
    </span>
  )
}
