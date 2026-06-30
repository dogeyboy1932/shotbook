import { useCallback, useEffect, useState } from 'react'

export type RenderMode = 'realtime' | 'finished'

export interface UserPreferences {
  // 'realtime'  -> frames stream in live as the GPU produces them (and can be steered)
  // 'finished'  -> only the completed mp4 is shown; the render still runs, frames are hidden
  renderMode: RenderMode
}

const PREFS_KEY = 'shotbook:user-prefs'
const DEFAULT_PREFS: UserPreferences = { renderMode: 'realtime' }

function load(): UserPreferences {
  try {
    const raw = localStorage.getItem(PREFS_KEY)
    if (raw) return { ...DEFAULT_PREFS, ...(JSON.parse(raw) as Partial<UserPreferences>) }
  } catch { /* ignore corrupt/unavailable storage */ }
  return DEFAULT_PREFS
}

/** Global (cross-book) user preferences, persisted in localStorage. */
export function useUserPreferences() {
  const [prefs, setPrefs] = useState<UserPreferences>(load)

  useEffect(() => {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs))
    } catch { /* ignore quota */ }
  }, [prefs])

  const setRenderMode = useCallback((renderMode: RenderMode) => {
    setPrefs((p) => ({ ...p, renderMode }))
  }, [])

  return { prefs, setRenderMode }
}
