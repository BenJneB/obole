import type { Item, ItemFields, ObjectType } from './api'

export const OBJECT_TYPES: { value: ObjectType; label: string; plural: string }[] = [
  { value: 'coin', label: 'Pièce', plural: 'Pièces' },
  { value: 'banknote', label: 'Billet', plural: 'Billets' },
  { value: 'token', label: 'Jeton / médaille', plural: 'Jetons' },
]

export function typeLabel(type: ObjectType): string {
  return OBJECT_TYPES.find((t) => t.value === type)?.label ?? 'Inconnu'
}

// Échelle française des états de conservation, du moins bon au meilleur
export const CONDITIONS: { value: string; label: string }[] = [
  { value: 'B', label: 'B : Beau' },
  { value: 'TB', label: 'TB : Très beau' },
  { value: 'TTB', label: 'TTB : Très très beau' },
  { value: 'SUP', label: 'SUP : Superbe' },
  { value: 'SPL', label: 'SPL : Splendide' },
  { value: 'UNC', label: 'UNC / FDC : Fleur de coin' },
  { value: 'BE', label: 'BE : Belle épreuve' },
]

/** Titre court d'un objet : « 2 euro 2012 » ou le titre Numista à défaut. */
export function itemTitle(item: Pick<ItemFields, 'denomination' | 'year' | 'numista_title'>): string {
  const parts = [item.denomination, item.year].filter((p) => p != null && p !== '')
  if (parts.length) return parts.join(' ')
  return item.numista_title ?? 'Objet sans nom'
}

export function mainThumb(item: Item): string | null {
  const photo = item.photos.find((p) => p.side === 'obverse') ?? item.photos[0]
  return photo?.thumb_url ?? null
}

const euro = new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'EUR' })
export const formatEuro = (value: number) => euro.format(value)

export function formatDate(iso: string): string {
  return new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`).toLocaleDateString('fr-FR')
}

export function formatDateTime(iso: string): string {
  // Horodatage local du serveur (sans fuseau) : affiché tel quel
  return new Date(iso).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
}
