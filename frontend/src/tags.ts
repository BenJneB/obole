// Tags d'un objet : « a, b, c » (format stocké par le backend, voir models.normalize_tags).
import { api, type TagCount } from './api.ts'

/** Découpe « a, b, c » en liste ; même règle que le backend (models.normalize_tags). */
export function splitTags(value: string | null | undefined): string[] {
  const seen = new Map<string, string>()
  for (const part of (value ?? '').split(',')) {
    const tag = part.trim().replace(/\s+/g, ' ')
    if (tag && !seen.has(tag.toLowerCase())) seen.set(tag.toLowerCase(), tag)
  }
  return [...seen.values()]
}

export const joinTags = (tags: string[]) => tags.join(', ')

// Tags existants : chargés une fois par session de l'appli, rafraîchis après chaque ajout
let cachedTags: Promise<TagCount[]> | null = null
export function existingTags(refresh = false): Promise<TagCount[]> {
  if (!cachedTags || refresh) {
    cachedTags = api.tags().catch((): TagCount[] => {
      cachedTags = null // réessayer au prochain appel
      return []
    })
  }
  return cachedTags
}
