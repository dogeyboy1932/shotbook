// Thin fetch wrapper for a UI-first architecture:
// - book/paragraph data comes directly from Supabase REST when configured
// - planning/render work is sent directly to the VM endpoint when configured
// - the legacy relative /api path remains as a fallback for local development
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL ?? ''
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY ?? ''
const VM_BASE_URL = import.meta.env.VITE_VM_BASE_URL ?? ''

export interface BookSummary {
  book_id: number
  title: string
  author: string | null
  ingestion_status: string
  paragraph_count: number
}

export interface Paragraph {
  paragraph_id: number
  sequence_index: number
  chapter_number: number
  raw_text: string
}

export interface DialogueLine {
  character_id: number
  character_name: string
  line: string
  emotion: string
  delivery: string
}

export interface CharacterContext {
  character_id: number
  name: string
  visual_description: string
  voice_description: string
  voice_reference_audio_uri: string | null
  emotional_state: string | null
  profile: Record<string, unknown>
}

export interface LocationContext {
  location_id: number
  name: string
  visual_description: string
  lighting_state: string | null
  ambient_sfx_prompt: string
  profile: Record<string, unknown>
}

export interface GenerationContext {
  paragraph_id: number
  book_id: number
  sequence_index: number
  chapter_number: number
  raw_text: string
  camera_framing: string
  action_summary: string
  characters: CharacterContext[]
  location: LocationContext | null
  dialogue_script: DialogueLine[]
  sfx_prompts: string[]
  // Dropped by the Supabase resolve_contexts RPC; the UI renders structured fields.
  narrative_context?: string
}

export interface VideoShot {
  shot_id: string
  camera: string
  action: string
  light: string
  continuity: 'continuous_frame' | 'cut_same_scene' | 'cut_new_scene'
  prompt: string
  audio_prompt: string
}

export interface VideoWorld {
  characters: Record<string, string>
  character_status: Record<string, string>
  location: string | null
  atmosphere: string | null
  look: string
}

export interface VideoPlan {
  world: VideoWorld
  shots: VideoShot[]
  negative_prompt: string
}

export interface ComposedScene {
  book_id: number
  paragraph_ids: number[]
  sequence_index_range: [number, number]
  selected_text: string
  characters: CharacterContext[]
  location: LocationContext | null
  dialogue_script: DialogueLine[]
  sfx_prompts: string[]
  camera_framing: string
  action_summary: string
  video: VideoPlan | null
  audio_prompt: string
}

function buildUrl(path: string): string {
  if (path.startsWith('/rest/v1/')) {
    return `${SUPABASE_URL}${path}`
  }
  if (VM_BASE_URL) {
    return `${VM_BASE_URL}${path}`
  }
  return `${API_BASE_URL}${path}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isSupabaseRest = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY && path.startsWith('/rest/v1/'))
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (isSupabaseRest) {
    headers.apikey = SUPABASE_ANON_KEY
    headers.Authorization = `Bearer ${SUPABASE_ANON_KEY}`
  }

  const response = await fetch(buildUrl(path), {
    headers,
    ...init,
  })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`${init?.method ?? 'GET'} ${path} failed (${response.status}): ${body}`)
  }
  return response.json() as Promise<T>
}

export interface GenerateVideoResponse {
  job_id: string
  scene: ComposedScene
}

export interface VideoJob {
  job_id: string
  status: 'planned' | 'running' | 'done' | 'failed'
  video_url: string | null
  stream_url: string | null
  error: string | null
}

// Resolve a highlighted span's full Tier-1/Tier-2 state via the Supabase RPC.
const resolveContexts = (paragraphIds: number[]) =>
  request<GenerationContext[]>('/rest/v1/rpc/resolve_contexts', {
    method: 'POST',
    body: JSON.stringify({ p_paragraph_ids: paragraphIds }),
  })

export interface IngestJob {
  ingest_job_id: string
  status: 'queued' | 'running' | 'done' | 'failed'
  stage: string
  completed: number
  total: number
  progress: number
  eta_seconds: number | null
  book_id: number | null
  error: string | null
}

interface BookRow {
  book_id: number
  title: string
  author: string | null
  ingestion_status: string
  paragraphs: { count: number }[]
}

export const api = {
  // Book + page data straight from Supabase REST (paragraph count via embedded count).
  listBooks: async (): Promise<BookSummary[]> => {
    const rows = await request<BookRow[]>(
      '/rest/v1/books?select=book_id,title,author,ingestion_status,paragraphs(count)&order=book_id.asc',
    )
    return rows.map((r) => ({
      book_id: r.book_id,
      title: r.title,
      author: r.author,
      ingestion_status: r.ingestion_status,
      paragraph_count: r.paragraphs?.[0]?.count ?? 0,
    }))
  },

  // Upload a .txt/.pdf to the VM and kick off Claude ingestion -> Supabase.
  ingestBook: async (file: File, title: string, author: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('title', title)
    form.append('author', author)
    const res = await fetch(`${VM_BASE_URL || API_BASE_URL}/ingest`, { method: 'POST', body: form })
    if (!res.ok) throw new Error(`Ingest failed (${res.status}): ${await res.text()}`)
    return res.json() as Promise<{ ingest_job_id: string }>
  },

  getIngestJob: (id: string) => request<IngestJob>(`/ingest/${id}`),

  listParagraphs: (bookId: number) =>
    request<Paragraph[]>(
      `/rest/v1/paragraphs?select=paragraph_id,sequence_index,chapter_number,raw_text&book_id=eq.${bookId}&order=sequence_index.asc`,
    ),

  // "Query" — resolve story state directly from Supabase.
  queryContext: resolveContexts,

  // "Generate" — resolve contexts (Supabase) then hand them to the VM to plan
  // shots + render. The VM owns planning now (no FastAPI middle tier).
  generateVideo: async (paragraphIds: number[]) => {
    const contexts = await resolveContexts(paragraphIds)
    return request<GenerateVideoResponse>('/generate', {
      method: 'POST',
      body: JSON.stringify({ contexts }),
    })
  },

  // Poll the VM render job; absolutize the VM-relative URLs it returns.
  getVideoJob: async (jobId: string): Promise<VideoJob> => {
    const job = await request<VideoJob>(`/jobs/${jobId}`)
    const abs = (u: string | null) => (u ? `${VM_BASE_URL || API_BASE_URL}${u}` : null)
    return { ...job, video_url: abs(job.video_url), stream_url: abs(job.stream_url) }
  },
}

/** Build the live MJPEG stream URL (VM) for a render job. */
export function videoStreamUrl(jobId: string): string {
  return `${VM_BASE_URL || API_BASE_URL}/jobs/${jobId}/stream`
}
