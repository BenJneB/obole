import type { Candidate, Rerank } from '../api.ts'

export default function CandidateCard({
  candidate,
  onChoose,
  compact = false,
  owned = 0,
  pick,
}: {
  candidate: Candidate
  onChoose?: () => void
  compact?: boolean
  /** Exemplaires de cette fiche déjà dans la collection. */
  owned?: number
  /** Fiche désignée par le re-classement visuel. */
  pick?: Rerank
}) {
  const classes = ['candidate', compact && 'compact', pick && 'picked'].filter(Boolean).join(' ')
  return (
    <div className={classes}>
      <div className="candidate-images">
        {[candidate.obverse_thumbnail, candidate.reverse_thumbnail].map(
          (src, i) => src && <img key={i} src={src} alt="" loading="lazy" referrerPolicy="no-referrer" />,
        )}
      </div>
      <div className="candidate-text">
        {pick && (
          <span className="pick-badge">
            Choix de l'IA · {Math.round(pick.confidence * 100)} %
            {pick.reason && <span className="muted small block">{pick.reason}</span>}
          </span>
        )}
        <strong>{candidate.title}</strong>
        {owned > 0 && <span className="owned-badge">Déjà dans la collection (×{owned})</span>}
        <span className="muted small">
          {candidate.issuer} · {candidate.years} ·{' '}
          <a href={candidate.url} target="_blank" rel="noreferrer">
            N# {candidate.id} ↗
          </a>
        </span>
        {onChoose && (
          <button className="btn primary small" onClick={onChoose}>
            C'est celle-ci
          </button>
        )}
      </div>
    </div>
  )
}
