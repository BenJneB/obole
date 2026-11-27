import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Stats } from '../api.ts'
import { ErrorMessage } from '../components/ui.tsx'
import { CONDITIONS, OBJECT_TYPES, formatEuro, isKnownCondition, plural } from '../format.ts'

interface Bar {
  label: string
  value: number
  /** Lien vers la collection filtrée sur cette barre. */
  to?: string
}

/** Barres horizontales (une seule série : une seule couleur, valeur écrite à côté). */
function BarList({ bars }: { bars: Bar[] }) {
  const max = Math.max(1, ...bars.map((b) => b.value))
  return (
    <ul className="bars">
      {bars.map((bar) => {
        const title = `${bar.label} : ${plural(bar.value, 'objet')}`
        const content = (
          <>
            <span className="bar-label">{bar.label}</span>
            <span className="bar-track" aria-hidden>
              <span className="bar-fill" style={{ width: `${(bar.value / max) * 100}%` }} />
            </span>
            <span className="bar-value">{bar.value}</span>
          </>
        )
        return (
          <li key={bar.label} title={title}>
            {bar.to ? (
              <Link to={bar.to} className="bar-row" aria-label={title}>
                {content}
              </Link>
            ) : (
              <div className="bar-row" aria-label={title}>
                {content}
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}

const q = (params: Record<string, string | number>) =>
  '/?' + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString()

/** Regroupe les années par décennie ou par siècle (ordre chronologique, périodes vides comprises). */
function periods(byYear: Record<string, number>, size: 10 | 100): Bar[] {
  const counts = new Map<number, number>()
  for (const [year, count] of Object.entries(byYear)) {
    const start = Math.floor(Number(year) / size) * size
    counts.set(start, (counts.get(start) ?? 0) + count)
  }
  if (!counts.size) return []
  const starts = [...counts.keys()]
  const [first, last] = [Math.min(...starts), Math.max(...starts)]
  // Trous comblés seulement s'ils restent lisibles (sinon : pièces antiques + modernes = 200 barres)
  const keys = (last - first) / size <= 40 ? range(first, last, size) : starts.sort((a, b) => a - b)
  return keys.map((start) => ({
    label: periodLabel(start, size),
    value: counts.get(start) ?? 0,
    to: q({ annee_min: start, annee_max: start + size - 1, tri: 'year' }),
  }))
}

function range(from: number, to: number, step: number): number[] {
  const out = []
  for (let v = from; v <= to; v += step) out.push(v)
  return out
}

function periodLabel(start: number, size: 10 | 100): string {
  if (size === 10) return start < 0 ? `${-start - 9} à ${-start} av. J.-C.` : `années ${start}`
  const century = start < 0 ? -start / 100 : start / 100 + 1
  return `${century}${century === 1 ? 'er' : 'e'} s.${start < 0 ? ' av. J.-C.' : ''}`
}

const TOP_COUNTRIES = 12

export default function StatsPage() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [size, setSize] = useState<10 | 100>(10)
  const [allCountries, setAllCountries] = useState(false)

  useEffect(() => {
    api
      .stats()
      .then(setStats)
      .catch((e: Error) => setError(e.message))
  }, [])

  if (!stats) {
    return (
      <>
        <header className="page-header">
          <h1>Statistiques</h1>
        </header>
        <ErrorMessage message={error} />
        {!error && <div className="spinner" aria-label="Chargement" />}
      </>
    )
  }
  if (!stats.items) {
    return (
      <>
        <header className="page-header">
          <h1>Statistiques</h1>
        </header>
        <p className="empty">Rien à compter pour l'instant : la collection est vide.</p>
      </>
    )
  }

  const countries = Object.entries(stats.by_country).map(([name, value]) => ({
    label: name,
    value,
    to: q({ pays: name }),
  }))
  const types = OBJECT_TYPES.filter((t) => stats.by_type[t.value]).map((t) => ({
    label: t.plural,
    value: stats.by_type[t.value],
    to: q({ type: t.value }),
  }))
  const conditions: Bar[] = [
    ...CONDITIONS.filter((c) => stats.by_condition[c.value]).map((c) => ({
      label: c.label,
      value: stats.by_condition[c.value],
      to: q({ etat: c.value }),
    })),
    // États saisis à la main hors de l'échelle
    ...Object.entries(stats.by_condition)
      .filter(([value]) => value && !isKnownCondition(value))
      .map(([value, count]) => ({ label: value, value: count, to: q({ etat: value }) })),
  ]
  const noCondition = stats.by_condition[''] ?? 0
  // Tri explicite : un tag numérique (« 2024 ») ne garderait pas l'ordre du serveur dans un objet JS
  const tags = Object.entries(stats.by_tag)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'fr'))
    .slice(0, 15)
    .map(([tag, value]) => ({ label: tag, value, to: q({ tag }) }))
  const byPeriod = periods(stats.by_year, size)
  const identifiedRatio = Math.round((stats.identified / stats.items) * 100)

  return (
    <>
      <header className="page-header">
        <h1>Statistiques</h1>
      </header>

      <ul className="tiles">
        <li>
          <strong>{stats.items}</strong>
          <span>objet{stats.items > 1 ? 's' : ''}</span>
        </li>
        <li>
          <strong>{stats.total_quantity}</strong>
          <span>exemplaire{stats.total_quantity > 1 ? 's' : ''}</span>
        </li>
        <li>
          <strong>{stats.countries}</strong>
          <span>pays</span>
        </li>
        <li>
          <strong>{identifiedRatio} %</strong>
          <span>avec fiche Numista</span>
        </li>
        {stats.estimated_value_total > 0 && (
          <li>
            <strong>{formatEuro(stats.estimated_value_total)}</strong>
            <span>valeur estimée</span>
          </li>
        )}
        {stats.acquired_price_total > 0 && (
          <li>
            <strong>{formatEuro(stats.acquired_price_total)}</strong>
            <span>dépensés</span>
          </li>
        )}
      </ul>
      {stats.identified < stats.items && (
        <p className="small">
          <Link to={q({ numista: 'non' })}>
            {plural(stats.items - stats.identified, 'objet')} sans fiche Numista
          </Link>
        </p>
      )}

      <section className="panel">
        <h2>Par type</h2>
        <BarList bars={types} />
      </section>

      {countries.length > 0 && (
        <section className="panel">
          <h2>Par pays</h2>
          <BarList bars={allCountries ? countries : countries.slice(0, TOP_COUNTRIES)} />
          {countries.length > TOP_COUNTRIES && (
            <button className="link small" onClick={() => setAllCountries((v) => !v)}>
              {allCountries ? 'Afficher moins' : `Afficher les ${countries.length} pays`}
            </button>
          )}
        </section>
      )}

      <section className="panel">
        <div className="panel-title">
          <h2>Par période</h2>
          <div className="chips" role="radiogroup" aria-label="Regrouper par">
            {([10, 100] as const).map((s) => (
              <button
                key={s}
                role="radio"
                aria-checked={size === s}
                className={size === s ? 'chip small active' : 'chip small'}
                onClick={() => setSize(s)}
              >
                {s === 10 ? 'Décennie' : 'Siècle'}
              </button>
            ))}
          </div>
        </div>
        {byPeriod.length > 0 ? <BarList bars={byPeriod} /> : <p className="muted small">Aucune année saisie.</p>}
        {stats.without_year > 0 && (
          <p className="muted small">
            + {plural(stats.without_year, 'objet')} sans année
          </p>
        )}
      </section>

      {conditions.length > 0 && (
        <section className="panel">
          <h2>Par état</h2>
          <BarList bars={conditions} />
          {noCondition > 0 && (
            <p className="small">
              <Link to={q({ etat: 'none' })}>
                + {plural(noCondition, 'objet')} sans état précisé
              </Link>
            </p>
          )}
        </section>
      )}

      {tags.length > 0 && (
        <section className="panel">
          <h2>Tags les plus utilisés</h2>
          <BarList bars={tags} />
        </section>
      )}
    </>
  )
}
