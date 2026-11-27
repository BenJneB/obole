import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type ItemList, type Stats, type TagCount } from '../api.ts'
import { existingTags } from '../tags.ts'
import { ErrorMessage } from '../components/ui.tsx'
import { CONDITIONS, OBJECT_TYPES, itemTitle, mainThumb } from '../format.ts'

const SORTS = [
  { value: '-created_at', label: 'Ajouts récents' },
  { value: 'year', label: 'Année croissante' },
  { value: '-year', label: 'Année décroissante' },
  { value: 'country', label: 'Pays' },
  { value: '-estimated_value', label: 'Valeur estimée' },
  { value: '-quantity', label: 'Quantité' },
  { value: '-updated_at', label: 'Modifiés récemment' },
]

// Filtres avancés : paramètre d'URL (français, lisible) -> paramètre de l'API
const ADVANCED = {
  annee_min: 'year_min',
  annee_max: 'year_max',
  etat: 'condition',
  tag: 'tag',
  emplacement: 'storage',
  numista: 'identified',
} as const
type AdvancedKey = keyof typeof ADVANCED

export default function Collection() {
  // Les filtres vivent dans l'URL : retour arrière depuis une fiche = mêmes filtres
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const objectType = params.get('type') ?? ''
  const country = params.get('pays') ?? ''
  const sort = params.get('tri') ?? '-created_at'
  const advanced = Object.fromEntries(
    (Object.keys(ADVANCED) as AdvancedKey[]).map((key) => [key, params.get(key) ?? '']),
  ) as Record<AdvancedKey, string>
  const advancedCount = Object.values(advanced).filter(Boolean).length
  // Clé stable pour l'effet de chargement (l'objet `advanced` change à chaque rendu)
  const advancedKey = JSON.stringify(advanced)

  const [search, setSearch] = useState(q)
  const [list, setList] = useState<ItemList | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tags, setTags] = useState<TagCount[]>([])
  const [showFilters, setShowFilters] = useState(advancedCount > 0)

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
    const extra: Record<string, string> = {}
    for (const [key, value] of Object.entries(JSON.parse(advancedKey) as Record<AdvancedKey, string>)) {
      if (!value || (key.startsWith('annee') && !/^-?\d+$/.test(value))) continue // année en cours de saisie
      extra[ADVANCED[key as AdvancedKey]] = key === 'numista' ? String(value === 'oui') : value
    }
    api
      .listItems({ q, country, object_type: objectType, sort, ...extra })
      .then((data) => !cancelled && (setList(data), setError(null)))
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [q, country, objectType, sort, advancedKey])

  useEffect(() => {
    api.stats().then(setStats).catch(() => setStats(null))
    existingTags(true).then(setTags)
  }, [])

  function clearAdvanced() {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        for (const key of Object.keys(ADVANCED)) next.delete(key)
        return next
      },
      { replace: true },
    )
  }

  const filtered = Boolean(q || country || objectType || advancedCount)

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
        <button
          className={advancedCount ? 'chip active filters-toggle' : 'chip filters-toggle'}
          aria-expanded={showFilters}
          onClick={() => setShowFilters((v) => !v)}
        >
          Filtres{advancedCount ? ` (${advancedCount})` : ''} {showFilters ? '▴' : '▾'}
        </button>
        {showFilters && (
          <div className="panel advanced-filters">
            <div className="row">
              <label>
                Année min.
                <input
                  value={advanced.annee_min}
                  onChange={(e) => setParam('annee_min', e.target.value.replace(/[^\d-]/g, ''))}
                  inputMode="numeric"
                  placeholder="ex. 1900"
                />
              </label>
              <label>
                Année max.
                <input
                  value={advanced.annee_max}
                  onChange={(e) => setParam('annee_max', e.target.value.replace(/[^\d-]/g, ''))}
                  inputMode="numeric"
                  placeholder="ex. 1999"
                />
              </label>
            </div>
            <div className="row">
              <label>
                État
                <select value={advanced.etat} onChange={(e) => setParam('etat', e.target.value)}>
                  <option value="">Tous</option>
                  {CONDITIONS.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                  <option value="none">Non précisé</option>
                </select>
              </label>
              <label>
                Fiche Numista
                <select value={advanced.numista} onChange={(e) => setParam('numista', e.target.value)}>
                  <option value="">Peu importe</option>
                  <option value="oui">Avec fiche</option>
                  <option value="non">Sans fiche (à identifier)</option>
                </select>
              </label>
            </div>
            <label>
              Tag
              <select value={advanced.tag} onChange={(e) => setParam('tag', e.target.value)}>
                <option value="">Tous</option>
                {tags.map((t) => (
                  <option key={t.tag} value={t.tag}>
                    {t.tag} ({t.count})
                  </option>
                ))}
                {advanced.tag && !tags.some((t) => t.tag.toLowerCase() === advanced.tag.toLowerCase()) && (
                  <option value={advanced.tag}>{advanced.tag}</option>
                )}
              </select>
            </label>
            <label>
              Emplacement (contient)
              <input
                value={advanced.emplacement}
                onChange={(e) => setParam('emplacement', e.target.value)}
                placeholder="ex. album 2"
              />
            </label>
            {advancedCount > 0 && (
              <button className="link small" onClick={clearAdvanced}>
                Effacer ces filtres
              </button>
            )}
          </div>
        )}
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
