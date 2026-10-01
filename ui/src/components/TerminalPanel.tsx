import { useEffect, useRef, useState } from 'react'

/**
 * A plain shell, in the app.
 *
 * Deliberately separate from the agent: no approval dialogs, no tokens, no
 * waiting for a model to decide. It is the same shell you would open in
 * Terminal.app, pointed at the active project.
 *
 * It is a pipe, not a PTY, so full-screen programs (vim, top) will not draw
 * correctly. Commands and their output — the reason you reach for a terminal
 * mid-task — work fine, and the panel says so rather than letting you find
 * out by trying.
 */
export type TermLine = { text?: string; echo?: boolean; done?: boolean; exit_code?: string }

export function TerminalPanel({ lines, cwd, shell, onRun, onInterrupt, onClear }: {
  lines: TermLine[]
  cwd: string
  shell: string
  onRun: (cmd: string) => void
  onInterrupt: () => void
  onClear: () => void
}) {
  const [value, setValue] = useState('')
  const [history, setHistory] = useState<string[]>([])
  const [hIndex, setHIndex] = useState(-1)
  const scroller = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const el = scroller.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines])

  const submit = () => {
    const cmd = value.trim()
    if (!cmd) return
    onRun(cmd)
    setHistory((h) => [cmd, ...h.filter((x) => x !== cmd)].slice(0, 100))
    setHIndex(-1)
    setValue('')
  }

  return (
    <div className="term">
      <div className="term-head">
        <span className="term-shell">{shell.split('/').pop()}</span>
        <span className="term-cwd" title={cwd}>{cwd.split('/').slice(-2).join('/')}</span>
        <div className="spacer" />
        <button onClick={onInterrupt} title="Interrupt (SIGINT)">⌃C</button>
        <button onClick={onClear} title="Clear">clear</button>
      </div>

      <div className="term-body" ref={scroller} onClick={() => input.current?.focus()}>
        {lines.length === 0 && (
          <div className="term-hint">
            Your own shell in <b>{cwd.split('/').pop()}</b> — no agent, no approvals.
            <br />Full-screen programs (vim, top) won’t render; commands and output work.
          </div>
        )}
        {lines.map((l, i) =>
          l.done ? (
            <div className={`term-exit ${l.exit_code !== '0' ? 'bad' : ''}`} key={i}>
              exit {l.exit_code}
            </div>
          ) : (
            <div className={`term-line ${l.echo ? 'echo' : ''}`} key={i}>{l.text}</div>
          ),
        )}
      </div>

      <div className="term-input">
        <span className="term-prompt">$</span>
        <input
          ref={input}
          value={value}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          placeholder="npm install, git status, …"
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') return submit()
            if (e.key === 'c' && e.ctrlKey) return onInterrupt()
            // Shell-style history on the arrow keys.
            if (e.key === 'ArrowUp') {
              e.preventDefault()
              const n = Math.min(hIndex + 1, history.length - 1)
              if (n >= 0) { setHIndex(n); setValue(history[n]) }
            }
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              // Floor at -1 (the live/empty line). Without the clamp this ran
              // to -2, -3…; ArrowUp then computed min(-2+1,…) = -1 forever
              // and history recall was dead until the next submitted command.
              const n = Math.max(-1, hIndex - 1)
              setHIndex(n)
              setValue(n >= 0 ? history[n] : '')
            }
          }}
        />
      </div>
    </div>
  )
}
