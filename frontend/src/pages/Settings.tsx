import { useCallback, useEffect, useState } from 'react'
import { api, type Health, type Stats } from '../api.ts'
import { ErrorMessage } from '../components/ui.tsx'
import { OBJECT_TYPES, formatEuro } from '../format.ts'

export default function Settings() {
  const [health, setHealth] = useState<Health | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [h, s] = await Promise.all([api.health(), api.stats()])
      setHealth(h)
      setStats(s)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- chargement initial
    refresh()
  }, [refresh])

  const quota = health?.numista
  const quotaRatio = quota ? quota.used / quota.limit : 0

  return (
    <>
      <header className="page-header">
        <h1>Réglages</h1>
      </header>
      <ErrorMessage message={error} />

      <section className="panel">
        <h2>Serveur</h2>
        {health ? (
          <ul className="status">
            <li className={health.ollama.ok ? 'ok' : 'ko'}>
              <strong>IA locale (Ollama)</strong> : {health.ollama.ok ? 'prête' : 'indisponible'}
              <br />
              <span className="muted small">
                Modèle {health.ollama.model}
                {health.ollama.version && ` · Ollama ${health.ollama.version}`}
              </span>
              {health.ollama.error && <span className="small block">{health.ollama.error}</span>}
            </li>
            <li className={quota?.configured ? 'ok' : 'ko'}>
              <strong>Numista</strong> : {quota?.configured ? 'clé configurée' : 'clé API absente du fichier .env'}
              {quota && (
                <>
                  <div className="meter" aria-hidden>
                    <div style={{ width: `${Math.min(100, quotaRatio * 100)}%` }} className={quotaRatio > 0.8 ? 'high' : ''} />
                  </div>
                  <span className="muted small">
                    {quota.used} / {quota.limit} requêtes ce mois-ci ({quota.remaining} restantes)
                  </span>
                </>
              )}
            </li>
          </ul>
        ) : (
          !error && <p className="muted">Chargement…</p>
        )}
        <button className="btn small" onClick={refresh} disabled={loading}>
          {loading ? 'Vérification…' : 'Vérifier à nouveau'}
        </button>
        {health && !health.ollama.ok && (
          <p className="muted small">
            Sans IA, l'ajout reste possible par recherche Numista ou saisie manuelle.
          </p>
        )}
      </section>

      {stats && (
        <section className="panel">
          <h2>Collection</h2>
          <ul className="plain">
            <li>
              {stats.items} objet{stats.items > 1 ? 's' : ''}, {stats.total_quantity} exemplaire
              {stats.total_quantity > 1 ? 's' : ''}, {stats.countries} pays
            </li>
            {OBJECT_TYPES.filter((t) => stats.by_type[t.value]).map((t) => (
              <li key={t.value}>
                {t.plural} : {stats.by_type[t.value]}
              </li>
            ))}
            {stats.estimated_value_total > 0 && <li>Valeur estimée : {formatEuro(stats.estimated_value_total)}</li>}
          </ul>
        </section>
      )}

      <section className="panel">
        <h2>Exporter et sauvegarder</h2>
        <p className="muted small">Tes données t'appartiennent : télécharge-les quand tu veux.</p>
        <div className="actions vertical">
          <a className="btn" href="/api/export.csv" download>
            Export CSV (Excel)
          </a>
          <a className="btn" href="/api/export.json" download>
            Export JSON
          </a>
          <a className="btn" href="/api/backup.zip" download>
            Sauvegarde complète (base + photos, .zip)
          </a>
        </div>
      </section>

      <p className="muted small center">Obole {__APP_VERSION__}</p>
    </>
  )
}
