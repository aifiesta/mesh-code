import { addBills, billTotal, fmtSmallUsd, fmtTokens, type Bill } from '../types'
import { useMoney } from '../money'

type PartKey = 'fresh' | 'cached' | 'cache_write' | 'output'

/** The parts a bill is made of, in the order they are shown. Thinking is a
 *  share of output (it is billed as output), so it is shown inside that row
 *  and never added to the total. */
const PARTS: { key: PartKey; label: string; hint: string }[] = [
  { key: 'fresh', label: 'Fresh input', hint: 'Input billed at the full price: new text the provider had not seen.' },
  { key: 'cached', label: 'Cached input', hint: 'Input the provider already had cached. Billed at a small fraction of the full price.' },
  { key: 'cache_write', label: 'Cache written', hint: 'Input stored in the provider’s cache on this request. Some providers charge extra for it once.' },
  { key: 'output', label: 'Output', hint: 'Everything the model wrote, thinking included. Usually several times the input price, never cached.' },
]

const tokensOf = (bill: Bill, key: PartKey): number => bill[(key + '_tokens') as keyof Bill] as number ?? 0
const usdOf = (bill: Bill, key: PartKey): number => bill[(key + '_usd') as keyof Bill] as number ?? 0
const shown = (bill: Bill) => PARTS.filter((p) => tokensOf(bill, p.key) > 0 || usdOf(bill, p.key) > 0)

/** One line under a finished turn: where its tokens and money went. */
export function BillLine({ bill }: { bill?: Bill | null }) {
  const { money } = useMoney()   // before any early return: hooks run every render
  if (!bill) return null
  const parts = shown(bill)
  if (!parts.length) return null
  const amt = (usd: number) => money(usd, fmtSmallUsd, bill.estimated)
  const why = bill.estimated
    ? 'The gateway sent no breakdown for at least one step, so this split is worked out from token counts and catalog prices.'
    : 'As billed by the gateway, step by step.'
  return (
    <div className="turn-split" title={why}>
      {parts.map((p, i) => (
        <span key={p.key} title={p.hint}>
          {i > 0 && ' · '}
          {p.label.toLowerCase()} {fmtTokens(tokensOf(bill, p.key))}
          {' '}{amt(usdOf(bill, p.key))}
          {p.key === 'output' && bill.thinking_tokens > 0 &&
            <> ({fmtTokens(bill.thinking_tokens)} thinking)</>}
        </span>
      ))}
      {bill.other_usd > 0 && <span> · other {amt(bill.other_usd)}</span>}
    </div>
  )
}

/** The same split as a small table, for the Tokens and Spend popovers. */
export function BillTable({ session, turn, live }: {
  session?: Bill | null; turn?: Bill | null; live: boolean
}) {
  const { money } = useMoney()
  // Session figures only move when a turn ends, so the turn in flight is added in.
  const bill = live ? addBills(session, turn) : (session ?? null)
  if (!bill) return null
  const parts = shown(bill)
  if (!parts.length) return null
  const total = billTotal(bill)
  const pct = (usd: number) => (total > 0 ? Math.round((100 * usd) / total) + '%' : '')
  const amt = (usd: number) => money(usd, fmtSmallUsd, bill.estimated)
  return (
    <table className="bill-table">
      <thead>
        <tr><th>This session</th><th>Tokens</th><th>Cost</th><th>Share</th></tr>
      </thead>
      <tbody>
        {parts.map((p) => (
          <tr key={p.key} title={p.hint}>
            <td>{p.label}</td>
            <td>{tokensOf(bill, p.key).toLocaleString()}</td>
            <td>{amt(usdOf(bill, p.key))}</td>
            <td>{pct(usdOf(bill, p.key))}</td>
          </tr>
        ))}
        {bill.thinking_tokens > 0 && (
          <tr className="sub" title="The part of output that was the model thinking. Already counted in Output.">
            <td>of which thinking</td>
            <td>{bill.thinking_tokens.toLocaleString()}</td>
            <td>{amt(bill.thinking_usd)}</td>
            <td>{pct(bill.thinking_usd)}</td>
          </tr>
        )}
        {bill.other_usd > 0 && (
          <tr title="Billed, but not as tokens (a tool fee, for example).">
            <td>Other</td><td></td><td>{amt(bill.other_usd)}</td><td>{pct(bill.other_usd)}</td>
          </tr>
        )}
      </tbody>
    </table>
  )
}
