import { useState, type FormEvent, type ReactNode } from 'react'
import type { ItemFields, ObjectType } from '../api.ts'
import { CONDITIONS, OBJECT_TYPES } from '../format.ts'
import { ErrorMessage } from './ui.tsx'

// Champs du formulaire, tous en texte (valeur des <input>) ; convertis à l'envoi
const TEXT_FIELDS = [
  'country', 'denomination', 'currency', 'year', 'mint_mark', 'condition', 'quantity', 'acquired_date',
  'acquired_price', 'estimated_value', 'storage_location', 'notes', 'tags', 'numista_id',
] as const
type TextField = (typeof TEXT_FIELDS)[number]
type FormState = Record<TextField, string> & { object_type: ObjectType }

const INTEGER_FIELDS: TextField[] = ['year', 'quantity', 'numista_id']
const DECIMAL_FIELDS: TextField[] = ['acquired_price', 'estimated_value']

function toState(initial: Partial<ItemFields>): FormState {
  const state = { object_type: initial.object_type ?? 'coin' } as FormState
  for (const key of TEXT_FIELDS) {
    const value = initial[key]
    state[key] = value == null ? '' : String(value)
  }
  if (!state.quantity) state.quantity = '1'
  return state
}

/** Convertit le formulaire en champs d'API. Renvoie un message d'erreur si une valeur est invalide. */
function toFields(state: FormState): Partial<ItemFields> | string {
  const fields: Record<string, unknown> = { object_type: state.object_type }
  for (const key of TEXT_FIELDS) {
    const raw = state[key].trim()
    if (raw === '') {
      fields[key] = key === 'quantity' ? 1 : null
    } else if (INTEGER_FIELDS.includes(key)) {
      if (!/^-?\d+$/.test(raw)) return `« ${raw} » n'est pas un nombre entier`
      fields[key] = Number(raw)
    } else if (DECIMAL_FIELDS.includes(key)) {
      const value = Number(raw.replace(',', '.').replace(/\s|€/g, ''))
      if (Number.isNaN(value)) return `« ${raw} » n'est pas un montant`
      fields[key] = value
    } else {
      fields[key] = raw
    }
  }
  if ((fields.quantity as number) < 0) return 'La quantité ne peut pas être négative'
  return fields as Partial<ItemFields>
}

interface Props {
  initial: Partial<ItemFields>
  submitLabel: string
  onSubmit: (fields: Partial<ItemFields>) => Promise<void>
  onCancel?: () => void
  /** Bloc affiché en haut du formulaire (ex. fiche Numista choisie). */
  header?: ReactNode
}

export default function ItemForm({ initial, submitLabel, onSubmit, onCancel, header }: Props) {
  const [state, setState] = useState<FormState>(() => toState(initial))
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  // Détails repliés par défaut à la création (objectif : ajouter une pièce en moins d'une minute) ;
  // la devise, pré-remplie par l'IA, ne suffit pas à les ouvrir
  const hasDetails = Boolean(
    initial.mint_mark || initial.acquired_date || initial.acquired_price ||
      initial.estimated_value || initial.storage_location || initial.notes || initial.tags,
  )

  function set(key: TextField) {
    return (e: { target: { value: string } }) => setState((s) => ({ ...s, [key]: e.target.value }))
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const fields = toFields(state)
    if (typeof fields === 'string') return setError(fields)
    setSaving(true)
    setError(null)
    try {
      await onSubmit(fields)
    } catch (err) {
      setError((err as Error).message)
      setSaving(false)
    }
  }

  return (
    <form className="item-form" onSubmit={submit}>
      {header}
      <div className="chips" role="radiogroup" aria-label="Type d'objet">
        {OBJECT_TYPES.map((t) => (
          <button
            key={t.value}
            type="button"
            role="radio"
            aria-checked={state.object_type === t.value}
            className={state.object_type === t.value ? 'chip active' : 'chip'}
            onClick={() => setState((s) => ({ ...s, object_type: t.value }))}
          >
            {t.label}
          </button>
        ))}
      </div>

      <label>
        Pays
        <input value={state.country} onChange={set('country')} autoCapitalize="words" />
      </label>
      <div className="row">
        <label className="grow">
          Valeur faciale
          <input value={state.denomination} onChange={set('denomination')} placeholder="ex. 2 euro" />
        </label>
        <label className="narrow">
          Année
          <input value={state.year} onChange={set('year')} inputMode="numeric" />
        </label>
      </div>
      <div className="row">
        <label className="grow">
          État
          <select value={state.condition} onChange={set('condition')}>
            <option value="">Non précisé</option>
            {CONDITIONS.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
            {state.condition && !CONDITIONS.some((c) => c.value === state.condition) && (
              <option value={state.condition}>{state.condition}</option>
            )}
          </select>
        </label>
        <label className="narrow">
          Quantité
          <input value={state.quantity} onChange={set('quantity')} inputMode="numeric" />
        </label>
      </div>

      <details open={hasDetails}>
        <summary>Plus de détails</summary>
        <div className="row">
          <label className="grow">
            Devise
            <input value={state.currency} onChange={set('currency')} placeholder="ex. euro" />
          </label>
          <label className="narrow">
            Atelier
            <input value={state.mint_mark} onChange={set('mint_mark')} placeholder="ex. S" />
          </label>
        </div>
        <div className="row">
          <label className="grow">
            Prix payé (€)
            <input value={state.acquired_price} onChange={set('acquired_price')} inputMode="decimal" />
          </label>
          <label className="grow">
            Valeur estimée (€)
            <input value={state.estimated_value} onChange={set('estimated_value')} inputMode="decimal" />
          </label>
        </div>
        <label>
          Date d'acquisition
          <input type="date" value={state.acquired_date} onChange={set('acquired_date')} />
        </label>
        <label>
          Emplacement
          <input value={state.storage_location} onChange={set('storage_location')} placeholder="ex. album 2, page 4" />
        </label>
        <label>
          Tags (séparés par des virgules)
          <input value={state.tags} onChange={set('tags')} />
        </label>
        <label>
          Notes
          <textarea value={state.notes} onChange={set('notes')} rows={3} />
        </label>
        <label>
          N° Numista (N#)
          <input value={state.numista_id} onChange={set('numista_id')} inputMode="numeric" />
        </label>
      </details>

      <ErrorMessage message={error} />
      <div className="actions">
        {onCancel && (
          <button type="button" className="btn" onClick={onCancel} disabled={saving}>
            Annuler
          </button>
        )}
        <button type="submit" className="btn primary" disabled={saving}>
          {saving ? 'Enregistrement…' : submitLabel}
        </button>
      </div>
    </form>
  )
}
