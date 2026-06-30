import { useState } from 'react'
import type { SavedClip } from '../api'

interface SavedClipsProps {
  clips: SavedClip[]
  onRemove: (id: string) => void
}

/**
 * Finished renders, each kept in its own red, labelled tab. A tab collapses to
 * just its labelled button; expanding it shows (and lets you download) the clip.
 * Every highlight -> Generate adds another tab.
 */
export default function SavedClips({ clips, onRemove }: SavedClipsProps) {
  // Track which tabs are collapsed; new clips default to expanded.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  if (clips.length === 0) return null

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  return (
    <div className="mt-4 space-y-2">
      <p className="text-sm font-semibold uppercase tracking-[0.3em] text-red-400/85">
        Saved clips ({clips.length})
      </p>
      {clips.map((clip) => {
        const isCollapsed = collapsed.has(clip.id)
        return (
          <div key={clip.id} className="overflow-hidden rounded-xl border border-red-500/40 bg-red-500/10">
            <div className="flex items-center gap-2 px-3 py-2">
              <button
                onClick={() => toggle(clip.id)}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
                title={isCollapsed ? 'Expand clip' : 'Collapse clip'}
              >
                <span className="text-red-300">{isCollapsed ? '▸' : '▾'}</span>
                <span className="truncate text-sm font-medium text-red-200">{clip.label}</span>
              </button>
              <a
                href={clip.videoUrl}
                download={`${clip.label.replace(/\s+/g, '_') || 'clip'}.mp4`}
                onClick={(e) => e.stopPropagation()}
                className="shrink-0 text-[11px] font-medium uppercase tracking-[0.15em] text-red-300/80 transition hover:text-red-200"
              >
                save
              </a>
              <button
                onClick={() => onRemove(clip.id)}
                title="Remove from list"
                className="shrink-0 text-red-300/70 transition hover:text-red-200"
              >
                ✕
              </button>
            </div>
            {!isCollapsed && (
              <div className="border-t border-red-500/30 bg-slate-950/40 p-2">
                <video src={clip.videoUrl} controls loop playsInline className="w-full rounded-lg" />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
