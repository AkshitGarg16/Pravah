import type { SuggestedSite } from '../types'

export const kindLabel: Record<SuggestedSite['kind'], string> = {
  'pravah-midroad': 'PRAVAH mid-road unit',
  'new-signal': 'New signalised junction',
  'corridor-sync': 'Corridor re-sync',
  'camera-upgrade': 'Camera upgrade',
}

export const kindShort: Record<SuggestedSite['kind'], string> = {
  'pravah-midroad': 'mid-road',
  'new-signal': 'new signal',
  'corridor-sync': 'corridor sync',
  'camera-upgrade': 'camera',
}

export type Priority = 'high' | 'medium' | 'low'

export function getPriority(score: number): Priority {
  if (score >= 80) return 'high'
  if (score >= 65) return 'medium'
  return 'low'
}

export const priorityColor: Record<Priority, string> = {
  high: '#6B3FA0',
  medium: '#18A0A8',
  low: '#9AABB8',
}

export const priorityLabel: Record<Priority, string> = {
  high: 'High priority',
  medium: 'Medium',
  low: 'Watchlist',
}

export function getSiteColor(score: number): string {
  return priorityColor[getPriority(score)]
}
