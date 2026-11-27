import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  api,
  identify,
  type Candidate,
  type Extraction,
  type IdentifyProgress,
  type IdentifyResponse,
  type ItemFields,
  type Match,
  type ObjectType,
} from '../api.ts'
import CandidateCard from '../components/CandidateCard.tsx'
import ItemForm from '../components/ItemForm.tsx'
import { CameraButton, ErrorMessage, Notice } from '../components/ui.tsx'
import { OBJECT_TYPES } from '../format.ts'

type Step =
  | { kind: 'photos' }
  | { kind: 'identifying'; progress: IdentifyProgress; startedAt: number }
  | { kind: 'results' }
  | { kind: 'manual-search' }
  | { kind: 'form'; initial: Partial<ItemFields>; candidate: Candidate | null }

export default function AddItem() {
  const navigate = useNavigate()
  const [obverse, setObverse] = useState<File | null>(null)
  const [reverse, setReverse] = useState<File | null>(null)
  const [step, setStep] = useState<Step>({ kind: 'photos' })
  const [result, setResult] = useState<IdentifyResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const abort = useRef<(() => void) | null>(null)

  useEffect(() => () => abort.current?.(), []) // quitter la page annule l'envoi en cours

  async function runIdentify() {
    if (!obverse) return
    setError(null)
    const startedAt = Date.now()
    setStep({ kind: 'identifying', progress: { phase: 'upload', ratio: 0 }, startedAt })
    const request = identify(obverse, reverse, (progress) =>
      setStep((s) => (s.kind === 'identifying' ? { ...s, progress } : s)),
    )
    abort.current = request.abort
    try {
      const response = await request.promise
      setResult(response)
      setStep({ kind: 'results' })
    } catch (e) {
      setError((e as Error).message)
      setStep({ kind: 'photos' })
    } finally {
      abort.current = null
    }
  }

  /** Pré-remplit le formulaire avec la lecture de l'IA et, si choisie, la fiche Numista. */
  function openForm(candidate: Candidate | null, match: Match | null) {
    const e = result?.extraction
    const initial: Partial<ItemFields> = {
      object_type: e?.object_type && e.object_type !== 'unknown' ? e.object_type : 'coin',
      country: candidate?.issuer ?? e?.country ?? null,
      issuer_code: candidate ? (match?.issuer_code ?? null) : null,
      denomination: e?.denomination_text ?? null,
      currency: e?.currency ?? null,
      year: e?.year ?? null,
      quantity: 1,
    }
    if (candidate) {
      initial.numista_id = candidate.id
      initial.numista_title = candidate.title
      initial.numista_url = candidate.url
    }
    setStep({ kind: 'form', initial, candidate })
  }

  async function save(fields: Partial<ItemFields>, candidate: Candidate | null) {
    const source = candidate ? (result?.extraction ? 'ai_numista' : 'numista') : 'manual'
    const item = await api.createItem({
      ...fields,
      numista_title: fields.numista_id === candidate?.id ? candidate?.title : null,
      issuer_code: step.kind === 'form' ? step.initial.issuer_code : null,
      identification_source: source,
      upload_id: result?.upload_id ?? null,
      ai_extraction: result?.extraction ?? null,
    })
    // Saisie manuelle sans identification : les photos n'ont pas encore été envoyées
    if (!result) {
      for (const [file, side] of [[obverse, 'obverse'], [reverse, 'reverse']] as const) {
        if (file) await api.addPhoto(item.id, file, side)
      }
    }
    navigate(`/objet/${item.id}`, { replace: true, state: { added: true } })
  }

  function restart() {
    setObverse(null)
    setReverse(null)
    setResult(null)
    setError(null)
    setStep({ kind: 'photos' })
  }

  if (step.kind === 'identifying') return <Identifying step={step} onCancel={() => abort.current?.()} />

  if (step.kind === 'results' && result) {
    return (
      <Results
        result={result}
        onUpdate={setResult}
        onChoose={(candidate) => openForm(candidate, result.match)}
        onNone={() => setStep({ kind: 'manual-search' })}
        onRestart={restart}
      />
    )
  }

  if (step.kind === 'manual-search') {
    return (
      <ManualSearch
        initialQuery={result?.extraction?.denomination_text ?? ''}
        objectType={result?.extraction?.object_type}
        onChoose={(candidate) => openForm(candidate, null)}
        onFreeEntry={() => openForm(null, null)}
        onBack={() => setStep(result ? { kind: 'results' } : { kind: 'photos' })}
      />
    )
  }

  if (step.kind === 'form') {
    const { candidate } = step
    return (
      <>
        <header className="page-header">
          <h1>Enregistrer</h1>
        </header>
        <ItemForm
          initial={step.initial}
          submitLabel="Ajouter à la collection"
          onSubmit={(fields) => save(fields, candidate)}
          onCancel={() => setStep(result ? { kind: 'results' } : { kind: 'photos' })}
          header={candidate && <CandidateCard candidate={candidate} compact />}
        />
      </>
    )
  }

  return (
    <>
      <header className="page-header">
        <h1>Ajouter un objet</h1>
        <p className="muted">Photographie l'objet de près, sur un fond uni et bien éclairé.</p>
      </header>
      <div className="cameras">
        <CameraButton label="Avers" file={obverse} onChange={setObverse} />
        <CameraButton label="Revers (facultatif)" file={reverse} onChange={setReverse} />
      </div>
      <ErrorMessage message={error} />
      <div className="actions vertical">
        <button className="btn primary big" disabled={!obverse} onClick={runIdentify}>
          Identifier
        </button>
        <button
          className="btn"
          onClick={() => {
            setResult(null)
            setStep({ kind: 'manual-search' })
          }}
        >
          Chercher dans Numista sans IA
        </button>
        <button className="link" onClick={() => openForm(null, null)}>
          Saisie manuelle
        </button>
      </div>
    </>
  )
}

