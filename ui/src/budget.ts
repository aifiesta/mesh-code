import type { BudgetSnapshot } from './types'

export function ringTone(b: BudgetSnapshot): 'over' | 'warn' | 'ok' {
  if (b.key_cap || b.held || b.day.pct >= 100 || b.month.pct >= 100) return 'over'
  if (b.day.pct >= 80 || b.month.pct >= 80) return 'warn'
  return 'ok'
}

/** A day hold can start from the pre-flight estimate alone, under 100% spent. */
export function canAllowMoreToday(b: BudgetSnapshot): boolean {
  if (b.key_cap || b.day.override || b.month.limit <= 0 || b.month.pct >= 100) return false
  return b.held === 'day' || b.day.pct >= 100
}
