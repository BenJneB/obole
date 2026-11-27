import { useEffect, useState, type KeyboardEvent } from 'react'
import type { TagCount } from '../api.ts'
import { existingTags, joinTags, splitTags } from '../tags.ts'

/** Saisie de tags en puces, avec suggestions tirées des tags déjà utilisés. */
export default function TagInput({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const tags = splitTags(value)
  const [draft, setDraft] = useState('')
  const [known, setKnown] = useState<TagCount[]>([])

  useEffect(() => {
    let cancelled = false
    existingTags().then((list) => !cancelled && setKnown(list))
    return () => {
      cancelled = true
    }
  }, [])

  function add(raw: string) {
    const next = splitTags(joinTags([...tags, raw]))
    onChange(joinTags(next))
    setDraft('')
  }

  function remove(tag: string) {
    onChange(joinTags(tags.filter((t) => t !== tag)))
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if ((e.key === 'Enter' || e.key === ',') && draft.trim()) {
      e.preventDefault() // Entrée ne doit pas envoyer le formulaire
      add(draft)
    } else if (e.key === 'Backspace' && !draft && tags.length) {
      remove(tags[tags.length - 1])
    }
  }

  const lower = new Set(tags.map((t) => t.toLowerCase()))
  const needle = draft.trim().toLowerCase()
  const suggestions = known
    .filter((k) => !lower.has(k.tag.toLowerCase()) && (!needle || k.tag.toLowerCase().includes(needle)))
    .slice(0, 8)

  return (
    <div className="tag-input">
      {tags.length > 0 && (
        <ul className="tag-list">
          {tags.map((tag) => (
            <li key={tag}>
              <button type="button" className="tag removable" onClick={() => remove(tag)} aria-label={`Retirer ${tag}`}>
                {tag} <span aria-hidden>×</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <input
        value={draft}
        onChange={(e) => {
          // Virgule tapée au clavier du téléphone (pas toujours un événement keydown fiable)
          const text = e.target.value
          if (text.includes(',')) add(text)
          else setDraft(text)
        }}
        onKeyDown={onKeyDown}
        onBlur={() => draft.trim() && add(draft)}
        placeholder={tags.length ? 'Ajouter un tag' : 'ex. album 2, commémorative'}
        enterKeyHint="done"
        autoCapitalize="none"
      />
      {suggestions.length > 0 && (
        <ul className="tag-list suggestions" aria-label="Tags déjà utilisés">
          {suggestions.map((s) => (
            <li key={s.tag}>
              <button
                type="button"
                className="tag"
                // mousedown : ajouté avant que le champ perde le focus (sinon onBlur ajoute le brouillon)
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => add(s.tag)}
              >
                + {s.tag}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
