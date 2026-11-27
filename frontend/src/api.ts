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
  estimated_value_total: number
}

export interface Health {
  status: 'ok' | 'degraded'
  ollama: { url: string; model: string; ok: boolean; version?: string; error?: string }
  numista: Quota & { configured: boolean }
  schema_version: number
}

/** Réponse brute de GET /api/numista/search (format Numista). */
interface NumistaSearch {
  count: number
  types: {
    id: number
    title: string
    issuer?: { name?: string }
    min_year?: number | null
    max_year?: number | null
    obverse_thumbnail?: string | null
    reverse_thumbnail?: string | null
  }[]
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const UNREACHABLE = 'Serveur injoignable : le PC est-il allumé, et Tailscale connecté sur le téléphone ?'

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json()
    const detail = body?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      // Erreur de validation FastAPI : [{loc: [...], msg: "..."}]
      return detail.map((d) => `${(d.loc ?? []).slice(1).join('.')} : ${d.msg}`).join(' ; ')
    }
  } catch {
    // corps non JSON
  }
  return `Erreur du serveur (HTTP ${response.status})`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new ApiError(0, UNREACHABLE)
  }
  if (!response.ok) throw new ApiError(response.status, await errorMessage(response))
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

export interface ItemQuery {
  q?: string
  country?: string
  object_type?: string
  sort?: string
}

export const api = {
  listItems(query: ItemQuery): Promise<ItemList> {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) if (value) params.set(key, value)
    params.set('limit', '1000')
    return request(`/api/items?${params}`)
  },
  getItem: (id: number) => request<Item>(`/api/items/${id}`),
  createItem: (body: ItemCreate) => request<Item>('/api/items', json('POST', body)),
  updateItem: (id: number, body: Partial<ItemFields>) => request<Item>(`/api/items/${id}`, json('PATCH', body)),
  deleteItem: (id: number) => request<void>(`/api/items/${id}`, { method: 'DELETE' }),
  addPhoto(itemId: number, file: File, side: PhotoSide): Promise<Photo> {
    const form = new FormData()
    form.append('file', file)
    form.append('side', side)
    return request(`/api/items/${itemId}/photos`, { method: 'POST', body: form })
  },
  deletePhoto: (itemId: number, photoId: number) =>
    request<void>(`/api/items/${itemId}/photos/${photoId}`, { method: 'DELETE' }),
  rematch: (extraction: Extraction) => request<MatchResponse>('/api/match', json('POST', { extraction })),
  async searchNumista(q: string, objectType?: ObjectType): Promise<Candidate[]> {
    const params = new URLSearchParams({ q, count: '20' })
    if (objectType && objectType !== 'unknown') params.set('object_type', objectType)
    const data = await request<NumistaSearch>(`/api/numista/search?${params}`)
    return data.types.map((t) => ({
      id: t.id,
      title: t.title,
      issuer: t.issuer?.name ?? null,
      years: formatYears(t.min_year, t.max_year),
      url: `https://fr.numista.com/${t.id}`,
      obverse_thumbnail: t.obverse_thumbnail ?? null,
      reverse_thumbnail: t.reverse_thumbnail ?? null,
    }))
  },
  stats: () => request<Stats>('/api/stats'),
  health: () => request<Health>('/api/health'),
}

function formatYears(min?: number | null, max?: number | null): string {
  if (min == null) return '?'
  return min === max || max == null ? String(min) : `${min}-${max}`
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
      const detail = xhr.response?.detail
      reject(new ApiError(xhr.status, typeof detail === 'string' ? detail : `Erreur du serveur (HTTP ${xhr.status})`))
    }
    xhr.onerror = () => reject(new ApiError(0, UNREACHABLE))
    xhr.onabort = () => reject(new ApiError(-1, 'Identification annulée'))
    xhr.send(form)
  })
  return { promise, abort: () => xhr.abort() }
}
