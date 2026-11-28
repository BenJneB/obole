// Client de l'API du backend (mêmes noms de champs que backend/app/schemas.py).

export type ObjectType = 'coin' | 'banknote' | 'token' | 'unknown'
export type PhotoSide = 'obverse' | 'reverse' | 'other'
export type IdentificationSource = 'ai_numista' | 'numista' | 'manual'

export interface ItemFields {
  object_type: ObjectType
  country: string | null
  issuer_code: string | null
  denomination: string | null
  currency: string | null
  year: number | null
  mint_mark: string | null
  numista_id: number | null
  numista_title: string | null
  numista_url: string | null
  condition: string | null
  quantity: number
  acquired_date: string | null
  acquired_price: number | null
  estimated_value: number | null
  storage_location: string | null
  notes: string | null
  tags: string | null
  identification_source: IdentificationSource
}

export interface Photo {
  id: number
  side: PhotoSide
  url: string
  thumb_url: string
}

export interface Item extends ItemFields {
  id: number
  created_at: string
  updated_at: string
  ai_extraction: Extraction | null
  photos: Photo[]
  thumb_url: string | null // miniature principale (avers de préférence), calculée par le serveur
}

export interface ItemList {
  total: number
  items: Item[]
}

/** Lecture des photos par le modèle (schéma imposé à Ollama, voir services/vision.py). */
export interface Extraction {
  object_type: ObjectType
  country: string | null
  country_confidence: number
  currency: string | null
  denomination_value: number | null
  denomination_text: string | null
  year: number | null
  year_visible: boolean
  inscriptions: string[]
  language_of_inscriptions: string | null
  description: string
  is_commemorative_guess: boolean
  search_keywords: string[]
}

export interface Candidate {
  id: number
  title: string
  issuer: string | null
  years: string
  url: string
  obverse_thumbnail: string | null
  reverse_thumbnail: string | null
}

export interface Match {
  issuer_code: string | null
  issuer_name: string | null
  issuer_score: number
  queries: Record<string, unknown>[]
  candidates: Candidate[]
}

export interface Quota {
  used: number
  limit: number
  remaining: number
}

export interface IdentifyResponse {
  upload_id: string
  sides: PhotoSide[]
  extraction: Extraction | null
  model: string | null
  seconds: number | null
  match: Match | null
  errors: string[]
  quota: Quota
  /** Re-classement visuel disponible : appeler ensuite api.rerank (RERANK=true sur le serveur). */
  rerank: boolean
}

/** Choix du modèle parmi les candidats, d'après leurs miniatures (POST /api/rerank). */
export interface Rerank {
  order: number[]
  /** match : choix confirmé par deux passes (ordre inversé) ; none : aucune fiche ; unsure : l'IA hésite */
  verdict: 'match' | 'none' | 'unsure'
  best_id: number | null
  confidence: number
  reason: string
  model: string
  seconds: number
  missing_thumbnails: number
}

/** Une fiche Numista comparée entre Obole et ma collection sur Numista. */
export interface TypeComparison {
  numista_id: number
  title: string
  url: string
  numista_quantity: number
  obole_quantity: number
  obole_item_ids: number[]
}

export interface CollectionComparison {
  user_id: number
  only_obole: TypeComparison[]
  only_numista: TypeComparison[]
  different: TypeComparison[]
  same: number
  obole_without_numista: number
  numista_entries: number
  unreadable_entries: number
  quota: Quota
}

export interface MatchResponse {
  match: Match | null
  errors: string[]
  quota: Quota
}

export interface Stats {
  items: number
  total_quantity: number
  countries: number
  by_type: Record<string, number>
  by_country: Record<string, number>
  by_year: Record<string, number>
  without_year: number
  by_condition: Record<string, number>
  by_tag: Record<string, number>
  identified: number
  estimated_value_total: number
  acquired_price_total: number
}

export interface TagCount {
  tag: string
  count: number
}

/** Objet déjà présent pour une fiche Numista (GET /api/items/owned). */
export interface OwnedItem {
  id: number
  year: number | null
  mint_mark: string | null
  condition: string | null
  quantity: number
  denomination: string | null
  country: string | null
  thumb_url: string | null
}

export interface BackupStatus {
  enabled: boolean
  interval_hours: number
  keep: number
  directory: string
  last_run_at: string | null
  last_status: 'created' | 'unchanged' | 'error' | null
  last_error: string | null
  backups: { name: string; created_at: string; size: number }[]
}

export interface Health {
  status: 'ok' | 'degraded'
  ollama: { url: string; model: string; ok: boolean; version?: string; error?: string }
  numista: Quota & { configured: boolean }
  schema_version: number
}

const UNREACHABLE = 'Serveur injoignable : le PC est-il allumé, et Tailscale connecté sur le téléphone ?'

