import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, previewSceneFromContexts, videoStreamUrl, type ComposedScene, type GenerationContext, type Paragraph, type SavedClip } from '../api'
import ContextPanel from '../components/ContextPanel'
import SavedClips from '../components/SavedClips'
import { clearHighlights, highlightRangeAcrossParagraphs } from '../lib/highlight'

const PARAGRAPHS_PER_PAGE = 4

const clipsKey = (bookId: string) => `shotbook:clips:${bookId}`
const MAX_CLIPS = 10

/** A short, human label for a saved clip, from the scene's action/selection. */
function clipLabel(scene: ComposedScene | null): string {
  const src = (scene?.action_summary || scene?.selected_text || '').replace(/[“”"_*]/g, '')
  const words = src.split(/\s+/).filter(Boolean).slice(0, 4).join(' ')
  return words.length > 30 ? `${words.slice(0, 30).trimEnd()}…` : words
}

export default function Reader() {
  const { bookId } = useParams<{ bookId: string }>()
  const navigate = useNavigate()

  const [paragraphs, setParagraphs] = useState<Paragraph[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const [selectedParagraphIds, setSelectedParagraphIds] = useState<number[]>([])
  const [contexts, setContexts] = useState<GenerationContext[]>([])
  const [queryLoading, setQueryLoading] = useState(false)
  const [queryError, setQueryError] = useState<string | null>(null)

  const [composedScene, setComposedScene] = useState<ComposedScene | null>(null)
  const [composing, setComposing] = useState(false)
  const [streamUrl, setStreamUrl] = useState<string | null>(null)
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const [videoStatus, setVideoStatus] = useState<string | null>(null)
  const [renderError, setRenderError] = useState<string | null>(null)

  const [savedClips, setSavedClips] = useState<SavedClip[]>([])

  const [pageIndex, setPageIndex] = useState(0)

  const containerRef = useRef<HTMLDivElement>(null)
  const paragraphElsRef = useRef<Map<number, HTMLParagraphElement>>(new Map())
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!bookId) return
    setLoading(true)
    setPageIndex(0)
    api
      .listParagraphs(Number(bookId))
      .then(setParagraphs)
      .catch((err) => setLoadError(String(err)))
      .finally(() => setLoading(false))
  }, [bookId])

  // Saved clips persist in localStorage only for the duration of this visit:
  // they survive an accidental refresh, but leaving the reader (unmount or
  // switching books) wipes the store -- see the cleanup below.
  useEffect(() => {
    if (!bookId) {
      setSavedClips([])
      return
    }
    try {
      const raw = localStorage.getItem(clipsKey(bookId))
      setSavedClips(raw ? JSON.parse(raw) : [])
    } catch {
      setSavedClips([])
    }
    return () => {
      // Leaving this book's reader: empty its saved-clip store.
      try {
        localStorage.removeItem(clipsKey(bookId))
      } catch { /* ignore */ }
    }
  }, [bookId])

  const removeClip = useCallback((id: string) => {
    setSavedClips((prev) => {
      const next = prev.filter((c) => c.id !== id)
      if (bookId) {
        try {
          localStorage.setItem(clipsKey(bookId), JSON.stringify(next))
        } catch { /* ignore quota */ }
      }
      return next
    })
  }, [bookId])

  const pages = useMemo(() => {
    const chunks: Paragraph[][] = []
    for (let i = 0; i < paragraphs.length; i += PARAGRAPHS_PER_PAGE) {
      chunks.push(paragraphs.slice(i, i + PARAGRAPHS_PER_PAGE))
    }
    return chunks.length > 0 ? chunks : [[]]
  }, [paragraphs])

  const totalPages = pages.length
  const currentPageParagraphs = pages[pageIndex] ?? []

  const resetSelection = useCallback(() => {
    if (pollRef.current) clearTimeout(pollRef.current)
    if (containerRef.current) clearHighlights(containerRef.current)
    setSelectedParagraphIds([])
    setContexts([])
    setQueryError(null)
    setComposedScene(null)
    setStreamUrl(null)
    setVideoUrl(null)
    setVideoStatus(null)
    setRenderError(null)
  }, [])

  const goToPage = useCallback(
    (next: number) => {
      const clamped = Math.max(0, Math.min(totalPages - 1, next))
      if (clamped === pageIndex) return
      resetSelection()
      paragraphElsRef.current.clear()
      setPageIndex(clamped)
    },
    [pageIndex, totalPages, resetSelection],
  )

  const paragraphRefList = useMemo(
    () =>
      currentPageParagraphs.map((p) => ({
        id: p.paragraph_id,
        get el() {
          return paragraphElsRef.current.get(p.paragraph_id)!
        },
      })),
    [currentPageParagraphs],
  )

  const handleMouseDown = useCallback(() => {
    resetSelection()
  }, [resetSelection])

  const handleMouseUp = useCallback(() => {
    const selection = window.getSelection()
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) return
    if (!containerRef.current) return

    const range = selection.getRangeAt(0)
    if (!containerRef.current.contains(range.commonAncestorContainer)) return

    const elements = paragraphRefList
      .filter((p) => paragraphElsRef.current.has(p.id))
      .map((p) => ({ id: p.id, el: paragraphElsRef.current.get(p.id)! }))

    const matchedIds = highlightRangeAcrossParagraphs(range.cloneRange(), elements)
    selection.removeAllRanges()

    if (matchedIds.length > 0) {
      setSelectedParagraphIds(matchedIds)
    }
  }, [paragraphRefList])

  const handleQuery = useCallback(async () => {
    if (selectedParagraphIds.length === 0) return
    setQueryLoading(true)
    setQueryError(null)
    setComposedScene(null)
    setStreamUrl(null)
    setVideoUrl(null)
    try {
      const result = await api.queryContext(selectedParagraphIds)
      setContexts(result)
    } catch (err) {
      setQueryError(String(err))
    } finally {
      setQueryLoading(false)
    }
  }, [selectedParagraphIds])

  const startJobPoll = useCallback((jobId: string, label: string) => {
    const poll = async () => {
      try {
        const job = await api.getVideoJob(jobId)
        setVideoStatus(job.status)
        if (job.status === 'done' && job.video_url) {
          const url = job.video_url
          setVideoUrl(url)
          setStreamUrl(null)
          // Keep the finished clip in its own tab.
          setSavedClips((prev) => {
            if (prev.some((c) => c.id === jobId)) return prev
            const next: SavedClip[] = [
              { id: jobId, label: label || `Clip ${prev.length + 1}`, videoUrl: url, createdAt: Date.now() },
              ...prev,
            ]
            if (bookId) {
              try {
                localStorage.setItem(clipsKey(bookId), JSON.stringify(next))
              } catch { /* ignore quota */ }
            }
            return next
          })
        } else if (job.status === 'failed') {
          setRenderError(job.error || 'Video render failed')
          setStreamUrl(null)
        } else {
          pollRef.current = setTimeout(poll, 1500)
        }
      } catch (err) {
        setRenderError(String(err))
      }
    }
    pollRef.current = setTimeout(poll, 1500)
  }, [bookId])

  const handleGenerate = useCallback(async () => {
    if (selectedParagraphIds.length === 0) return
    if (savedClips.length >= MAX_CLIPS) {
      setQueryError(`Clip limit reached (${MAX_CLIPS}). Remove a saved clip to generate another.`)
      return
    }
    setComposing(true)
    setStreamUrl(null)
    setVideoUrl(null)
    setVideoStatus(null)
    setRenderError(null)
    try {
      // 1) Resolve world-state up front (fast Supabase RPC) and show the handoff
      //    preview IMMEDIATELY so the user sees what's being generated. Reuse the
      //    contexts already on screen if they queried first.
      const ctx = contexts.length > 0 ? contexts : await api.queryContext(selectedParagraphIds)
      if (contexts.length === 0) setContexts(ctx)
      setComposedScene(previewSceneFromContexts(ctx))

      // 2) Plan shots on the VM (Claude) + start the render; enrich the same
      //    preview in place when the real shot plan lands.
      const { scene, job_id } = await api.generateVideo(selectedParagraphIds, ctx)
      setComposedScene(scene)
      setStreamUrl(videoStreamUrl(job_id))
      setVideoStatus('planned')
      startJobPoll(job_id, clipLabel(scene))
    } catch (err) {
      setQueryError(String(err))
    } finally {
      setComposing(false)
    }
  }, [selectedParagraphIds, contexts, savedClips.length, startJobPoll])

  useEffect(() => {
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current)
    }
  }, [])

  return (
    <div className="mx-auto flex min-h-screen max-w-7xl flex-col gap-6 px-4 py-6 lg:grid lg:grid-cols-[1.15fr_0.85fr] lg:px-6 lg:py-8">
      <div className="min-w-0">
        <button
          onClick={() => navigate('/')}
          className="mb-4 text-sm font-medium text-slate-400 transition hover:text-amber-300"
        >
          ← Back to library
        </button>

        <div className="rounded-3xl border border-white/10 bg-slate-900/80 p-4 shadow-2xl shadow-black/20 backdrop-blur sm:p-6">
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.3em] text-amber-300/85">
                Reader workspace
              </p>
              <h2 className="mt-1 text-2xl font-semibold text-white">Highlight a passage</h2>
            </div>
            <div className="rounded-full border border-white/10 bg-slate-800/80 px-3 py-1.5 text-sm text-slate-400">
              Page {pageIndex + 1} of {totalPages}
            </div>
          </div>

          {loading && <p className="rounded-2xl border border-white/10 bg-slate-800/60 px-4 py-5 text-slate-400">Loading text…</p>}
          {loadError && (
            <p className="rounded-2xl border border-red-500/40 bg-red-950/40 px-4 py-5 text-red-300">
              Failed to load paragraphs: {loadError}
            </p>
          )}

          {!loading && !loadError && (
            <>
              <div
                ref={containerRef}
                onMouseDown={handleMouseDown}
                onMouseUp={handleMouseUp}
                className="h-[68vh] overflow-y-auto rounded-2xl border border-white/10 bg-slate-950/70 p-6 leading-8 text-slate-200 shadow-inner shadow-black/30 select-text"
              >
                {currentPageParagraphs.map((paragraph) => {
                  const isSelected = selectedParagraphIds.includes(paragraph.paragraph_id)
                  return (
                    <p
                      key={paragraph.paragraph_id}
                      ref={(el) => {
                        if (el) paragraphElsRef.current.set(paragraph.paragraph_id, el)
                        else paragraphElsRef.current.delete(paragraph.paragraph_id)
                      }}
                      data-paragraph-id={paragraph.paragraph_id}
                      className={`mb-4 rounded-xl px-3 py-2 transition ${
                        isSelected
                          ? 'bg-amber-400/15 text-white ring-1 ring-amber-400/30'
                          : 'bg-transparent text-slate-200'
                      }`}
                    >
                      {paragraph.raw_text}
                    </p>
                  )
                })}
              </div>

              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                <button
                  onClick={() => goToPage(pageIndex - 1)}
                  disabled={pageIndex === 0}
                  className="rounded-xl border border-white/10 bg-slate-800/70 px-4 py-2 text-sm font-medium text-slate-200 transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  ← Previous page
                </button>
                <div className="text-sm text-slate-500">
                  {selectedParagraphIds.length > 0
                    ? `${selectedParagraphIds.length} paragraph${selectedParagraphIds.length > 1 ? 's' : ''} selected`
                    : 'Select a passage to begin'}
                </div>
                <button
                  onClick={() => goToPage(pageIndex + 1)}
                  disabled={pageIndex === totalPages - 1}
                  className="rounded-xl border border-white/10 bg-slate-800/70 px-4 py-2 text-sm font-medium text-slate-200 transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Next page →
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="min-w-0 lg:sticky lg:top-4 lg:self-start">
        <div className="rounded-3xl border border-white/10 bg-slate-900/80 p-5 shadow-2xl shadow-black/20 backdrop-blur">
          <div className="mb-4 flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.3em] text-slate-400">Studio panel</p>
              <h3 className="mt-1 text-xl font-semibold text-white">Compose the scene</h3>
            </div>
            <button
              onClick={resetSelection}
              className="rounded-full border border-white/10 bg-slate-800/80 px-3 py-1.5 text-sm text-slate-300 transition hover:bg-slate-700"
            >
              Clear
            </button>
          </div>

          <div className="mb-5 rounded-2xl border border-amber-400/20 bg-amber-400/10 p-4 text-sm text-amber-100">
            <p className="font-medium">Guide</p>
            <p className="mt-1 text-amber-100/80">
              Select a passage in the reader, then inspect the resolved story state or generate an instant cinematic preview.
            </p>
          </div>

          <div className="mb-4 flex flex-wrap gap-2">
            <button
              onClick={handleQuery}
              disabled={selectedParagraphIds.length === 0 || queryLoading}
              className="rounded-xl bg-slate-700 px-3 py-2 text-sm font-medium text-slate-100 transition hover:bg-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {queryLoading ? 'Querying…' : 'Get query'}
            </button>
            <button
              onClick={handleGenerate}
              disabled={selectedParagraphIds.length === 0 || composing || (!!streamUrl && !videoUrl) || savedClips.length >= MAX_CLIPS}
              title={savedClips.length >= MAX_CLIPS ? `Clip limit reached (${MAX_CLIPS}) — remove a saved clip first` : 'Plan shots and stream a ~10s seamless clip in real time'}
              className="rounded-xl bg-amber-400 px-3 py-2 text-sm font-medium text-slate-900 transition hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {composing ? 'Planning…' : streamUrl && !videoUrl ? 'Rendering…' : 'Generate'}
            </button>
            <span className={`self-center text-xs ${savedClips.length >= MAX_CLIPS ? 'font-medium text-red-400' : 'text-slate-500'}`}>
              {savedClips.length}/{MAX_CLIPS} clips
            </span>
          </div>

          {selectedParagraphIds.length > 0 && (
            <p className="mb-4 text-xs uppercase tracking-[0.25em] text-slate-500">
              Paragraph IDs: {selectedParagraphIds.join(', ')}
            </p>
          )}

          <ContextPanel
            contexts={contexts}
            composedScene={composedScene}
            loading={queryLoading}
            error={queryError}
            generating={composing}
            streamUrl={streamUrl}
            videoUrl={videoUrl}
            videoStatus={videoStatus}
            renderError={renderError}
            hasSelection={selectedParagraphIds.length > 0}
          />

          <SavedClips clips={savedClips} onRemove={removeClip} />
        </div>
      </div>
    </div>
  )
}
