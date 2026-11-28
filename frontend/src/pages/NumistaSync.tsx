import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type CollectionComparison, type TypeComparison } from '../api.ts'
import { ErrorMessage, Notice } from '../components/ui.tsx'
import { plural } from '../format.ts'

type Direction = 'push' | 'import'

/** Résultat d'une action sur une fiche : message affiché à la place du bouton. */
type Done = Record<number, string>

function Row({
  row,
  direction,
  done,
  busy,
  onAction,
}: {
  row: TypeComparison
  direction: Direction
  done: string | undefined
  busy: boolean
  onAction: (row: TypeComparison) => void
}) {
  const missing = Math.abs(row.obole_quantity - row.numista_quantity)
  return (
    <li>
      <span className="grow">
        <a href={row.url} target="_blank" rel="noreferrer">
          {row.title} ↗
        </a>
        <span className="muted small block">
          N# {row.numista_id} · Obole ×{row.obole_quantity} · Numista ×{row.numista_quantity}
          {row.obole_item_ids.map((id) => (
            <span key={id}>
              {' · '}
              <Link to={`/objet/${id}`}>objet {id}</Link>
            </span>
          ))}
        </span>
      </span>
      {done ? (
        <span className="done">✓ {done}</span>
      ) : (
        <button className="btn small primary" disabled={busy} onClick={() => onAction(row)}>
          {direction === 'push' ? `Ajouter sur Numista (+${missing})` : `Importer (+${missing})`}
        </button>
      )}
    </li>
  )
}

export default function NumistaSync() {
  const [comparison, setComparison] = useState<CollectionComparison | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [done, setDone] = useState<Done>({})

  async function compare() {
    setLoading(true)
    setError(null)
    try {
      setComparison(await api.compareCollection())
      setDone({})
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  async function act(row: TypeComparison, direction: Direction) {
    setBusy(row.numista_id)
    setError(null)
    try {
      if (direction === 'push') {
        const result = await api.pushToNumista(row.numista_id)
        setDone((d) => ({ ...d, [row.numista_id]: `${plural(result.added, 'exemplaire')} ajouté(s)` }))
      } else {
        const item = await api.importFromNumista(row.numista_id)
        setDone((d) => ({ ...d, [row.numista_id]: `objet ${item.id} créé` }))
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const toPush = comparison
    ? [...comparison.only_obole, ...comparison.different.filter((r) => r.obole_quantity > r.numista_quantity)]
    : []
  const toImport = comparison
    ? [...comparison.only_numista, ...comparison.different.filter((r) => r.obole_quantity < r.numista_quantity)]
    : []

  return (
    <>
      <header className="page-header">
        <h1>Collection Numista</h1>
        <p className="muted small">
          Compare Obole avec ta collection sur le site Numista, fiche par fiche (N#). Chaque action n'ajoute que les
          exemplaires manquants : rien n'est supprimé ni modifié, ni ici ni sur Numista.
        </p>
      </header>

      <div className="actions">
        <button className="btn primary" onClick={compare} disabled={loading}>
          {loading ? 'Comparaison…' : comparison ? 'Comparer à nouveau' : 'Comparer les collections'}
        </button>
        <Link className="btn" to="/reglages">
          Retour
        </Link>
      </div>
      <ErrorMessage message={error} />

      {comparison && (
        <>
          <section className="panel">
            <h2>Bilan</h2>
            <p className="small">
              {plural(comparison.same, 'fiche')} identique(s) des deux côtés · {comparison.numista_entries} objet(s)
              dans ta collection Numista.
            </p>
            {comparison.obole_without_numista > 0 && (
              <p className="muted small">
                {plural(comparison.obole_without_numista, 'objet')} d'Obole sans fiche Numista (N#) : non
                comparable(s).
              </p>
            )}
            {comparison.unreadable_entries > 0 && (
              <Notice>
                {plural(comparison.unreadable_entries, 'objet')} de Numista dans un format inattendu, ignoré(s).
              </Notice>
            )}
            <p className="muted small">
              Quota Numista : {comparison.quota.used} / {comparison.quota.limit} requêtes ce mois-ci (chaque
              comparaison ou action en utilise 1 ou 2).
            </p>
          </section>

          <section className="panel">
            <h2>À ajouter sur Numista</h2>
            {toPush.length === 0 ? (
              <p className="muted small">Rien : tout ce qui a un N# dans Obole est déjà sur Numista.</p>
            ) : (
              <ul className="sync-list">
                {toPush.map((row) => (
                  <Row
                    key={row.numista_id}
                    row={row}
                    direction="push"
                    done={done[row.numista_id]}
                    busy={busy != null}
                    onAction={(r) => act(r, 'push')}
                  />
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <h2>À importer dans Obole</h2>
            {toImport.length === 0 ? (
              <p className="muted small">Rien : tout ce qui est sur Numista est déjà dans Obole.</p>
            ) : (
              <>
                <p className="muted small">
                  L'objet importé reprend la fiche, la quantité manquante et, si possible, l'année et l'état ; ajoute
                  ensuite tes photos depuis sa fiche.
                </p>
                <ul className="sync-list">
                  {toImport.map((row) => (
                    <Row
                      key={row.numista_id}
                      row={row}
                      direction="import"
                      done={done[row.numista_id]}
                      busy={busy != null}
                      onAction={(r) => act(r, 'import')}
                    />
                  ))}
                </ul>
              </>
            )}
          </section>
        </>
      )}
    </>
  )
}
