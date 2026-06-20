import type { ComposedScene, GenerationContext } from '../api'

interface ContextPanelProps {
  contexts: GenerationContext[]
  composedScene: ComposedScene | null
  loading: boolean
  error: string | null
  onCompose: () => void
  composing: boolean
}

export default function ContextPanel({
  contexts,
  composedScene,
  loading,
  error,
  onCompose,
  composing,
}: ContextPanelProps) {
  if (loading) {
    return <p className="text-sm text-slate-400">Querying state for the selected text...</p>
  }

  if (error) {
    return (
      <p className="rounded-lg bg-red-950 px-4 py-3 text-sm text-red-300">
        Query failed: {error}
      </p>
    )
  }

  if (contexts.length === 0) {
    return (
      <p className="text-sm text-slate-500">
        Highlight a passage in the reader, then press Query to resolve its story state here.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
          Resolved state ({contexts.length} paragraph{contexts.length > 1 ? 's' : ''})
        </h3>
        <button
          onClick={onCompose}
          disabled={composing}
          className="rounded-lg bg-amber-400 px-4 py-1.5 text-sm font-medium text-slate-900 transition hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {composing ? 'Planning shots...' : 'Submit'}
        </button>
      </div>

      <div className="max-h-[28rem] space-y-3 overflow-y-auto pr-1">
        {contexts.map((ctx) => (
          <div
            key={ctx.paragraph_id}
            className="rounded-lg border border-slate-700 bg-slate-800/60 p-3 text-sm"
          >
            <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
              <span>paragraph_id={ctx.paragraph_id}</span>
              <span>seq={ctx.sequence_index}</span>
            </div>
            <p className="text-slate-300">
              <span className="font-medium text-slate-200">Location: </span>
              {ctx.location ? ctx.location.name : 'none'}
            </p>
            <p className="text-slate-300">
              <span className="font-medium text-slate-200">Characters: </span>
              {ctx.characters.length > 0
                ? ctx.characters.map((c) => c.name).join(', ')
                : 'none'}
            </p>
            <p className="mt-1 text-slate-400">{ctx.action_summary}</p>
          </div>
        ))}
      </div>

      {composedScene?.video && (
        <div className="space-y-2 rounded-lg border border-amber-400/40 bg-amber-400/10 p-3 text-sm">
          <p className="font-medium text-amber-300">
            {composedScene.video.shots.length} video shot
            {composedScene.video.shots.length > 1 ? 's' : ''} planned (full payload logged to
            the browser console).
          </p>

          <details className="rounded border border-amber-400/20 bg-slate-900/40 p-2">
            <summary className="cursor-pointer text-xs font-mono uppercase tracking-wide text-amber-400/80">
              world
            </summary>
            <div className="mt-1 space-y-1 text-slate-300">
              {Object.entries(composedScene.video.world.characters).map(([name, desc]) => (
                <p key={name}>
                  <span className="font-medium text-slate-200">{name}: </span>
                  {desc}
                </p>
              ))}
              {composedScene.video.world.location && (
                <p>
                  <span className="font-medium text-slate-200">location: </span>
                  {composedScene.video.world.location}
                </p>
              )}
              <p>
                <span className="font-medium text-slate-200">look: </span>
                {composedScene.video.world.look}
              </p>
            </div>
          </details>

          {composedScene.video.shots.map((shot) => (
            <div
              key={shot.shot_id}
              className="rounded border border-amber-400/20 bg-slate-900/40 p-2"
            >
              <div className="flex items-center justify-between">
                <p className="text-xs font-mono uppercase tracking-wide text-amber-400/80">
                  {shot.shot_id}
                </p>
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
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
              <p className="mt-1 text-slate-400">
                <span className="font-medium text-slate-300">camera: </span>
                {shot.camera}
              </p>
              <p className="text-slate-400">
                <span className="font-medium text-slate-300">action: </span>
                {shot.action}
              </p>
              <p className="text-slate-400">
                <span className="font-medium text-slate-300">light: </span>
                {shot.light}
              </p>
              <p className="mt-1 text-slate-300">{shot.prompt}</p>
            </div>
          ))}

          <p className="pt-1 text-slate-400">
            <span className="font-medium text-slate-300">negative_prompt: </span>
            {composedScene.video.negative_prompt}
          </p>
          <p className="text-slate-400">
            <span className="font-medium text-slate-300">audio_prompt: </span>
            {composedScene.audio_prompt}
          </p>
        </div>
      )}
    </div>
  )
}