function Identifying({
  step,
  onCancel,
}: {
  step: Extract<Step, { kind: 'identifying' }>
  onCancel: () => void
}) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(timer)
  }, [])
  const seconds = Math.max(0, Math.round((now - step.startedAt) / 1000))
  const { progress } = step
  return (
    <div className="identifying">
      <div className="spinner" aria-hidden />
      <ol className="steps">
        <li className={progress.phase === 'upload' ? 'current' : 'done'}>
          Envoi des photos
          {progress.phase === 'upload' && ` (${Math.round(progress.ratio * 100)} %)`}
        </li>
        <li className={progress.phase === 'analysis' ? 'current' : ''}>Analyse de l'image</li>
        <li className={progress.phase === 'analysis' ? 'current' : ''}>Recherche Numista</li>
      </ol>
      <p className="muted">{seconds} s</p>
      {seconds > 20 && (
        <Notice>
          C'est plus long que d'habitude : le modèle est peut-être en train de se charger sur le PC (jusqu'à une
          minute la première fois).
        </Notice>
      )}
      <button className="btn" onClick={onCancel}>
        Annuler
      </button>
    </div>
  )
}

const EDITABLE: { key: 'country' | 'denomination_text' | 'year'; label: string }[] = [
  { key: 'country', label: 'Pays' },
  { key: 'denomination_text', label: 'Valeur' },
  { key: 'year', label: 'Année' },
]

