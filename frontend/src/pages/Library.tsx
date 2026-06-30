import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type BookSummary } from '../api'

export default function Library() {
  const [books, setBooks] = useState<BookSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    api
      .listBooks()
      .then(setBooks)
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false))
  }, [])

  const stats = useMemo(() => {
    const totalParagraphs = books.reduce((sum, book) => sum + book.paragraph_count, 0)
    const readyCount = books.filter((book) => book.ingestion_status === 'beats_pass_complete').length
    return { totalParagraphs, readyCount }
  }, [books])

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-10 lg:px-8">
      <div className="rounded-3xl border border-white/10 bg-slate-900/80 p-6 shadow-2xl shadow-black/30 backdrop-blur lg:p-8">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <p className="text-sm font-semibold uppercase tracking-[0.3em] text-amber-300/85">
              ShotBook Studio
            </p>
            <h1 className="mt-3 text-4xl font-semibold tracking-tight text-white sm:text-5xl">
              Turn highlighted passages into cinematic clips.
            </h1>
            <p className="mt-4 text-lg leading-8 text-slate-400">
              Open a book, select a passage, and inspect the resolved story state before generating a seamless preview.
            </p>
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
          <p className="rounded-2xl border border-red-500/40 bg-red-950/40 px-4 py-5 text-red-300">
            Failed to load books: {error}
          </p>
        )}

        {!loading && !error && books.length === 0 && (
          <div className="rounded-2xl border border-dashed border-white/10 bg-slate-900/70 p-8 text-slate-400">
            No books have been ingested yet. Run the ingestion orchestrator first to populate this library.
          </div>
        )}

        {!loading && !error && books.length > 0 && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {books.map((book) => (
              <button
                key={book.book_id}
                onClick={() => navigate(`/books/${book.book_id}`)}
                className="group flex flex-col items-start gap-3 rounded-2xl border border-white/10 bg-slate-900/70 p-5 text-left transition duration-200 hover:-translate-y-1 hover:border-amber-400/50 hover:bg-slate-800/90"
              >
                <div className="flex w-full items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-amber-300">{book.title}</p>
                    <p className="mt-1 text-sm text-slate-400">{book.author ?? 'Unknown author'}</p>
                  </div>
                  <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.2em] text-amber-300">
                    Open
                  </span>
                </div>

                <div className="flex flex-wrap gap-2 text-xs text-slate-500">
                  <span className="rounded-full bg-slate-800 px-2.5 py-1">
                    {book.paragraph_count} paragraphs
                  </span>
                  <span className="rounded-full bg-slate-800 px-2.5 py-1">
                    {book.ingestion_status}
                  </span>
                </div>

                <p className="text-sm leading-6 text-slate-400">
                  Highlight a passage to inspect its resolved state and generate a seamless video preview.
                </p>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
