import { createContext, useContext } from 'react'

/** The display currency and its USD rate, as the engine sends it.
 *  rate null = show dollars (dollars chosen, or no rate fetched yet). */
export type Fx = {
  currency: string
  symbol: string
  rate: number | null
  label?: string
  date?: string | null
  source?: string | null
  /** The rate services failed just now; this is the saved rate. */
  stale?: boolean
}

export const USD_FX: Fx = { currency: 'USD', symbol: '$', rate: null }
export const FxContext = createContext<Fx>(USD_FX)

/** True when amounts on screen are converted from dollars. */
export const converting = (fx: Fx) =>
  fx.currency !== 'USD' && typeof fx.rate === 'number' && fx.rate > 0

/** A converted amount: 2 decimals from 1 up, otherwise 2 significant digits,
 *  so a $0.0007 step reads ₱0.045 and never ₱0.00. */
export function fmtConverted(v: number): string {
  if (!v || !Number.isFinite(v)) return '0'
  const a = Math.abs(v)
  if (a >= 1) return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  const dp = Math.min(8, Math.max(2, Math.ceil(-Math.log10(a)) + 1))
  return v.toFixed(dp)
}

/** One amount for the screen. Every money figure goes through here.
 *
 *  `usd` is what the gateway billed (or the estimate). In dollars the
 *  caller's own format is used unchanged, so choosing dollars looks exactly
 *  as it always did. Converted, it reads "≈₱0.045": ≈ because a display
 *  rate is not what was billed. An estimate keeps its "~" (which already
 *  says approximate) in place of the ≈. */
export function money(usd: number, fx: Fx, usdFmt: (n: number) => string, estimated = false): string {
  if (!converting(fx)) return (estimated ? '~' : '') + usdFmt(usd)
  if (!usd) return fx.symbol + '0'
  return (estimated ? '~' : '≈') + fx.symbol + fmtConverted(usd * (fx.rate as number))
}

/** A catalog price ("free" and "—" pass through untouched). */
export function price(usd: number | null, fx: Fx, usdFmt: (n: number | null) => string): string {
  if (usd === null || usd === 0 || !converting(fx)) return usdFmt(usd)
  return '≈' + fx.symbol + fmtConverted(usd * (fx.rate as number))
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const fmtDate = (d?: string | null) => {
  const m = d && /^(\d{4})-(\d{2})-(\d{2})$/.exec(d)
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1] ?? ''} ${m[1]}` : ''
}

/** A plain sentence saying which rate is in use, for the Spend popover.
 *  Empty when dollars were chosen. */
export function fxNote(fx: Fx): string {
  if (fx.currency === 'USD') return ''
  const name = fx.label ?? fx.currency
  if (!converting(fx)) {
    return fx.stale
      ? `No ${name} rate yet: the rate services could not be reached, so costs are shown in US dollars.`
      : `Getting the ${name} rate. Costs are shown in US dollars until it arrives.`
  }
  const rate = (fx.rate as number).toFixed(2)
  const when = fmtDate(fx.date)
  const from = [fx.source, when].filter(Boolean).join(', ')
  const base = `Billed in US dollars, shown at ${fx.symbol}${rate} per $1${from ? ` (${from})` : ''}.`
  return fx.stale ? `${base} The rate services could not be reached, so this is the last saved rate.` : base
}

/** The current rate and a formatter bound to it. */
export function useMoney() {
  const fx = useContext(FxContext)
  return {
    fx,
    money: (usd: number, usdFmt: (n: number) => string, estimated = false) =>
      money(usd, fx, usdFmt, estimated),
    price: (usd: number | null, usdFmt: (n: number | null) => string) => price(usd, fx, usdFmt),
  }
}
