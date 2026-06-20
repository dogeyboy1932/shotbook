import { useEffect, useState } from 'react'
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

  return (
    <div className="mx-auto max-w-5xl px-6 py-12">
      <h1 className="text-3xl font-semibold text-slate-100">Your library</h1>
      <p className="mt-2 text-slate-400">Pick a text to start reading.</p>

      {loading && <p className="mt-8 text-slate-400">Loading...</p>}
      {error && (
        <p className="mt-8 rounded-lg bg-red-950 px-4 py-3 text-red-300">
          Failed to load books: {error}
        </p>
      )}

      {!loading && !error && books.length === 0 && (
        <p className="mt-8 text-slate-400">
          No books have been ingested yet. Run the ingestion orchestrator first.
        </p>
      )}

      <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 md:grid-cols-3">
        {books.map((book) => (
          <button
            key={book.book_id}
            onClick={() => navigate(`/books/${book.book_id}`)}
            className="flex flex-col items-start gap-2 rounded-xl border border-slate-700 bg-slate-800/60 p-5 text-left transition hover:border-amber-400 hover:bg-slate-800"
          >
            <span className="text-lg font-medium text-slate-100">{book.title}</span>
            <span className="text-sm text-slate-400">{book.author ?? 'Unknown author'}</span>
            <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
              <span className="rounded-full bg-slate-700 px-2 py-0.5">
                {book.paragraph_count} paragraphs
              </span>
              <span className="rounded-full bg-slate-700 px-2 py-0.5">
                {book.ingestion_status}
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
