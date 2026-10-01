export function ServerPanel({ servers, onStop }: {
  servers: { pid: number; port: number; url: string; cmd: string }[]
  onStop: (pid: number) => void
}) {
  return (
    <section className="panel">
      <div className="panel-head"><h3>Running</h3></div>
      <ul className="server-list">
        {servers.map((s) => (
          <li key={s.pid}>
            <a href={s.url} target="_blank" rel="noreferrer noopener">{s.url}</a>
            <code title={s.cmd}>{s.cmd}</code>
            <button onClick={() => onStop(s.pid)}>stop</button>
          </li>
        ))}
      </ul>
    </section>
  )
}
