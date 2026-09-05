import type { HealthSource } from '../types'

const statusColor: Record<HealthSource['status'], string> = {
  ok: '#2FA98C',
  warn: '#E8A33D',
  err: '#E4674F',
}

interface StatusDotProps {
  status: HealthSource['status']
  size?: number
}

export default function StatusDot({ status, size = 8 }: StatusDotProps) {
  return (
    <div
      className="rounded-full shrink-0"
      style={{
        width: size,
        height: size,
        backgroundColor: statusColor[status],
      }}
    />
  )
}
