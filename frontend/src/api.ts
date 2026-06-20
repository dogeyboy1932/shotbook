// Thin fetch wrapper around the book_video_gen FastAPI backend (app/main.py).
// Defaults to relative paths, which Vite's dev server proxies to the
// backend (see vite.config.ts) -- this is what makes "frontend + backend
// both running on a remote box, only this dev server's port tunneled
// back" work with zero config. Set VITE_API_BASE_URL only if the backend
// is reachable on a different origin than this dev server.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

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
  narrative_context: string
}

export interface VideoShot {
  shot_id: string
  camera: string
  action: string
  light: string
  continuity: 'continuous_frame' | 'cut_same_scene' | 'cut_new_scene'
  prompt: string
}

export interface VideoWorld {
  characters: Record<string, string>
  location: string | null
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(`${init?.method ?? 'GET'} ${path} failed (${response.status}): ${body}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  listBooks: () => request<BookSummary[]>('/api/books'),

  listParagraphs: (bookId: number) =>
    request<Paragraph[]>(`/api/books/${bookId}/paragraphs`),

  queryContext: (paragraphIds: number[]) =>
    request<GenerationContext[]>('/api/generate-context/batch', {
      method: 'POST',
      body: JSON.stringify({ paragraph_ids: paragraphIds }),
    }),

  composeScene: (paragraphIds: number[]) =>
    request<ComposedScene>('/api/compose-scene', {
      method: 'POST',
      body: JSON.stringify({ paragraph_ids: paragraphIds }),
    }),
}
