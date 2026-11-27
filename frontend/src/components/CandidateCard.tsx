import type { Candidate } from '../api.ts'

export default function CandidateCard({
  candidate,
  onChoose,
  compact = false,
}: {
  candidate: Candidate
  onChoose?: () => void
  compact?: boolean
}) {
  return (
    <div className={compact ? 'candidate compact' : 'candidate'}>
      <div className="candidate-images">
        {[candidate.obverse_thumbnail, candidate.reverse_thumbnail].map(
          (src, i) => src && <img key={i} src={src} alt="" loading="lazy" referrerPolicy="no-referrer" />,
        )}
      </div>
      <div className="candidate-text">
        <strong>{candidate.title}</strong>
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
