export function getCongestionColor(ratio: number): string {
  if (ratio < 0.25) return '#2FA98C'
  if (ratio < 0.5) return '#E8A33D'
  return '#E4674F'
}

export function getCongestionLabel(ratio: number): 'good' | 'moderate' | 'severe' {
  if (ratio < 0.25) return 'good'
  if (ratio < 0.5) return 'moderate'
  return 'severe'
}
