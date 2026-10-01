/** Render `text` with the fuzzy-matched character positions emphasised. */
export function Highlight({ text, positions }: { text: string; positions: number[] }) {
  if (!positions.length) return <>{text}</>
  const set = new Set(positions)
  const out: React.ReactNode[] = []
  let buf = ''
  let bufHit = set.has(0)

  const flush = (i: number) => {
    if (!buf) return
    out.push(bufHit ? <mark key={i}>{buf}</mark> : <span key={i}>{buf}</span>)
    buf = ''
  }

  for (let i = 0; i < text.length; i++) {
    const hit = set.has(i)
    if (hit !== bufHit) { flush(i); bufHit = hit }
    buf += text[i]
  }
  flush(text.length)
  return <>{out}</>
}
