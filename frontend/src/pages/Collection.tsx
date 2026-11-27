import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type ItemList, type Stats } from '../api.ts'
import { ErrorMessage } from '../components/ui.tsx'
import { OBJECT_TYPES, itemTitle, mainThumb } from '../format.ts'

const SORTS = [
  { value: '-created_at', label: 'Ajouts récents' },
  { value: 'year', label: 'Année croissante' },
  { value: '-year', label: 'Année décroissante' },
  { value: 'country', label: 'Pays' },
  { value: '-estimated_value', label: 'Valeur estimée' },
]

export default function Collection() {
  // Les filtres vivent dans l'URL : retour arrière depuis une fiche = mêmes filtres
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const objectType = params.get('type') ?? ''
  const country = params.get('pays') ?? ''
  const sort = params.get('tri') ?? '-created_at'

  const [search, setSearch] = useState(q)
  const [list, setList] = useState<ItemList | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [error, setError] = useState<string | null>(null)

  function setParam(key: string, value: string) {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set(key, value)
        else next.delete(key)
        return next
      },
      { replace: true },
    )
  }

  // Recherche texte : attendre une courte pause de frappe avant d'interroger le serveur
  useEffect(() => {
    const timer = setTimeout(() => {
      if (search !== q) setParam('q', search.trim())
    }, 300)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search])

  useEffect(() => {
    let cancelled = false
    api
      .listItems({ q, country, object_type: objectType, sort })
      .then((data) => !cancelled && (setList(data), setError(null)))
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [q, country, objectType, sort])

  useEffect(() => {
    api.stats().then(setStats).catch(() => setStats(null))
  }, [])

  const filtered = Boolean(q || country || objectType)

  return (
    <>
      <header className="page-header">
        <h1>Ma collection</h1>
        {stats && (
          <p className="muted">
            {stats.items} objet{stats.items > 1 ? 's' : ''} · {stats.total_quantity} exemplaire
            {stats.total_quantity > 1 ? 's' : ''} · {stats.countries} pays
          </p>
        )}
      </header>

      <div className="filters">
        <input
          type="search"
          placeholder="Rechercher (pays, valeur, notes…)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          enterKeyHint="search"
        />
        <div className="chips">
          <button className={!objectType ? 'chip active' : 'chip'} onClick={() => setParam('type', '')}>
            Tout
          </button>
          {OBJECT_TYPES.map((t) => (
            <button
              key={t.value}
              className={objectType === t.value ? 'chip active' : 'chip'}
              onClick={() => setParam('type', objectType === t.value ? '' : t.value)}
            >
              {t.plural}
            </button>
          ))}
        </div>
        <div className="row">
          <select value={country} onChange={(e) => setParam('pays', e.target.value)} aria-label="Pays">
            <option value="">Tous les pays</option>
            {stats &&
              Object.entries(stats.by_country).map(([name, count]) => (
                <option key={name} value={name}>
                  {name} ({count})
                </option>
              ))}
          </select>
          <select value={sort} onChange={(e) => setParam('tri', e.target.value)} aria-label="Tri">
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <ErrorMessage message={error} />

      {list && list.items.length === 0 && (
        <div className="empty">
          {filtered ? (
            <p>Aucun objet ne correspond à ces filtres.</p>
          ) : (
            <>
              <p>La collection est vide.</p>
              <Link className="btn primary" to="/ajouter">
                Ajouter un premier objet
              </Link>
            </>
          )}
        </div>
      )}

      {list && list.items.length > 0 && (
        <>
          {filtered && <p className="muted small">{list.total} résultat{list.total > 1 ? 's' : ''}</p>}
          <ul className="grid">
            {list.items.map((item) => {
              const thumb = mainThumb(item)
              return (
                <li key={item.id}>
                  <Link to={`/objet/${item.id}`} className="card">
                    {thumb ? (
                      <img src={thumb} alt="" loading="lazy" />
                    ) : (
                      <div className="no-photo" aria-hidden>
                        ◎
                      </div>
                    )}
                    {item.quantity > 1 && <span className="badge">×{item.quantity}</span>}
                    <div className="card-text">
                      <strong>{itemTitle(item)}</strong>
                      <span className="muted">{item.country ?? 'Pays inconnu'}</span>
                    </div>
                  </Link>
                </li>
              )
            })}
          </ul>
        </>
      )}

      <Link to="/ajouter" className="fab" aria-label="Ajouter un objet">
        ＋
      </Link>
    </>
  )
}
