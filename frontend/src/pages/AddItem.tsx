import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
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
  type OwnedItem,
  type Rerank,
} from '../api.ts'
import CandidateCard from '../components/CandidateCard.tsx'
import ItemForm from '../components/ItemForm.tsx'
import { existingTags } from '../tags.ts'
import { CameraButton, ErrorMessage, Notice } from '../components/ui.tsx'
import { OBJECT_TYPES, itemTitle, plural } from '../format.ts'

// Mode série : champs repris d'un objet au suivant (même album, même lot, même jour d'achat)
const CARRIED_OVER = ['storage_location', 'tags', 'acquired_date'] as const
const SERIES_KEY = 'obole.serie'

function loadSeriesFlag(): boolean {
  try {
    return localStorage.getItem(SERIES_KEY) === '1'
  } catch {
    return false // stockage indisponible (navigation privée...) : simple valeur par défaut
  }
}

function saveSeriesFlag(on: boolean) {
  try {
    localStorage.setItem(SERIES_KEY, on ? '1' : '0')
  } catch {
    // sans conséquence : le mode série reste actif jusqu'à la fermeture de l'appli
  }
}

interface SeriesProgress {
  count: number
  last: { id: number; label: string }
}

/** Objets déjà dans la collection pour ces N# (détection des doublons). */
function useOwned(numistaIds: number[]): Record<number, OwnedItem[]> {
  const key = [...new Set(numistaIds)].sort((a, b) => a - b).join(',')
  const [owned, setOwned] = useState<{ key: string; data: Record<number, OwnedItem[]> }>({ key: '', data: {} })
  useEffect(() => {
    if (!key) return
    let cancelled = false
    api
      .owned(key.split(',').map(Number))
      .then((data) => !cancelled && setOwned({ key, data }))
      .catch(() => undefined) // information de confort : on ignore une erreur réseau
    return () => {
      cancelled = true
    }
  }, [key])
  return owned.key === key ? owned.data : {}
}

/** Liste de fiches Numista, chacune marquée si elle est déjà dans la collection. */
function CandidateList({
  candidates,
  onChoose,
  pick,
}: {
  candidates: Candidate[]
  onChoose: (c: Candidate) => void
  pick?: Rerank | null
}) {
  const owned = useOwned(candidates.map((c) => c.id))
  return (
    <ul className="candidates">
      {candidates.map((c) => (
        <li key={c.id}>
          <CandidateCard
            candidate={c}
            owned={(owned[c.id] ?? []).reduce((n, i) => n + i.quantity, 0)}
            onChoose={() => onChoose(c)}
            pick={pick?.best_id === c.id ? pick : undefined}
          />
        </li>
      ))}
    </ul>
  )
}

type RerankState = { key: string; data: Rerank | null; error: string | null }

/** Re-classement visuel des candidats (seconde analyse, lancée après l'affichage des résultats).
 *  Renvoie les candidats réordonnés : le choix du modèle d'abord. */
