import type { ComposedScene, GenerationContext } from '../api'
import LiveVideoPlayer from './LiveVideoPlayer'

interface ContextPanelProps {
  contexts: GenerationContext[]
  composedScene: ComposedScene | null
  loading: boolean
  error: string | null
  generating: boolean
  streamUrl: string | null
  videoUrl: string | null
  videoStatus: string | null
  renderError: string | null
  hasSelection: boolean
}

export default function ContextPanel({
  contexts,
  composedScene,
  loading,
  error,
  generating,
  streamUrl,
  videoUrl,
  videoStatus,
  renderError,
  hasSelection,
}: ContextPanelProps) {
  const showVideo = generating || streamUrl || videoUrl || renderError
  const hasContextData = contexts.length > 0 || Boolean(composedScene) || showVideo

  if (loading) {
    return <p className="rounded-2xl border border-white/10 bg-slate-800/60 px-4 py-3 text-sm text-slate-400">Querying state for the selected text…</p>
  }

  if (error && contexts.length === 0 && !composedScene) {
    return (
      <p className="rounded-2xl border border-red-500/40 bg-red-950/40 px-4 py-3 text-sm text-red-300">
        Query failed: {error}
      </p>
    )
  }

  if (!hasSelection) {
    return (
      <p className="rounded-2xl border border-white/10 bg-slate-800/60 px-4 py-3 text-sm text-slate-400">
        Select a passage in the reader to inspect the resolved story state or generate a visual preview.
      </p>
    )
  }

  if (!hasContextData) {
    return (
      <div className="space-y-4">
        <div className="rounded-2xl border border-white/10 bg-slate-800/60 p-3 text-sm text-slate-400">
          Your selection is ready. Generate to plan shots and stream a preview, or query first to inspect the story state.
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-white/10 bg-slate-800/60 p-3">
        <div className="mb-3 flex items-center justify-between">
          <p className="text-sm font-semibold text-slate-100">
            Query output ({contexts.length} paragraph{contexts.length > 1 ? 's' : ''})
          </p>
          <span className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2.5 py-1 text-[11px] uppercase tracking-[0.2em] text-emerald-300">
            grounded
          </span>
        </div>

        <div className="space-y-2 rounded-xl border border-white/10 bg-slate-950/70 p-2">
          {contexts.length > 0 ? (
            contexts.map((ctx) => (
              <details open key={ctx.paragraph_id} className="group rounded-xl border border-white/10 bg-slate-900/70 p-3">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-medium text-slate-200">
                  <span>Paragraph {ctx.paragraph_id}</span>
                  <span className="text-xs uppercase tracking-[0.2em] text-slate-500">Open</span>
                </summary>
                <div className="mt-3 space-y-2 border-t border-white/10 pt-3 text-sm text-slate-300">
                  <p>
                    <span className="font-medium text-slate-200">Sequence:</span> {ctx.sequence_index}
                  </p>
                  <p>
                    <span className="font-medium text-slate-200">Location:</span>{' '}
                    {ctx.location ? ctx.location.name : 'none'}
                  </p>
                  <p>
                    <span className="font-medium text-slate-200">Characters:</span>{' '}
                    {ctx.characters.length > 0 ? ctx.characters.map((c) => c.name).join(', ') : 'none'}
                  </p>
                  <p>
                    <span className="font-medium text-slate-200">Action:</span> {ctx.action_summary}
                  </p>
                </div>
              </details>
            ))
          ) : (
            <div className="rounded-xl border border-dashed border-white/10 bg-slate-900/50 p-4 text-sm text-slate-400">
              The selection is ready for handoff. Once you generate, the passage excerpt, shot plan, and audio prompt will appear here.
            </div>
          )}
        </div>
      </div>

      {showVideo && (
        <div className="rounded-2xl border border-white/10 bg-slate-800/60 p-3">
          <LiveVideoPlayer
            streamUrl={streamUrl}
            videoUrl={videoUrl}
            planning={generating}
            status={videoStatus}
            error={renderError}
          />
        </div>
      )}

      {composedScene && (
        <div className="space-y-3 rounded-2xl border border-amber-400/20 bg-amber-400/10 p-3 text-sm">
          <div className="flex items-center justify-between gap-3">
            <p className="font-semibold text-amber-200">
              Handoff preview
            </p>
            <span className="rounded-full bg-slate-900/60 px-2.5 py-1 text-[11px] uppercase tracking-[0.2em] text-amber-300">
              ready for GPU
            </span>
          </div>

          <div className="rounded-xl border border-amber-400/20 bg-slate-900/50 p-2 text-slate-300">
            <p className="text-xs uppercase tracking-[0.25em] text-amber-400/80">Preview</p>
            <p className="mt-2 text-sm leading-6 text-slate-300">
              {composedScene.video?.shots?.[0]
                ? `${composedScene.video.shots[0].camera} • ${composedScene.video.shots[0].action}`
                : composedScene.action_summary}
            </p>
            {composedScene.video && composedScene.video.shots.length > 1 && (
              <p className="mt-2 text-xs uppercase tracking-[0.2em] text-slate-500">
                {composedScene.video.shots.length} planned shots with continuity cues
              </p>
            )}
          </div>

          <details className="rounded-xl border border-amber-400/20 bg-slate-900/50 p-2 text-slate-300">
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-[0.25em] text-amber-400/80">
              Scene summary
            </summary>
            <div className="mt-2 space-y-2 text-sm leading-6">
              <p>
                <span className="font-medium text-slate-200">Selection:</span> {composedScene.selected_text}
              </p>
              <p>
                <span className="font-medium text-slate-200">Camera framing:</span> {composedScene.camera_framing}
              </p>
              <p>
                <span className="font-medium text-slate-200">Action:</span> {composedScene.action_summary}
              </p>
              {/* AUDIO PAUSED: audio prompt hidden — video quality + interactivity focus. */}
            </div>
          </details>

          {composedScene.video && (
            <>
              <details className="rounded-xl border border-amber-400/20 bg-slate-900/50 p-2">
                <summary className="cursor-pointer text-xs font-semibold uppercase tracking-[0.25em] text-amber-400/80">
                  World anchors
                </summary>
                <div className="mt-2 space-y-1 text-slate-300">
                  {Object.entries(composedScene.video.world.characters).map(([name, desc]) => (
                    <p key={name}>
                      <span className="font-medium text-slate-200">{name}:</span> {desc}
                      {composedScene.video!.world.character_status?.[name] && (
                        <span className="text-amber-300/90"> — status: {composedScene.video!.world.character_status[name]}</span>
                      )}
                    </p>
                  ))}
                  {composedScene.video.world.location && (
                    <p>
                      <span className="font-medium text-slate-200">location:</span>{' '}
                      {composedScene.video.world.location}
                      {composedScene.video.world.atmosphere && (
                        <span className="text-amber-300/90"> — atmosphere: {composedScene.video.world.atmosphere}</span>
                      )}
                    </p>
                  )}
                  <p>
                    <span className="font-medium text-slate-200">look:</span> {composedScene.video.world.look}
                  </p>
                </div>
              </details>

              <details className="rounded-xl border border-amber-400/20 bg-slate-900/50 p-2">
                <summary className="cursor-pointer text-xs font-semibold uppercase tracking-[0.25em] text-amber-400/80">
                  Shot plan
                </summary>
                <div className="mt-2 space-y-2">
                  {composedScene.video.shots.map((shot) => (
                    <div key={shot.shot_id} className="rounded-xl border border-amber-400/20 bg-slate-900/50 p-2">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-xs font-semibold uppercase tracking-[0.25em] text-amber-400/80">
                          {shot.shot_id}
                        </p>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.2em] ${
                            shot.continuity === 'continuous_frame'
                              ? 'bg-emerald-400/20 text-emerald-300'
                              : shot.continuity === 'cut_same_scene'
                                ? 'bg-sky-400/20 text-sky-300'
                                : 'bg-slate-700 text-slate-300'
                          }`}
                        >
                          {shot.continuity === 'continuous_frame'
                            ? 'continues from prev'
                            : shot.continuity === 'cut_same_scene'
                              ? 'cut, same scene'
                              : 'new scene'}
                        </span>
                      </div>
                      <p className="mt-2 text-slate-400">
                        <span className="font-medium text-slate-300">camera:</span> {shot.camera}
                      </p>
                      <p className="text-slate-400">
                        <span className="font-medium text-slate-300">action:</span> {shot.action}
                      </p>
                      <p className="text-slate-400">
                        <span className="font-medium text-slate-300">light:</span> {shot.light}
                      </p>
                      <p className="mt-2 text-slate-300">{shot.prompt}</p>
                    </div>
                  ))}
                </div>
              </details>
            </>
          )}
        </div>
      )}
    </div>
  )
}
