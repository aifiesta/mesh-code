import { describe, expect, it } from 'vitest'
import { canAllowMoreToday, ringTone } from './budget'
import type { BudgetSnapshot } from './types'

const snap = (over: Partial<BudgetSnapshot> = {}, dayPct = 0): BudgetSnapshot => ({
  month: { spent: 0, limit: 10, pct: 0, resets: 0, by_kind: {} },
  day: { spent: 0, limit: 0.32, pct: dayPct, resets: 0, by_kind: {}, override: false },
  key_cap: false, held: null, estimated_usd: 0, ...over,
})

describe('budget hold', () => {
  it('offers Allow more today for a hold issued while under 100%', () => {
    const b = snap({ held: 'day' })
    expect(canAllowMoreToday(b)).toBe(true)
    expect(ringTone(b)).toBe('over')
  })
  it('offers nothing when nothing is held and the day has room', () => {
    expect(canAllowMoreToday(snap())).toBe(false)
    expect(ringTone(snap())).toBe('ok')
  })
  it('never offers it once today is already overridden or the month is spent', () => {
    expect(canAllowMoreToday(snap({ held: 'day', day: { ...snap().day, override: true } }))).toBe(false)
    expect(canAllowMoreToday(snap({ held: 'month', month: { ...snap().month, pct: 100 } }, 100))).toBe(false)
  })
})