/** Message lisible à partir du corps JSON d'une réponse d'erreur FastAPI. */
function detailMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // Erreur de validation FastAPI : [{loc: [...], msg: "..."}]
    return detail.map((d) => `${(d.loc ?? []).slice(1).join('.')} : ${d.msg}`).join(' ; ')
  }
  return `Erreur du serveur (HTTP ${status})`
}

async function errorMessage(response: Response): Promise<string> {
  const body = await response.json().catch(() => null) // corps non JSON
  return detailMessage(body, response.status)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new Error(UNREACHABLE)
  }
  if (!response.ok) throw new Error(await errorMessage(response))
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}

export type ItemCreate = Partial<ItemFields> & {
  upload_id?: string | null
  ai_extraction?: Extraction | null
}

export const api = {
  /** `query` : paramètres de GET /api/items (q, country, sort, tag…), déjà encodés. */
  listItems(query: string, signal?: AbortSignal): Promise<ItemList> {
    const params = new URLSearchParams(query)
    params.set('limit', '1000')
    return request(`/api/items?${params}`, { signal })
  },
  getItem: (id: number) => request<Item>(`/api/items/${id}`),
  createItem: (body: ItemCreate) => request<Item>('/api/items', json('POST', body)),
  updateItem: (id: number, body: Partial<ItemFields>) => request<Item>(`/api/items/${id}`, json('PATCH', body)),
  deleteItem: (id: number) => request<void>(`/api/items/${id}`, { method: 'DELETE' }),
  incrementItem: (id: number, discardUploadId: string | null) =>
    request<Item>(`/api/items/${id}/increment`, json('POST', { by: 1, discard_upload_id: discardUploadId })),
  async owned(numistaIds: number[]): Promise<Record<number, OwnedItem[]>> {
    const ids = [...new Set(numistaIds)].slice(0, 100)
    if (!ids.length) return {}
    return request(`/api/items/owned?numista_ids=${ids.join(',')}`)
  },
  tags: () => request<TagCount[]>('/api/tags'),
  addPhoto(itemId: number, file: File, side: PhotoSide): Promise<Photo> {
    const form = new FormData()
    form.append('file', file)
    form.append('side', side)
    return request(`/api/items/${itemId}/photos`, { method: 'POST', body: form })
  },
  deletePhoto: (itemId: number, photoId: number) =>
    request<void>(`/api/items/${itemId}/photos/${photoId}`, { method: 'DELETE' }),
  rematch: (extraction: Extraction) => request<MatchResponse>('/api/match', json('POST', { extraction })),
  rerank: (uploadId: string, candidates: Candidate[], signal?: AbortSignal) =>
    request<Rerank>('/api/rerank', { ...json('POST', { upload_id: uploadId, candidates }), signal }),
  compareCollection: () => request<CollectionComparison>('/api/numista/collection/compare'),
  pushToNumista: (numistaId: number) =>
    request<{ numista_id: number; added: number; grade: string | null; quota: Quota }>(
      '/api/numista/collection/push',
      json('POST', { numista_id: numistaId }),
    ),
  importFromNumista: (numistaId: number) =>
    request<Item>('/api/numista/collection/import', json('POST', { numista_id: numistaId })),
  searchNumista(q: string, objectType?: ObjectType): Promise<Candidate[]> {
    const params = new URLSearchParams({ q, count: '20' })
    if (objectType && objectType !== 'unknown') params.set('object_type', objectType)
    return request(`/api/numista/search?${params}`) // même format que les candidats de /identify
  },
  stats: () => request<Stats>('/api/stats'),
  health: () => request<Health>('/api/health'),
  backups: () => request<BackupStatus>('/api/backups'),
  backupNow: () => request<BackupStatus>('/api/backups', { method: 'POST' }),
}

export type IdentifyProgress = { phase: 'upload'; ratio: number } | { phase: 'analysis' }

/** POST /api/identify via XMLHttpRequest, pour suivre l'envoi des photos (fetch ne le permet pas). */
export function identify(
  obverse: File,
  reverse: File | null,
  onProgress: (progress: IdentifyProgress) => void,
): { promise: Promise<IdentifyResponse>; abort: () => void } {
  const xhr = new XMLHttpRequest()
  const promise = new Promise<IdentifyResponse>((resolve, reject) => {
    const form = new FormData()
    form.append('obverse', obverse)
    if (reverse) form.append('reverse', reverse)
    xhr.open('POST', '/api/identify')
    xhr.responseType = 'json'
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress({ phase: 'upload', ratio: event.loaded / event.total })
    }
    xhr.upload.onload = () => onProgress({ phase: 'analysis' })
    xhr.onload = () => {
      if (xhr.status === 200) return resolve(xhr.response as IdentifyResponse)
      reject(new Error(detailMessage(xhr.response, xhr.status)))
    }
    xhr.onerror = () => reject(new Error(UNREACHABLE))
    xhr.onabort = () => reject(new Error('Identification annulée'))
    xhr.send(form)
  })
  return { promise, abort: () => xhr.abort() }
}