function Results({
  result,
  onUpdate,
  onChoose,
  onNone,
  onRestart,
}: {
  result: IdentifyResponse
  onUpdate: (result: IdentifyResponse) => void
  onChoose: (candidate: Candidate) => void
  onNone: () => void
  onRestart: () => void
}) {
  const extraction = result.extraction
  const [draft, setDraft] = useState(() => ({
    country: extraction?.country ?? '',
    denomination_text: extraction?.denomination_text ?? '',
    year: extraction?.year != null ? String(extraction.year) : '',
  }))
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const candidates = result.match?.candidates ?? []

  const changed =
    extraction &&
    (draft.country !== (extraction.country ?? '') ||
      draft.denomination_text !== (extraction.denomination_text ?? '') ||
      draft.year !== (extraction.year != null ? String(extraction.year) : ''))

  async function researchWithCorrection() {
    if (!extraction) return
    const year = /^\d{1,4}$/.test(draft.year.trim()) ? Number(draft.year) : null
    const corrected: Extraction = {
      ...extraction,
      country: draft.country.trim() || null,
      denomination_text: draft.denomination_text.trim() || null,
      year,
      year_visible: year != null,
    }
    setSearching(true)
    setError(null)
    try {
      const response = await api.rematch(corrected)
      onUpdate({ ...result, extraction: corrected, match: response.match, errors: response.errors, quota: response.quota })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSearching(false)
    }
  }

  return (
    <>
      <header className="page-header">
        <h1>Résultats</h1>
        {result.seconds != null && (
          <p className="muted small">
            Analyse en {result.seconds} s ({result.model})
          </p>
        )}
      </header>

      {result.errors.map((message) => (
        <Notice key={message}>{message}</Notice>
      ))}

      {extraction && (
        <section className="panel">
          <h2>Ce que l'IA a lu</h2>
          <p className="muted small">{extraction.description}</p>
          <div className="reading">
            {EDITABLE.map(({ key, label }) => (
              <label key={key}>
                {label}
                <input
                  value={draft[key]}
                  inputMode={key === 'year' ? 'numeric' : undefined}
                  onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                />
              </label>
            ))}
          </div>
          {extraction.inscriptions.length > 0 && (
            <p className="inscriptions small">« {extraction.inscriptions.join(' · ')} »</p>
          )}
          {changed && (
            <button className="btn" disabled={searching} onClick={researchWithCorrection}>
              {searching ? 'Recherche…' : 'Relancer la recherche avec ces corrections'}
            </button>
          )}
          <ErrorMessage message={error} />
        </section>
      )}

      {candidates.length > 0 && (
        <section>
          <h2>Fiches Numista proposées</h2>
          <ul className="candidates">
            {candidates.map((c) => (
              <li key={c.id}>
                <CandidateCard candidate={c} onChoose={() => onChoose(c)} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="actions vertical">
        <button className="btn" onClick={onNone}>
          {candidates.length ? 'Aucune ne correspond' : 'Chercher dans Numista'}
        </button>
        <button className="link" onClick={onRestart}>
          Reprendre les photos
        </button>
      </div>
      <p className="muted small center">
        Quota Numista : {result.quota.used} / {result.quota.limit} requêtes ce mois-ci
      </p>
    </>
  )
}

function ManualSearch({
  initialQuery,
  objectType,
  onChoose,
  onFreeEntry,
  onBack,
}: {
  initialQuery: string
  objectType?: ObjectType
  onChoose: (candidate: Candidate) => void
  onFreeEntry: () => void
  onBack: () => void
}) {
  const [query, setQuery] = useState(initialQuery)
  const [type, setType] = useState<ObjectType | ''>(objectType && objectType !== 'unknown' ? objectType : '')
  const [results, setResults] = useState<Candidate[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function search(e: FormEvent) {
    e.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    try {
      setResults(await api.searchNumista(query.trim(), type || undefined))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <header className="page-header">
        <h1>Recherche Numista</h1>
        <p className="muted small">Ex. « 20 kopecks 1967 », « 5 francs Semeuse », « Hadrien denier ».</p>
      </header>
      <form className="search-form" onSubmit={search}>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Valeur, pays, motif…"
          enterKeyHint="search"
          autoFocus
        />
        <select value={type} onChange={(e) => setType(e.target.value as ObjectType | '')} aria-label="Type">
          <option value="">Tous types</option>
          {OBJECT_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.plural}
            </option>
          ))}
        </select>
        <button className="btn primary" disabled={loading || !query.trim()}>
          {loading ? 'Recherche…' : 'Chercher'}
        </button>
      </form>
      <ErrorMessage message={error} />
      {results && results.length === 0 && <p className="empty">Aucun résultat.</p>}
      {results && results.length > 0 && (
        <ul className="candidates">
          {results.map((c) => (
            <li key={c.id}>
              <CandidateCard candidate={c} onChoose={() => onChoose(c)} />
            </li>
          ))}
        </ul>
      )}
      <div className="actions vertical">
        <button className="btn" onClick={onFreeEntry}>
          Saisie libre, sans fiche Numista
        </button>
        <button className="link" onClick={onBack}>
          Retour
        </button>
      </div>
    </>
  )
}
