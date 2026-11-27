import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type BackupStatus, type Health, type Stats } from '../api.ts'
import { ErrorMessage } from '../components/ui.tsx'
import { formatDateTime } from '../format.ts'

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} Ko`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} Mo`
  return `${(bytes / 1024 ** 3).toFixed(2).replace('.', ',')} Go`
}

function intervalLabel(hours: number): string {
  if (hours % 24 === 0) return hours === 24 ? 'chaque jour' : `tous les ${hours / 24} jours`
  return hours === 1 ? 'chaque heure' : `toutes les ${hours} h`
}

function BackupPanel() {
  const [status, setStatus] = useState<BackupStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  useEffect(() => {
    api
      .backups()
      .then(setStatus)
      .catch((e: Error) => setError(e.message))
  }, [])

  async function backupNow() {
    setRunning(true)
    setError(null)
    try {
      setStatus(await api.backupNow())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRunning(false)
    }
  }

  const last = status?.backups[0]
  return (
    <section className="panel">
      <h2>Sauvegarde automatique</h2>
      {status && (
        <ul className="status">
          <li className={status.enabled && status.last_status !== 'error' ? 'ok' : 'ko'}>
            {status.enabled ? (
              <>
                <strong>Activée</strong> : {intervalLabel(status.interval_hours)} si la collection a changé, sur le
                PC. Les {status.keep} plus récentes sont gardées.
              </>
            ) : (
              <>
                <strong>Désactivée</strong> (BACKUP_INTERVAL_HOURS=0 dans .env)
              </>
            )}
            <span className="muted small block">Dossier : {status.directory}</span>
            {status.last_status === 'error' && (
              <span className="small block">Dernier essai en échec : {status.last_error}</span>
            )}
          </li>
        </ul>
      )}
      {last ? (
        <p className="small">
          Dernière sauvegarde : {formatDateTime(last.created_at)} ({formatSize(last.size)}) ·{' '}
          {status.backups.length} conservée{status.backups.length > 1 ? 's' : ''}
        </p>
      ) : (
        status && <p className="muted small">Aucune sauvegarde automatique pour l'instant.</p>
      )}
      <ErrorMessage message={error} />
      <button className="btn small" onClick={backupNow} disabled={running}>
        {running ? 'Sauvegarde en cours…' : 'Sauvegarder maintenant'}
      </button>
      <p className="muted small">
        Ces fichiers restent sur le PC : copie-les de temps en temps ailleurs (clé USB, cloud), ou règle BACKUP_DIR
        vers un dossier synchronisé.
      </p>
    </section>
  )
}

export default function Settings() {
  const [health, setHealth] = useState<Health | null>(null)
  const [stats, setStats] = useState<Stats | null>(null) // résumé de la collection
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
          <p>
            {stats.items} objet{stats.items > 1 ? 's' : ''}, {stats.total_quantity} exemplaire
            {stats.total_quantity > 1 ? 's' : ''}, {stats.countries} pays ·{' '}
            <Link to="/stats">Statistiques détaillées</Link>
          </p>
        </section>
      )}

      <BackupPanel />

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
            Télécharger une sauvegarde complète (base + photos, .zip)
          </a>
        </div>
      </section>

      <p className="muted small center">Obole {__APP_VERSION__}</p>
    </>
  )
}
