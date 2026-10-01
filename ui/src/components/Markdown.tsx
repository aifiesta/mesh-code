import { useMemo, useState } from 'react'
import { highlightLines, langForTag, Line } from '../highlight'

/**
 * Small markdown renderer — headings, lists, code, emphasis, links.
 *
 * Hand-rolled rather than pulled from npm for one reason that matters: this
 * renders text an LLM produced, which may itself contain text a web page or
 * a file fed to the model. It never builds HTML from that string — every
 * node here is a React element with text set as a child, so there is no
 * dangerouslySetInnerHTML and therefore no injection surface at all.
 */
type Block =
  | { t: 'code'; lang: string; body: string }
  | { t: 'h'; level: number; body: string }
  | { t: 'ul'; items: string[] }
  | { t: 'ol'; items: string[] }
  | { t: 'quote'; body: string }
  | { t: 'p'; body: string }

function parse(src: string): Block[] {
  const lines = src.split('\n')
  const out: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]

    const fence = line.match(/^```(\w*)/)
    if (fence) {
      const lang = fence[1] || ''
      const body: string[] = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) body.push(lines[i++])
      i++
      out.push({ t: 'code', lang, body: body.join('\n') })
      continue
    }

    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) { out.push({ t: 'h', level: h[1].length, body: h[2] }); i++; continue }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i]))
        items.push(lines[i++].replace(/^\s*[-*]\s+/, ''))
      out.push({ t: 'ul', items }); continue
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i]))
        items.push(lines[i++].replace(/^\s*\d+\.\s+/, ''))
      out.push({ t: 'ol', items }); continue
    }

    if (/^>\s?/.test(line)) {
      const body: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i])) body.push(lines[i++].replace(/^>\s?/, ''))
      out.push({ t: 'quote', body: body.join('\n') }); continue
    }

    if (!line.trim()) { i++; continue }

    const body: string[] = []
    while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|>\s?|\s*[-*]\s|\s*\d+\.\s)/.test(lines[i]))
      body.push(lines[i++])
    out.push(...splitDump(body.join('\n')))
  }
  return out
}

/**
 * A weaker model sometimes pastes a whole document into its prose without
 * fences — "created with the content: <!DOCTYPE html> <html>…" — and a
 * paragraph of unbroken markup is unreadable. When a paragraph carries a
 * document opener, cut there: prose before it stays a paragraph, the
 * markup after it becomes a code block with the copy button and all.
 */
const DUMP = /(<!DOCTYPE\s|<html[\s>]|<\?xml\s|<svg[\s>])/i
function splitDump(body: string): Block[] {
  const m = body.match(DUMP)
  if (!m || m.index === undefined) return [{ t: 'p', body }]
  const head = body.slice(0, m.index).trim()
  const code = body.slice(m.index).trim()
  const lang = /^<\?xml/i.test(code) ? 'xml' : /^<svg/i.test(code) ? 'svg' : 'html'
  const out: Block[] = []
  if (head) out.push({ t: 'p', body: head })
  out.push({ t: 'code', lang, body: code })
  return out
}

/** Inline spans: `code`, **bold**, *italic*, [text](url). Text only. */
function Inline({ text }: { text: string }) {
  const parts = useMemo(() => {
    const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g
    return text.split(re).filter((s) => s !== '' && s !== undefined)
  }, [text])

  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith('`') && p.endsWith('`')) return <code key={i}>{p.slice(1, -1)}</code>
        if (p.startsWith('**') && p.endsWith('**')) return <strong key={i}>{p.slice(2, -2)}</strong>
        if (p.startsWith('*') && p.endsWith('*')) return <em key={i}>{p.slice(1, -1)}</em>
        const link = p.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
        if (link) {
          const href = link[2]
          // Only http(s) — a javascript: or data: href in model output must
          // never become a live link.
          const safe = /^https?:\/\//i.test(href)
          return safe
            ? <a key={i} href={href} target="_blank" rel="noreferrer noopener">{link[1]}</a>
            : <span key={i}>{link[1]}</span>
        }
        return <span key={i}>{p}</span>
      })}
    </>
  )
}

function CodeBlock({ lang, body }: { lang: string; body: string }) {
  const [copied, setCopied] = useState(false)
  const lines = useMemo(() => highlightLines(body, langForTag(lang)), [body, lang])
  return (
    <div className="code-block">
      <div className="code-bar">
        <span>{lang || 'text'}</span>
        <button
          onClick={() => {
            navigator.clipboard?.writeText(body)
            setCopied(true)
            setTimeout(() => setCopied(false), 1200)
          }}
        >{copied ? 'copied' : 'copy'}</button>
      </div>
      <pre><code>{lines.map((l, i) => (
        <span key={i} className="code-line"><Line spans={l} />{'\n'}</span>
      ))}</code></pre>
    </div>
  )
}

export function Markdown({ text }: { text: string }) {
  const blocks = useMemo(() => parse(text), [text])
  return (
    <div className="md">
      {blocks.map((b, i) => {
        switch (b.t) {
          case 'code': return <CodeBlock key={i} lang={b.lang} body={b.body} />
          case 'h': {
            const H = (`h${Math.min(b.level + 1, 6)}`) as 'h2'
            return <H key={i}><Inline text={b.body} /></H>
          }
          case 'ul': return <ul key={i}>{b.items.map((it, j) => <li key={j}><Inline text={it} /></li>)}</ul>
          case 'ol': return <ol key={i}>{b.items.map((it, j) => <li key={j}><Inline text={it} /></li>)}</ol>
          case 'quote': return <blockquote key={i}><Inline text={b.body} /></blockquote>
          default: return <p key={i}><Inline text={b.body} /></p>
        }
      })}
    </div>
  )
}
