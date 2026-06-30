import { useEffect, useRef, useState } from 'react'

interface LiveVideoPlayerProps {
  /** MJPEG multipart stream — frames appear as the GPU produces them. */
  streamUrl: string | null
  /** Finished mp4 once the render completes. */
  videoUrl: string | null
  planning: boolean
  status: string | null
  error: string | null
  /** 'realtime' shows live frames; 'finished' hides them (the render still runs,
   *  driven by the same stream connection) and reveals only the completed mp4. */
  mode?: 'realtime' | 'finished'
}

export default function LiveVideoPlayer({
  streamUrl,
  videoUrl,
  planning,
  status,
  error,
  mode = 'realtime',
}: LiveVideoPlayerProps) {
  const [streamActive, setStreamActive] = useState(false)
  const [frameCount, setFrameCount] = useState(0)
  const imgRef = useRef<HTMLImageElement>(null)
  const prevSrc = useRef<string | null>(null)

  useEffect(() => {
    const img = imgRef.current
    if (!img || !streamUrl) return

    const onLoad = () => {
      setStreamActive(true)
      setFrameCount((n) => n + 1)
    }
    const onError = () => setStreamActive(false)

    img.addEventListener('load', onLoad)
    img.addEventListener('error', onError)
    return () => {
      img.removeEventListener('load', onLoad)
      img.removeEventListener('error', onError)
    }
  }, [streamUrl])

  useEffect(() => {
    if (streamUrl && streamUrl !== prevSrc.current) {
      prevSrc.current = streamUrl
      setFrameCount(0)
      setStreamActive(false)
    }
  }, [streamUrl])

  if (error) {
    return (
      <div className="rounded-2xl border border-red-500/40 bg-red-950/40 p-3 text-sm">
        <p className="font-semibold text-red-300">Render failed</p>
        <p className="mt-1 text-red-200/80">{error}</p>
      </div>
    )
  }

  if (planning) {
    return (
      <div className="rounded-2xl border border-amber-400/30 bg-amber-400/10 p-4 text-sm">
        <div className="flex items-center gap-3">
          <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
          <div>
            <p className="font-semibold text-amber-300">Planning shots…</p>
            <p className="mt-0.5 text-xs text-slate-400">
              Resolving story state and structuring the passage into a cinematic shot plan.
            </p>
          </div>
        </div>
      </div>
    )
  }

  if (!streamUrl && !videoUrl) {
    return (
      <div className="rounded-2xl border border-slate-700/60 bg-slate-900/40 p-3 text-sm text-slate-400">
        <p className="font-semibold text-slate-300">No GPU connected</p>
      </div>
    )
  }

  const showStream = streamUrl && !videoUrl
  const isRendering = showStream && (streamActive || status === 'running' || status === 'planned')
  // 'finished' mode: the stream still runs (it drives the GPU render), but we
  // keep the frames invisible and reveal only the completed mp4.
  const hideFrames = mode === 'finished' && Boolean(showStream)

  return (
    <div className="space-y-2 rounded-2xl border border-emerald-400/30 bg-emerald-400/10 p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <p className="font-semibold text-emerald-300">
          {videoUrl ? 'Generated video' : hideFrames ? 'Rendering video…' : isRendering ? 'Live preview' : 'Starting render…'}
        </p>
        {isRendering && streamActive && (
          <span className="flex items-center gap-1.5 text-xs text-emerald-400/90">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
            </span>
            {frameCount > 0 ? `${frameCount} frames${hideFrames ? ' rendered' : ''}` : 'Connecting…'}
          </span>
        )}
      </div>

      <div className="relative aspect-[832/480] overflow-hidden rounded-xl border border-slate-700 bg-black">
        {showStream && (
          // Kept mounted even when hidden so the MJPEG connection stays alive and
          // keeps driving the render in 'finished' mode.
          <img
            ref={imgRef}
            src={streamUrl}
            alt="Live video generation"
            className={`h-full w-full object-contain ${hideFrames ? 'opacity-0' : ''}`}
          />
        )}
        {videoUrl && (
          <video src={videoUrl} controls autoPlay loop className="h-full w-full object-contain" />
        )}
        {showStream && !streamActive && !hideFrames && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/60">
            <span className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-emerald-400 border-t-transparent" />
          </div>
        )}
        {hideFrames && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/80 text-slate-300">
            <span className="inline-block h-7 w-7 animate-spin rounded-full border-2 border-emerald-400 border-t-transparent" />
            <p className="text-xs">Rendering — your video will appear here when it's done.</p>
          </div>
        )}
      </div>

      {isRendering && !hideFrames && (
        <p className="text-xs text-slate-400">
          Frames stream in real time as the GPU generates them — an uninterrupted rollout rather than waiting for the full clip.
        </p>
      )}
    </div>
  )
}