function useRerank(result: IdentifyResponse, candidates: Candidate[]) {
  const enabled = result.rerank && result.extraction != null && candidates.length >= 2
  const key = enabled ? `${result.upload_id}:${candidates.map((c) => c.id).join(',')}` : ''
  const [state, setState] = useState<RerankState>({ key: '', data: null, error: null })
  useEffect(() => {
    if (!key) return
    const controller = new AbortController()
    api
      .rerank(result.upload_id, candidates, controller.signal)
      .then((data) => setState({ key, data, error: null }))
      .catch((e: Error) => !controller.signal.aborted && setState({ key, data: null, error: e.message }))
    return () => controller.abort()
    // `key` résume upload_id et candidats : inutile de relancer pour un nouveau tableau identique
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  const current = state.key === key && key ? state : null
  const data = current?.data ?? null
  const ordered = data
    ? [...candidates].sort((a, b) => data.order.indexOf(a.id) - data.order.indexOf(b.id))
    : candidates
  return { running: Boolean(key) && !current, data, error: current?.error ?? null, ordered }
}

/** Champs corrigeables de la lecture de l'IA, en texte (valeurs des <input>). */
function draftOf(extraction: Extraction | null) {
  return {
    country: extraction?.country ?? '',
    denomination_text: extraction?.denomination_text ?? '',
    year: extraction?.year != null ? String(extraction.year) : '',
  }
}

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
  const [series, setSeries] = useState(loadSeriesFlag)
  const [progress, setProgress] = useState<SeriesProgress | null>(null)
  const [carried, setCarried] = useState<Partial<ItemFields>>({})

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
      ...(series ? carried : {}),
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
    // Saisie manuelle sans identification : les photos n'ont pas encore été envoyées.
    // L'objet existe déjà : un échec ici ne doit pas remonter au formulaire (le renvoyer
    // créerait un doublon) ; on le signale et la photo pourra être ajoutée depuis la fiche.
    let photoError: string | null = null
    if (!result) {
      for (const [file, side] of [[obverse, 'obverse'], [reverse, 'reverse']] as const) {
        if (!file) continue
        try {
          await api.addPhoto(item.id, file, side)
        } catch (e) {
          photoError = `Objet enregistré, mais photo non ajoutée : ${(e as Error).message}`
        }
      }
    }
    if (series) {
      nextInSeries(item.id, itemTitle(item), fields)
      if (photoError) setError(photoError)
      return
    }
    navigate(`/objet/${item.id}`, { replace: true, state: { added: true, photoError } })
  }

  /** Doublon : +1 exemplaire sur l'objet existant (les nouvelles photos ne sont pas gardées). */
  async function addCopy(owned: OwnedItem) {
    const item = await api.incrementItem(owned.id, result?.upload_id ?? null)
    if (series) return nextInSeries(item.id, `${itemTitle(item)} (×${item.quantity})`, null)
    navigate(`/objet/${item.id}`, { replace: true, state: { incremented: item.quantity } })
  }

  function nextInSeries(id: number, label: string, fields: Partial<ItemFields> | null) {
    if (fields) {
      setCarried(Object.fromEntries(CARRIED_OVER.map((key) => [key, fields[key] ?? null])))
      if (fields.tags) existingTags(true) // nouveaux tags proposés dès l'objet suivant
    }
    setProgress((p) => ({ count: (p?.count ?? 0) + 1, last: { id, label } }))
    restart()
    window.scrollTo(0, 0)
  }

  function toggleSeries(on: boolean) {
    setSeries(on)
    saveSeriesFlag(on)
    if (!on) {
      setProgress(null)
      setCarried({})
    }
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
        {step.initial.numista_id != null && (
          <Duplicates numistaId={step.initial.numista_id} year={step.initial.year ?? null} onAddCopy={addCopy} />
        )}
        <ItemForm
          initial={step.initial}
          submitLabel={series ? 'Ajouter et passer au suivant' : 'Ajouter à la collection'}
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
      {progress && (
        <p className="success" role="status">
          ✓ <Link to={`/objet/${progress.last.id}`}>{progress.last.label}</Link> enregistré.{' '}
          {plural(progress.count, 'objet')} dans cette série.
        </p>
      )}
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
      <label className="switch">
        <input type="checkbox" checked={series} onChange={(e) => toggleSeries(e.target.checked)} />
        <span>
          <strong>Mode série</strong>
          <span className="muted small block">
            Enchaîner plusieurs objets : après chaque ajout, retour ici, avec l'emplacement, les tags et la date
            d'acquisition repris de l'objet précédent.
          </span>
        </span>
      </label>
      {progress && (
        <div className="actions">
          <button className="btn" onClick={() => navigate('/')}>
            Terminer la série
          </button>
        </div>
      )}
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
  const initialDraft = draftOf(extraction)
  const [draft, setDraft] = useState(initialDraft)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const candidates = result.match?.candidates ?? []
  const rerank = useRerank(result, candidates)
  const changed = extraction && EDITABLE.some(({ key }) => draft[key] !== initialDraft[key])

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
          {rerank.running && (
            <p className="muted small rerank-status" role="status">
              <span className="spinner small" aria-hidden /> Comparaison visuelle avec les fiches…
            </p>
          )}
          {rerank.data?.verdict === 'none' && (
            <p className="muted small" role="status">
              Comparaison visuelle : aucune fiche ne correspond vraiment selon l'IA
              {rerank.data.reason ? ` (${rerank.data.reason})` : ''}.
            </p>
          )}
          {rerank.data?.verdict === 'unsure' && (
            <p className="muted small" role="status">
              Comparaison visuelle : l'IA hésite entre plusieurs fiches, à toi de trancher.
            </p>
          )}
          {rerank.error && <p className="muted small">{rerank.error}</p>}
          <CandidateList candidates={rerank.ordered} onChoose={onChoose} pick={rerank.data} />
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
        <CandidateList candidates={results} onChoose={onChoose} />
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

/** Même fiche Numista déjà dans la collection : proposer +1 exemplaire plutôt qu'un nouvel objet.
 *  Une fiche couvre souvent plusieurs années : l'année identique est mise en avant. */
function Duplicates({
  numistaId,
  year,
  onAddCopy,
}: {
  numistaId: number
  year: number | null
  onAddCopy: (item: OwnedItem) => Promise<void>
}) {
  const items = useOwned([numistaId])[numistaId] ?? []
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (!items.length) return null

  async function add(item: OwnedItem) {
    setBusy(item.id)
    setError(null)
    try {
      await onAddCopy(item)
    } catch (e) {
      setError((e as Error).message)
      setBusy(null)
    }
  }

  const sameYear = items.some((i) => year != null && i.year === year)
  return (
    <section className="panel duplicates">
      <h2>Déjà dans ta collection</h2>
      <p className="muted small">
        {sameYear
          ? 'Même fiche et même année : sans doute un exemplaire de plus.'
          : year == null
            ? 'Même fiche Numista (une fiche couvre souvent plusieurs années) : vérifie l\'année avant d\'ajouter un exemplaire.'
            : 'Même fiche Numista, mais pas la même année : vérifie avant d\'ajouter un exemplaire.'}
      </p>
      <ul className="owned">
        {items.map((item) => {
          const details = [item.year ?? 'année ?', item.mint_mark, item.condition, `×${item.quantity}`]
          return (
            <li key={item.id} className={year != null && item.year === year ? 'same' : ''}>
              {item.thumb_url ? <img src={item.thumb_url} alt="" /> : <div className="no-photo small" aria-hidden>◎</div>}
              <span className="grow">
                <Link to={`/objet/${item.id}`}>{item.denomination ?? `Objet ${item.id}`}</Link>
                <span className="muted small block">{details.filter(Boolean).join(' · ')}</span>
              </span>
              <button className="btn small primary" disabled={busy != null} onClick={() => add(item)}>
                {busy === item.id ? '…' : '+1 exemplaire'}
              </button>
            </li>
          )
        })}
      </ul>
      <ErrorMessage message={error} />
      <p className="muted small">Ou bien enregistre-le comme un nouvel objet ci-dessous (autre année, autre état…).</p>
    </section>
  )
}
