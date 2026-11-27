import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, type Item, type PhotoSide } from '../api.ts'
import ItemForm from '../components/ItemForm.tsx'
import { ConfirmButton, ErrorMessage, Notice } from '../components/ui.tsx'
import { CONDITIONS, formatDate, formatEuro, itemTitle, typeLabel } from '../format.ts'

const SIDE_LABELS: Record<PhotoSide, string> = { obverse: 'Avers', reverse: 'Revers', other: 'Photo' }

export default function ItemPage() {
  const { id } = useParams()
  const itemId = Number(id)
  const navigate = useNavigate()
  const location = useLocation()
  const justAdded = Boolean((location.state as { added?: boolean } | null)?.added)
  const [item, setItem] = useState<Item | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const photoInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let cancelled = false
    api
      .getItem(itemId)
      .then((data) => !cancelled && setItem(data))
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [itemId])

  if (error && !item) {
    return (
      <>
        <ErrorMessage message={error} />
        <Link to="/">Retour à la collection</Link>
      </>
    )
  }
  if (!item) return <div className="spinner" aria-label="Chargement" />

  async function addPhoto(file: File) {
    if (!item) return
    const side: PhotoSide = !item.photos.some((p) => p.side === 'obverse')
      ? 'obverse'
      : !item.photos.some((p) => p.side === 'reverse')
        ? 'reverse'
        : 'other'
    setBusy(true)
    setError(null)
    try {
      await api.addPhoto(item.id, file, side)
      setItem(await api.getItem(item.id))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function deletePhoto(photoId: number) {
    if (!item) return
    try {
      await api.deletePhoto(item.id, photoId)
      setItem({ ...item, photos: item.photos.filter((p) => p.id !== photoId) })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function deleteItem() {
    if (!item) return
    try {
      await api.deleteItem(item.id)
      navigate('/', { replace: true })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (editing) {
    return (
      <>
        <header className="page-header">
          <h1>Modifier</h1>
        </header>
        <ItemForm
          initial={item}
          submitLabel="Enregistrer"
          onCancel={() => setEditing(false)}
          onSubmit={async (fields) => {
            const changedNumista = fields.numista_id !== item.numista_id
            const updated = await api.updateItem(item.id, {
              ...fields,
              // Nouveau N# saisi à la main : l'ancien titre Numista ne correspond plus
              ...(changedNumista ? { numista_title: null } : {}),
            })
            setItem(updated)
            setEditing(false)
          }}
        />
        <section className="panel">
          <h2>Photos</h2>
          <ul className="photo-strip">
            {item.photos.map((photo) => (
              <li key={photo.id}>
                <img src={photo.thumb_url} alt={SIDE_LABELS[photo.side]} />
                <ConfirmButton
                  label="Supprimer"
                  confirmLabel="Confirmer"
                  className="btn danger small"
                  onConfirm={() => deletePhoto(photo.id)}
                />
              </li>
            ))}
          </ul>
        </section>
        <div className="actions">
          <ConfirmButton label="Supprimer l'objet" confirmLabel="Confirmer la suppression" onConfirm={deleteItem} />
        </div>
        <ErrorMessage message={error} />
      </>
    )
  }

  const condition = CONDITIONS.find((c) => c.value === item.condition)?.label ?? item.condition
  const rows: [string, string | null][] = [
    ['Type', typeLabel(item.object_type)],
    ['Pays', item.country],
    ['Valeur faciale', item.denomination],
    ['Devise', item.currency],
    ['Année', item.year != null ? String(item.year) : null],
    ['Atelier', item.mint_mark],
    ['État', condition],
    ['Quantité', String(item.quantity)],
    ['Acquis le', item.acquired_date ? formatDate(item.acquired_date) : null],
    ['Prix payé', item.acquired_price != null ? formatEuro(item.acquired_price) : null],
    ['Valeur estimée', item.estimated_value != null ? formatEuro(item.estimated_value) : null],
    ['Emplacement', item.storage_location],
    ['Tags', item.tags],
  ]

  return (
    <>
      <header className="page-header with-back">
        <button
          className="back"
          aria-label="Retour"
          // Fiche ouverte directement (lien, appli relancée) : pas d'historique, retour à la collection
          onClick={() => (location.key === 'default' ? navigate('/') : navigate(-1))}
        >
          ‹
        </button>
        <h1>{itemTitle(item)}</h1>
      </header>
      {justAdded && <Notice>Ajouté à la collection.</Notice>}

      {item.photos.length > 0 ? (
        <ul className="photo-strip large">
          {item.photos.map((photo) => (
            <li key={photo.id}>
              <a href={photo.url} target="_blank" rel="noreferrer">
                <img src={photo.thumb_url} alt={SIDE_LABELS[photo.side]} />
              </a>
              <span className="muted small">{SIDE_LABELS[photo.side]}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Aucune photo.</p>
      )}

      {item.numista_id && (
        <section className="panel">
          <h2>Numista</h2>
          <p>
            {item.numista_title && <strong>{item.numista_title}</strong>}
            <br />
            <a href={item.numista_url ?? `https://fr.numista.com/${item.numista_id}`} target="_blank" rel="noreferrer">
              Fiche N# {item.numista_id} sur Numista ↗
            </a>
          </p>
        </section>
      )}

      <dl className="fields">
        {rows
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
      </dl>
      {item.notes && <p className="notes">{item.notes}</p>}
      <p className="muted small">
        Ajouté le {formatDate(item.created_at)}
        {item.identification_source === 'ai_numista' && ' · identifié par photo'}
      </p>

      <ErrorMessage message={error} />
      <div className="actions">
        <button className="btn" disabled={busy} onClick={() => photoInput.current?.click()}>
          {busy ? 'Envoi…' : 'Ajouter une photo'}
        </button>
        <button className="btn primary" onClick={() => setEditing(true)}>
          Modifier
        </button>
      </div>
      <input
        ref={photoInput}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) addPhoto(file)
          e.target.value = ''
        }}
      />
    </>
  )
}
