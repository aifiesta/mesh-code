import { IconExternal } from './Chevron'
import type { Profile } from '../types'

/** Account and session state. Never renders the API key itself — see
 *  Engine.profile() for why a hint is the whole point. */
export function ProfilePanel({ profile, sessionCost, sessionTokens, onManageKey }: {
  profile: Profile | null
  sessionCost: number
  sessionTokens: number
  onManageKey: () => void
}) {
  if (!profile) {
    return <div className="profile"><div className="profile-empty">Waiting for the engine…</div></div>
  }
  return (
    <div className="profile">
      <div className="profile-id">
        <span className={`profile-status ${profile.signed_in ? 'on' : ''}`} />
        <div>
          <strong>{profile.signed_in ? 'Signed in' : 'Not signed in'}</strong>
          <span>
            {profile.key_hint ? `Key ${profile.key_hint}` : 'No key stored'}
            {profile.key_source && <> · from {profile.key_source}</>}
          </span>
        </div>
      </div>

      <button className="btn subtle wide" onClick={onManageKey}>
        {profile.signed_in ? 'Replace API key' : 'Connect an API key'}
      </button>

      <div className="profile-head">This session</div>
      <Row label="Spent" value={`$${sessionCost.toFixed(4)}`} mono accent />
      <Row label="Tokens" value={sessionTokens.toLocaleString()} mono />

      {profile.lifetime && (
        <>
          <div className="profile-head">
            All time
            <span title="Counted by this app from what the gateway billed per turn. Your account total lives on meshapi.ai.">
              ⓘ
            </span>
          </div>
          <Row label="Spent" value={`$${profile.lifetime.cost.toFixed(4)}`} mono accent />
          <Row label="Tokens" value={profile.lifetime.tokens.toLocaleString()} mono />
          <Row label="Turns" value={profile.lifetime.turns.toLocaleString()} mono />
        </>
      )}

      <div className="profile-head">Connection</div>
      <Row label="Gateway" value={profile.gateway.replace(/^https?:\/\//, '')} mono />
      <Row label="Models" value={profile.models ? profile.models.toLocaleString() : '—'} />
      <Row label="Version" value={profile.version} mono />

      <a className="btn subtle wide profile-link"
         href={profile.account_url ?? 'https://app.meshapi.ai'}
         target="_blank" rel="noreferrer noopener">
        Account &amp; billing <IconExternal size={11} />
      </a>

      <p className="profile-note">
        Totals above are what <b>this app</b> has spent — Mesh doesn’t expose an
        account balance to an API key, so your authoritative usage lives on
        meshapi.ai. Settings and repo memory are in <code>{profile.config_dir}</code>,
        owner-readable only.
      </p>
    </div>
  )
}

function Row({ label, value, mono, accent }: {
  label: string; value: string; mono?: boolean; accent?: boolean
}) {
  return (
    <div className="profile-row">
      <span>{label}</span>
      <span className={`${mono ? 'mono' : ''} ${accent ? 'accent' : ''}`} title={value}>{value}</span>
    </div>
  )
}
