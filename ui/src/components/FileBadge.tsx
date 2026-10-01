import { IconX } from './Chevron'

/** A referenced workspace file, as a pill: icon, name, the directory in a
 *  quieter weight, and the full path on hover. Used in the composer (with
 *  a remove button) and in sent messages (without). */
export function FileBadge({ path, onRemove, onClick }: {
  path: string
  onRemove?: () => void
  onClick?: () => void
}) {
  const cut = path.lastIndexOf('/')
  const name = cut >= 0 ? path.slice(cut + 1) : path
  const dir = cut >= 0 ? path.slice(0, cut) : ''
  const Tag: any = onClick ? 'button' : 'span'
  return (
    <Tag className="file-badge" title={path} onClick={onClick}>
      <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden fill="none"
           stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round">
        <path d="M4 1.5h5l3 3v10H4z" /><path d="M9 1.5v3h3" />
      </svg>
      <span className="file-badge-name">{name}</span>
      {dir && <span className="file-badge-dir">{dir}</span>}
      {onRemove && (
        <button className="file-badge-x" onClick={(e) => { e.stopPropagation(); onRemove() }}
                aria-label={`Remove ${name}`}><IconX size={10} /></button>
      )}
    </Tag>
  )
}
