import { type MouseEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type BookSummary, type IngestJob } from '../api'

function formatEta(seconds: number | null): string {
  if (seconds == null) return 'estimating…'
  if (seconds <= 0) return 'almost done'
  if (seconds < 60) return `~${seconds}s remaining`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `~${m}m ${s}s remaining`
}

export default function Library() {
  const [books, setBooks] = useState<BookSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  // Add-story modal state.
  const [showAdd, setShowAdd] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [author, setAuthor] = useState('')
  const [job, setJob] = useState<IngestJob | null>(null)
  const [addError, setAddError] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const refreshBooks = useCallback(() => {
    return api.listBooks().then(setBooks).catch((err) => setError(String(err)))
  }, [])

  useEffect(() => {
    refreshBooks().finally(() => setLoading(false))
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current)
    }
  }, [refreshBooks])

  const stats = useMemo(() => {
    const totalParagraphs = books.reduce((sum, book) => sum + book.paragraph_count, 0)
    const readyCount = books.filter((book) => book.ingestion_status === 'beats_pass_complete').length
    return { totalParagraphs, readyCount }
  }, [books])

  const resetAdd = useCallback(() => {
    if (pollRef.current) clearTimeout(pollRef.current)
    setShowAdd(false)
    setFile(null)
    setTitle('')
    setAuthor('')
    setJob(null)
    setAddError(null)
  }, [])

  const onPickFile = useCallback((f: File | null) => {
    setFile(f)
    setAddError(null)
    if (f && !title) {
      // Default the title from the filename ("poe-the-cask.txt" -> "poe the cask").
      const base = f.name.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').trim()
      setTitle(base.replace(/\b\w/g, (c) => c.toUpperCase()))
    }
  }, [title])

  const pollJob = useCallback((id: string) => {
    const tick = async () => {
      try {
        const j = await api.getIngestJob(id)
        setJob(j)
        if (j.status === 'done') {
          await refreshBooks()
          setTimeout(resetAdd, 900) // brief "done" flash, then close
        } else if (j.status === 'failed') {
          setAddError(j.error || 'Ingestion failed')
        } else {
          pollRef.current = setTimeout(tick, 1500)
        }
      } catch (err) {
        setAddError(String(err))
      }
    }
    pollRef.current = setTimeout(tick, 1000)
  }, [refreshBooks, resetAdd])

  const startIngest = useCallback(async () => {
    if (!file || !title.trim()) {
      setAddError('Pick a .txt or .pdf file and enter a title.')
      return
    }
    setAddError(null)
    setJob({ ingest_job_id: '', status: 'queued', stage: 'Uploading', completed: 0, total: 0, progress: 0, eta_seconds: null, book_id: null, error: null })
    try {
      const { ingest_job_id } = await api.ingestBook(file, title.trim(), author.trim())
      pollJob(ingest_job_id)
    } catch (err) {
      setJob(null)
      setAddError(String(err))
    }
  }, [file, title, author, pollJob])

  const deleteBook = useCallback(async (book: BookSummary, e: MouseEvent) => {
    e.stopPropagation()
    if (!window.confirm(`Delete "${book.title}" and all its generated state? This cannot be undone.`)) return
    setDeletingId(book.book_id)
    try {
      await api.deleteBook(book.book_id)
      setBooks((prev) => prev.filter((b) => b.book_id !== book.book_id))
    } catch (err) {
      setError(String(err))
    } finally {
      setDeletingId(null)
    }
  }, [])

  const ingesting = job != null && job.status !== 'failed'
  const pct = job ? Math.round(job.progress * 100) : 0

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-10 lg:px-8">
      <div className="rounded-3xl border border-white/10 bg-slate-900/80 p-6 shadow-2xl shadow-black/30 backdrop-blur lg:p-8">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <p className="text-sm font-semibold uppercase tracking-[0.3em] text-amber-300/85">ShotBook Studio</p>
            <h1 className="mt-3 text-4xl font-semibold tracking-tight text-white sm:text-5xl">
              Turn highlighted passages into cinematic clips.
            </h1>
            <p className="mt-4 text-lg leading-8 text-slate-400">
              Add a story, open it, select a passage, and inspect the resolved state before generating a seamless preview.
            </p>
            <button
              onClick={() => setShowAdd(true)}
              className="mt-6 inline-flex items-center gap-2 rounded-xl bg-amber-400 px-4 py-2 text-sm font-semibold text-slate-900 transition hover:bg-amber-300"
            >
              <span className="text-lg leading-none">+</span> Add story (.txt / .pdf)
            </button>
          </div>

          <div className="grid gap-3 rounded-2xl border border-white/10 bg-slate-800/70 p-4 text-sm text-slate-300 sm:grid-cols-2">
            <div>
              <p className="text-slate-500">Books ready</p>
              <p className="mt-1 text-2xl font-semibold text-white">{stats.readyCount}</p>
            </div>
            <div>
              <p className="text-slate-500">Paragraphs indexed</p>
              <p className="mt-1 text-2xl font-semibold text-white">{stats.totalParagraphs}</p>
            </div>
          </div>
        </div>
      </div>

      <div className="mt-8 flex-1">
        {loading && <p className="rounded-2xl border border-white/10 bg-slate-900/70 px-4 py-5 text-slate-400">Loading your library…</p>}
        {error && (
          <p className="rounded-2xl border border-red-500/40 bg-red-950/40 px-4 py-5 text-red-300">Failed to load books: {error}</p>
        )}

        {!loading && !error && books.length === 0 && (
          <div className="rounded-2xl border border-dashed border-white/10 bg-slate-900/70 p-8 text-slate-400">
            No books yet. Press <span className="font-semibold text-amber-300">Add story</span> to upload a .txt or .pdf and ingest it.
          </div>
        )}

        {!loading && !error && books.length > 0 && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {books.map((book) => {
              const ready = book.ingestion_status === 'beats_pass_complete'
              const deleting = deletingId === book.book_id
              return (
                <div
                  key={book.book_id}
                  onClick={() => ready && !deleting && navigate(`/books/${book.book_id}`)}
                  className={`group relative flex flex-col items-start gap-3 rounded-2xl border border-white/10 bg-slate-900/70 p-5 text-left transition duration-200 ${
                    ready && !deleting
                      ? 'cursor-pointer hover:-translate-y-1 hover:border-amber-400/50 hover:bg-slate-800/90'
                      : 'cursor-not-allowed opacity-60'
                  }`}
                >
                  <div className="flex w-full items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-medium text-amber-300">{book.title}</p>
                      <p className="mt-1 text-sm text-slate-400">{book.author ?? 'Unknown author'}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.2em] text-amber-300">
                        {ready ? 'Open' : 'Ingesting'}
                      </span>
                      <button
                        onClick={(e) => deleteBook(book, e)}
                        disabled={deleting}
                        title="Delete story"
                        className="rounded-full border border-red-500/30 bg-red-500/10 px-2 py-1 text-[11px] font-medium text-red-300 transition hover:bg-red-500/20 disabled:opacity-50"
                      >
                        {deleting ? '…' : '🗑'}
                      </button>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2 text-xs text-slate-500">
                    <span className="rounded-full bg-slate-800 px-2.5 py-1">{book.paragraph_count} paragraphs</span>
                    <span className="rounded-full bg-slate-800 px-2.5 py-1">{book.ingestion_status}</span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {showAdd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={() => !ingesting && resetAdd()}>
          <div className="w-full max-w-md rounded-2xl border border-white/10 bg-slate-900 p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-white">Add a story</h2>
              {!ingesting && (
                <button onClick={resetAdd} className="text-slate-400 hover:text-slate-200">✕</button>
              )}
            </div>

            {!job && (
              <div className="space-y-4">
                <label className="block">
                  <span className="text-sm text-slate-400">File (.txt or .pdf)</span>
                  <input
                    type="file"
                    accept=".txt,.pdf,text/plain,application/pdf"
                    onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
                    className="mt-1 block w-full text-sm text-slate-300 file:mr-3 file:rounded-lg file:border-0 file:bg-amber-400 file:px-3 file:py-1.5 file:text-sm file:font-semibold file:text-slate-900 hover:file:bg-amber-300"
                  />
                </label>
                <label className="block">
                  <span className="text-sm text-slate-400">Title</span>
                  <input
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="The Cask of Amontillado"
                    className="mt-1 w-full rounded-lg border border-white/10 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-amber-400/60"
                  />
                </label>
                <label className="block">
                  <span className="text-sm text-slate-400">Author (optional)</span>
                  <input
                    value={author}
                    onChange={(e) => setAuthor(e.target.value)}
                    placeholder="Edgar Allan Poe"
                    className="mt-1 w-full rounded-lg border border-white/10 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-amber-400/60"
                  />
                </label>
                <button
                  onClick={startIngest}
                  className="w-full rounded-xl bg-amber-400 px-4 py-2 text-sm font-semibold text-slate-900 transition hover:bg-amber-300"
                >
                  Ingest story
                </button>
              </div>
            )}

            {job && (
              <div className="space-y-3">
                <div className="flex items-center justify-between text-sm">
                  <span className={job.status === 'done' ? 'font-semibold text-emerald-300' : 'font-semibold text-amber-300'}>
                    {job.status === 'done' ? 'Done — added to library' : job.stage}
                  </span>
                  <span className="text-slate-400">{pct}%</span>
                </div>
                <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-800">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${job.status === 'done' ? 'bg-emerald-400' : 'bg-amber-400'}`}
                    style={{ width: `${Math.max(4, pct)}%` }}
                  />
                </div>
                <div className="flex items-center justify-between text-xs text-slate-500">
                  <span>{job.total > 0 ? `step ${job.completed} / ${job.total}` : 'starting…'}</span>
                  <span>{job.status === 'done' ? '' : formatEta(job.eta_seconds)}</span>
                </div>
                <p className="text-xs text-slate-500">
                  Chunking the text and deriving characters, locations, and scene state with Claude — this runs once per story.
                </p>
              </div>
            )}

            {addError && <p className="mt-4 rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{addError}</p>}
          </div>
        </div>
      )}
    </div>
  )
}
