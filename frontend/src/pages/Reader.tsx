import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type ComposedScene, type GenerationContext, type Paragraph } from '../api'
import ContextPanel from '../components/ContextPanel'
import { clearHighlights, highlightRangeAcrossParagraphs } from '../lib/highlight'

// Paragraphs per page -- a stand-in for real pagination (which would be
// driven by rendered line height / viewport size). Fixed here purely to
// simulate page turns over a multi-page book.
const PARAGRAPHS_PER_PAGE = 4

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
  // The backend renders server-side; we poll the job and play the stitched mp4
  // (streamed from /api/video-jobs/{id}/video) once it's ready.
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const [videoStatus, setVideoStatus] = useState<string | null>(null)

  const [pageIndex, setPageIndex] = useState(0)

  const containerRef = useRef<HTMLDivElement>(null)
  const paragraphElsRef = useRef<Map<number, HTMLParagraphElement>>(new Map())

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
    if (containerRef.current) clearHighlights(containerRef.current)
    setSelectedParagraphIds([])
    setContexts([])
    setQueryError(null)
    setComposedScene(null)
    setVideoUrl(null)
    setVideoStatus(null)
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
    try {
      const result = await api.queryContext(selectedParagraphIds)
      setContexts(result)
    } catch (err) {
      setQueryError(String(err))
    } finally {
      setQueryLoading(false)
    }
  }, [selectedParagraphIds])

  const handleCompose = useCallback(async () => {
    if (selectedParagraphIds.length === 0) return
    setComposing(true)
    setVideoUrl(null)
    setVideoStatus(null)
    try {
      const { scene, job_id } = await api.generateVideo(selectedParagraphIds)
      setComposedScene(scene)
      setVideoStatus('rendering')
      // Poll the render job until the stitched mp4 is ready, then play it.
      const poll = async () => {
        try {
          const job = await api.getVideoJob(job_id)
          setVideoStatus(job.status)
          if (job.status === 'done' && job.video_url) {
            setVideoUrl(job.video_url)
          } else if (job.status === 'failed') {
            setQueryError(job.error || 'Video render failed')
          } else {
            setTimeout(poll, 3000)
          }
        } catch (err) {
          setQueryError(String(err))
        }
      }
      setTimeout(poll, 2000)
    } catch (err) {
      setQueryError(String(err))
    } finally {
      setComposing(false)
    }
  }, [selectedParagraphIds])

  return (
    <div className="mx-auto flex max-w-6xl gap-8 px-6 py-8">
      <div className="flex-1">
        <button
          onClick={() => navigate('/')}
          className="mb-4 text-sm text-slate-400 hover:text-slate-200"
        >
          &larr; Back to library
        </button>

        {loading && <p className="text-slate-400">Loading text...</p>}
        {loadError && (
          <p className="rounded-lg bg-red-950 px-4 py-3 text-red-300">
            Failed to load paragraphs: {loadError}
          </p>
        )}

        {!loading && !loadError && (
          <>
            <div
              ref={containerRef}
              onMouseDown={handleMouseDown}
              onMouseUp={handleMouseUp}
              className="h-[70vh] overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 p-8 leading-relaxed text-slate-200 select-text"
            >
              {currentPageParagraphs.map((paragraph) => (
                <p
                  key={paragraph.paragraph_id}
                  ref={(el) => {
                    if (el) paragraphElsRef.current.set(paragraph.paragraph_id, el)
                    else paragraphElsRef.current.delete(paragraph.paragraph_id)
                  }}
                  data-paragraph-id={paragraph.paragraph_id}
                  className="mb-4"
                >
                  {paragraph.raw_text}
                </p>
              ))}
            </div>

            <div className="mt-4 flex items-center justify-between">
              <button
                onClick={() => goToPage(pageIndex - 1)}
                disabled={pageIndex === 0}
                className="rounded-lg border border-slate-700 px-4 py-1.5 text-sm font-medium text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
              >
                &larr; Previous page
              </button>
              <span className="text-sm text-slate-500">
                Page {pageIndex + 1} of {totalPages}
              </span>
              <button
                onClick={() => goToPage(pageIndex + 1)}
                disabled={pageIndex === totalPages - 1}
                className="rounded-lg border border-slate-700 px-4 py-1.5 text-sm font-medium text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Next page &rarr;
              </button>
            </div>
          </>
        )}
      </div>

      <div className="w-96 shrink-0">
        <div className="sticky top-8 rounded-xl border border-slate-700 bg-slate-900 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-slate-100">Selection</h2>
            <button
              onClick={handleQuery}
              disabled={selectedParagraphIds.length === 0 || queryLoading}
              className="rounded-lg bg-slate-700 px-4 py-1.5 text-sm font-medium text-slate-100 transition hover:bg-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Query
            </button>
          </div>

          {selectedParagraphIds.length > 0 && (
            <p className="mb-4 text-xs text-slate-500">
              Spans paragraph_id(s): {selectedParagraphIds.join(', ')}
            </p>
          )}

          <ContextPanel
            contexts={contexts}
            composedScene={composedScene}
            loading={queryLoading}
            error={queryError}
            onCompose={handleCompose}
            composing={composing}
            videoUrl={videoUrl}
            videoStatus={videoStatus}
          />
        </div>
      </div>
    </div>
  )
}
